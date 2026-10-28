"""9단계 품질 점검: 불변 조건·권리·보관기간·커버리지를 점검하고 위반은 검수 대기로 올린다. 결과는 job_run.detail 과 내보내기 JSON 에 남는다."""
import json
from pathlib import Path

from .. import config
from ..db import enqueue_review
from ..pipeline import Context, Stage
from ..timeutil import now_iso, today
from .common import CLAIM_TABLES, TABLE_PK


class QualityStage(Stage):
    name = "quality"
    custom = True

    def __init__(self, export_dir=None, inbox_path=None):
        self.export_dir = Path(export_dir or config.EXPORT_DIR)
        self.inbox_path = inbox_path

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        checks = {}

        def check(name, bad_rows, severity="error", enqueue=None):
            n = len(bad_rows)
            checks[name] = {"violations": n, "severity": severity, "pass": n == 0}
            if enqueue and n:
                for r in bad_rows[:50]:
                    enqueue_review(conn, "quality", enqueue[0], str(r[0]), enqueue[1], 80)

        # 불변 조건(스키마가 막지만 이중 확인)
        check("confirmed_without_external_source_or_evidence", q(
            """SELECT f.fact_id FROM bib_fact f LEFT JOIN source s ON s.source_id=f.source_id
               WHERE f.epistemic_status='confirmed_fact' AND (s.is_external IS NOT 1 OR f.evidence_id IS NULL)"""), enqueue=("bib_fact", "확정인데 외부 출처/근거 위치 없음"))
        check("interpretation_confirmed", q("SELECT interp_id FROM interpretation WHERE epistemic_status NOT IN ('source_claim','ai_inference','unverified')"))
        check("quote_over_12_chars", q("SELECT evidence_id FROM evidence_passage WHERE length(quote_snippet)>?", config.MAX_QUOTE_CHARS))
        check("claim_without_collected_at", [r for t in CLAIM_TABLES for r in q(f"SELECT {TABLE_PK[t]} FROM {t} WHERE collected_at IS NULL OR collected_at=''")])
        check("ai_promoted_without_reviewer", q("SELECT inference_id FROM ai_inference WHERE review_state='promoted' AND (reviewed_by IS NULL OR promoted_to_id IS NULL)"))
        check("ai_inference_rows_present", q("SELECT inference_id FROM ai_inference WHERE epistemic_status<>'ai_inference'"))
        # 문학 지식그래프
        check("work_theme_claim_without_evidence", q("SELECT wt_id FROM work_theme WHERE epistemic_status='source_claim' AND (evidence_id IS NULL OR source_id IS NULL)"))
        check("ai_theme_promoted", q("SELECT inference_id FROM ai_inference WHERE review_state='promoted' AND proposed_relation LIKE 'work_theme%'"))
        check("opposes_missing_side", q(
            """SELECT r.rel_id FROM interpretation_relation r LEFT JOIN interpretation a ON a.interp_id=r.a_interp_id LEFT JOIN interpretation b ON b.interp_id=r.b_interp_id
               WHERE r.relation='opposes' AND r.recorded_to IS NULL AND (a.interp_id IS NULL OR b.interp_id IS NULL OR a.evidence_id IS NULL OR b.evidence_id IS NULL
                 OR a.attributed_to_name='' OR b.attributed_to_name='')"""), enqueue=("interpretation_relation", "대립 해석 한쪽의 근거/작성자 없음"))
        check("critique_without_evidence", q("SELECT interp_id FROM interpretation WHERE recorded_to IS NULL AND kind IN ('critique','counter','interpretation') AND evidence_id IS NULL"), "warn")
        check("registry_missing_rights_or_retention", q("SELECT registry_key FROM source_registry WHERE rights_note='' OR (retention_days IS NULL AND support_level<>'unsupported')"), "warn")
        # 권리·정책
        check("claims_from_policy_blocked_sources", q(
            """SELECT DISTINCT s.source_id FROM source s JOIN source_registry r ON r.registry_key=s.registry_key
               WHERE r.support_level='unsupported' AND s.registry_key<>'internal_site_records'
                 AND EXISTS (SELECT 1 FROM bib_fact b WHERE b.source_id=s.source_id AND b.recorded_to IS NULL)"""), enqueue=("source", "수집 불가 출처에서 온 주장이 남아 있음"))
        check("retention_expired_sources", q("SELECT source_id FROM source WHERE retention_until IS NOT NULL AND retention_until < ?", today()), "warn",
              enqueue=("source", "보관기간 만료 — 수집함 원문 폐기 승인 필요(삭제는 승인 후)"))
        check("auto_publish_flag_off", [] if config.AUTO_PUBLISH_TO_SITE is False else [("AUTO_PUBLISH_TO_SITE",)])
        # 충돌·검수 적체
        check("open_conflicts_older_than_14d", q("SELECT conflict_id FROM conflict WHERE status='open' AND detected_at < ?", _days_ago(14)), "warn")
        # 정합성
        check("claims_missing_source_row", [r for t in CLAIM_TABLES if t != "edition" for r in q(
            f"SELECT {TABLE_PK[t]} FROM {t} WHERE source_id IS NOT NULL AND source_id NOT IN (SELECT source_id FROM source)")])
        check("dead_failed_queue", q("SELECT fq_id FROM failed_queue WHERE state='dead'"), "warn")
        # 커버리지(정보)
        cov = {
            "works": conn.execute("SELECT COUNT(*) FROM work").fetchone()[0],
            "works_without_any_confirmed_fact": conn.execute(
                "SELECT COUNT(*) FROM work w WHERE NOT EXISTS (SELECT 1 FROM bib_fact f WHERE f.subject_id=w.work_id AND f.epistemic_status='confirmed_fact')").fetchone()[0],
            "works_without_author": conn.execute("SELECT COUNT(*) FROM work w WHERE NOT EXISTS (SELECT 1 FROM work_author a WHERE a.work_id=w.work_id)").fetchone()[0],
            "claims_by_status": {t: {r[0]: r[1] for r in conn.execute(f"SELECT epistemic_status, COUNT(*) FROM {t} WHERE recorded_to IS NULL GROUP BY 1")} for t in CLAIM_TABLES},
            "open_review_items": conn.execute("SELECT COUNT(*) FROM review_queue WHERE state='open'").fetchone()[0],
        }
        failed = [k for k, v in checks.items() if not v["pass"] and v["severity"] == "error"]
        ctx.consume("rows", sum(c["violations"] for c in checks.values()) + 1)
        report = {"checked_at": now_iso(), "pass": not failed, "failed_errors": failed, "checks": checks, "coverage": cov}
        self.export_dir.mkdir(parents=True, exist_ok=True)
        (self.export_dir / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        ctx.processed += len(checks)
        conn.commit()
        return {"pass": report["pass"], "failed_errors": failed, "warnings": [k for k, v in checks.items() if not v["pass"] and v["severity"] == "warn"], "coverage": cov}


def _days_ago(n: int) -> str:
    import datetime as dt
    return (dt.datetime.now() - dt.timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S")
