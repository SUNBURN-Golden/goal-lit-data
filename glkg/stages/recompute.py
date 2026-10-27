"""8단계 결과 재계산: 입력 지문이 바뀐 out_* 표만 지우고 다시 만든다(파생 데이터, 언제든 재생성 가능).
사용자용 결과에는 주장의 확인 수준(epistemic_status)이 항상 같이 나가고, 요지 원문·인용은 포함하지 않는다."""
import json
from pathlib import Path
from typing import Dict, List, Tuple

from .. import config
from ..db import sha
from ..pipeline import Context, Stage
from ..timeutil import now_iso

CUR = "recorded_to IS NULL"

OUTPUTS: Dict[str, Tuple[List[str], List[str]]] = {
    # name: (input tables for fingerprint, [delete+insert statements])
    "out_work_edition_diff": (["edition", "bib_fact", "conflict", "source"], [
        """INSERT INTO out_work_edition_diff
           SELECT e.work_id, f.predicate, e.edition_id, f.value_text, f.epistemic_status, e.identity_status, f.source_url,
                  (SELECT COUNT(DISTINCT lower(replace(g.value_text,' ',''))) FROM bib_fact g JOIN edition e2 ON e2.edition_id=g.subject_id
                    WHERE g.subject_type='edition' AND e2.work_id=e.work_id AND g.predicate=f.predicate AND g.recorded_to IS NULL),
                  EXISTS(SELECT 1 FROM conflict c WHERE c.status='open' AND ((c.a_table='bib_fact' AND c.a_id=f.fact_id) OR (c.b_table='bib_fact' AND c.b_id=f.fact_id))),
                  (SELECT COUNT(DISTINCT g.subject_id) FROM bib_fact g JOIN edition e2 ON e2.edition_id=g.subject_id
                    WHERE g.subject_type='edition' AND e2.work_id=e.work_id AND g.predicate=f.predicate AND g.recorded_to IS NULL) >= 2
           FROM bib_fact f JOIN edition e ON e.edition_id=f.subject_id
           WHERE f.subject_type='edition' AND f.recorded_to IS NULL
           UNION ALL
           SELECT e.work_id, 'isbn', e.edition_id, e.isbn, e.epistemic_status, e.identity_status, e.source_url, 1, 0,
                  (SELECT COUNT(*) FROM edition e2 WHERE e2.work_id=e.work_id AND e2.isbn IS NOT NULL) >= 2
           FROM edition e WHERE e.isbn IS NOT NULL"""]),
    "out_theme_critique_view": (["interpretation", "theme", "interpretation_relation", "evidence_passage"], [
        """INSERT INTO out_theme_critique_view
           SELECT i.work_id, t.label, i.interp_id, i.kind, i.attributed_to_name, i.attribution_verified, i.epistemic_status, i.source_url,
                  ev.locator, i.collected_at,
                  (SELECT r.b_interp_id FROM interpretation_relation r WHERE r.a_interp_id=i.interp_id AND r.relation='opposes' AND r.recorded_to IS NULL AND r.epistemic_status='source_claim' LIMIT 1),
                  EXISTS(SELECT 1 FROM interpretation_relation r WHERE (r.a_interp_id=i.interp_id OR r.b_interp_id=i.interp_id) AND r.relation='opposes' AND r.recorded_to IS NULL AND r.epistemic_status='source_claim')
           FROM interpretation i LEFT JOIN theme t ON t.theme_id=i.theme_id LEFT JOIN evidence_passage ev ON ev.evidence_id=i.evidence_id
           WHERE i.recorded_to IS NULL AND i.kind IN ('critique','counter','interpretation','scholarly_ref')"""]),
    "out_author_statement_index": (["author_statement", "author", "evidence_passage"], [
        """INSERT INTO out_author_statement_index
           SELECT a.author_id, a.canonical_name, s.work_id, s.statement_id, s.statement_date, s.statement_medium, s.source_tier, s.epistemic_status,
                  s.source_url, ev.locator, s.collected_at
           FROM author_statement s JOIN author a ON a.author_id=s.author_id LEFT JOIN evidence_passage ev ON ev.evidence_id=s.evidence_id
           WHERE s.recorded_to IS NULL"""]),
    "out_work_fact_status": (["bib_fact", "conflict", "evidence_passage", "edition"], [
        """INSERT INTO out_work_fact_status
           SELECT COALESCE(CASE f.subject_type WHEN 'work' THEN f.subject_id ELSE (SELECT e.work_id FROM edition e WHERE e.edition_id=f.subject_id) END,''),
                  f.fact_id, f.subject_type, f.predicate, f.value_text, f.epistemic_status, f.source_url, ev.locator, ev.locator_quality, f.collected_at,
                  EXISTS(SELECT 1 FROM conflict c WHERE c.status='open' AND ((c.a_table='bib_fact' AND c.a_id=f.fact_id) OR (c.b_table='bib_fact' AND c.b_id=f.fact_id)))
           FROM bib_fact f LEFT JOIN evidence_passage ev ON ev.evidence_id=f.evidence_id WHERE f.recorded_to IS NULL"""]),
    "out_unverified_and_ai": (["bib_fact", "author_statement", "interpretation", "ai_inference"], [
        """INSERT INTO out_unverified_and_ai
           SELECT subject_id, 'unverified_fact', fact_id, value_text, predicate, NULL FROM bib_fact WHERE epistemic_status='unverified' AND subject_type='work' AND recorded_to IS NULL
           UNION ALL SELECT work_id, 'unverified_statement', statement_id, summary, statement_medium, NULL FROM author_statement WHERE epistemic_status='unverified' AND recorded_to IS NULL
           UNION ALL SELECT work_id, 'unverified_interpretation', interp_id, summary, kind, NULL FROM interpretation WHERE epistemic_status='unverified' AND recorded_to IS NULL
           UNION ALL SELECT NULL, 'ai_inference', inference_id, proposed_relation, subject_table, review_state FROM ai_inference"""]),
    "out_author_theme_view": (["work_author", "work_theme", "author", "theme"], [
        """INSERT INTO out_author_theme_view
           SELECT a.author_id, a.canonical_name, t.label, wa.work_id, wa.epistemic_status, wt.epistemic_status, wa.source_url, wt.source_url
           FROM work_author wa JOIN work_theme wt ON wt.work_id=wa.work_id JOIN author a ON a.author_id=wa.author_id JOIN theme t ON t.theme_id=wt.theme_id
           WHERE wa.recorded_to IS NULL AND wt.recorded_to IS NULL AND wa.source_id IS NOT NULL AND wt.source_id IS NOT NULL
             AND wa.epistemic_status IN ('confirmed_fact','source_claim') AND wt.epistemic_status='source_claim'"""]),
}


def fingerprint(conn, tables: List[str]) -> str:
    parts = []
    for t in tables:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        pk = next(r[1] for r in conn.execute(f"PRAGMA table_info({t})") if r[5] == 1)
        extra = "||coalesce(epistemic_status,'')" if "epistemic_status" in cols else ""
        extra += "||coalesce(recorded_to,'')" if "recorded_to" in cols else ""
        extra += "||coalesce(status,'')" if "status" in cols else ""
        rows = conn.execute(f"SELECT group_concat(x, ',') FROM (SELECT {pk}||'' {extra} AS x FROM {t} ORDER BY {pk})").fetchone()[0] or ""
        parts.append(f"{t}:{sha(rows)}")
    return sha("|".join(parts))


class RecomputeStage(Stage):
    name = "recompute"
    custom = True

    def __init__(self, export_dir=None):
        self.export_dir = Path(export_dir or config.EXPORT_DIR)

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        stats = {"recomputed": [], "unchanged": []}
        self.export_dir.mkdir(parents=True, exist_ok=True)
        for name, (inputs, stmts) in OUTPUTS.items():
            fp = fingerprint(conn, inputs)
            old = conn.execute("SELECT input_fingerprint FROM out_meta WHERE output_name=?", (name,)).fetchone()
            if old and old["input_fingerprint"] == fp:
                stats["unchanged"].append(name)
                continue
            conn.execute(f"DELETE FROM {name}")
            for s in stmts:
                conn.execute(s)
            n = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            if not ctx.consume("rows", n):
                conn.rollback()
                stats["budget_exhausted"] = "rows"
                return stats
            conn.execute("INSERT INTO out_meta(output_name,computed_at,input_fingerprint,row_count) VALUES(?,?,?,?) "
                         "ON CONFLICT(output_name) DO UPDATE SET computed_at=excluded.computed_at, input_fingerprint=excluded.input_fingerprint, row_count=excluded.row_count",
                         (name, now_iso(), fp, n))
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {name}")]
            tmp = self.export_dir / f".{name}.json.tmp"
            tmp.write_text(json.dumps({"output": name, "computed_at": now_iso(), "rows": rows}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.export_dir / f"{name}.json")
            stats["recomputed"].append(name)
            ctx.processed += 1
        conn.commit()
        return stats
