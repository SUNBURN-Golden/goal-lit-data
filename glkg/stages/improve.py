"""자기 개선 단계(하루 1회): 지표 스냅샷·기준선 대비 변화 · 보류 평가 세트 정밀도/재현율 · 실패 출처·지속 빈칸·품질 경고 → 개선 제안 파일.
안전하고 결정적인 수정(실패 도메인 백오프, robots 가 다시 열린 출처 재허용)만 제안 JSON 으로 남기고, PR 은 ops/improve_pr.py 가 연다(테스트 통과 시에만 병합).
AI 추론을 사실로 올리지 않는다. 차단 우회 없음(robots 재확인은 정직한 UA로 하루 1건, 공유 예산에서 차감)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config
from ..pipeline import Context, Stage
from ..timeutil import now_iso, today

IMPROVE_DIR = Path(os.environ.get("GLKG_IMPROVE_DIR", str(config.DATA_DIR / "improve")))
BASELINE = Path(os.environ.get("GLKG_PROOF_BASELINE", "/workspace/gl-data/proof_baseline.json"))
HELDOUT_MOD = 5          # doc_id 해시 % 5 == 0 → 보류(평가 전용). 나머지는 튜닝용. 규칙 튜닝 때 보류 문서는 보지 않는다.
EVAL_FIELDS = ("isbn", "pub_year", "publisher")
FAIL_MIN_ATTEMPTS, FAIL_RATIO, BACKOFF_DAYS = 3, 0.8, 7
PERSIST_DAYS = 3


def _ago(days: int) -> str:
    from ..timeutil import add_seconds
    return add_seconds(now_iso(), -days * 86400)


def is_heldout(doc_id: str) -> bool:
    return int(hashlib.sha1(doc_id.encode()).hexdigest(), 16) % HELDOUT_MOD == 0


def _norm(field: str, v: str) -> str:
    v = (v or "").strip().lower()
    return v.replace("-", "").replace(" ", "") if field == "isbn" else v


def build_heldout(conn) -> Dict[str, Any]:
    """보류 세트(동결): 본문이 저장된 문서 중 해시로 고른 20%. 정답은 생성 시점 DB 의 출처 레코드 값(bib_fact·edition, AI 추론 제외)."""
    docs = []
    for d in conn.execute("SELECT doc_id, canonical_url, url, fetch_method, content_type, storage_path FROM raw_document WHERE body_stored=1 AND storage_path IS NOT NULL ORDER BY doc_id"):
        if not is_heldout(d["doc_id"]):
            continue
        gold: Dict[str, List[str]] = {}
        for r in conn.execute("""SELECT f.predicate, f.value_text FROM bib_fact f JOIN source s ON s.source_id=f.source_id
                                 WHERE s.canonical_url=? AND f.predicate IN ('pub_year','publisher') AND f.epistemic_status IN ('confirmed_fact','source_claim')
                                 AND f.value_text<>''""", (d["canonical_url"],)):
            gold.setdefault(r[0], []).append(r[1])
        for r in conn.execute("SELECT e.isbn FROM edition e JOIN source s ON s.source_id=e.source_id WHERE s.canonical_url=? AND e.isbn<>''", (d["canonical_url"],)):
            gold.setdefault("isbn", []).append(r[0])
        if gold:
            docs.append({"doc_id": d["doc_id"], "url": d["url"], "fetch_method": d["fetch_method"], "content_type": d["content_type"],
                         "storage_path": d["storage_path"], "gold": {k: sorted(set(v)) for k, v in gold.items()}})
    return {"created_at": now_iso(), "rule": f"sha1(doc_id) % {HELDOUT_MOD} == 0", "fields": list(EVAL_FIELDS),
            "gold_basis": "생성 시점 출처 레코드 값 동결(회귀 감지용, 독립 수작업 정답 아님). 튜닝 데이터와 분리: 보류 문서는 규칙 튜닝에 쓰지 않는다.",
            "docs": docs}


def evaluate(heldout: Dict[str, Any]) -> Dict[str, Any]:
    from ..bot.executor import extractor_kind
    from ..bot.extractors import extract_by_kind
    tp = fp = fn = 0
    per = {f: {"tp": 0, "fp": 0, "fn": 0} for f in EVAL_FIELDS}
    missing = 0
    for d in heldout.get("docs", []):
        p = Path(d["storage_path"])
        if not p.exists():
            missing += 1
            continue
        try:
            _, fields = extract_by_kind(extractor_kind(d["url"], d["fetch_method"], d["content_type"]), p.read_bytes(), d["url"], d["content_type"])
        except Exception:  # noqa: BLE001
            fields = []
        pred: Dict[str, set] = {}
        for f, v, _loc, _m in fields:
            f = f[3:] if f.startswith("ed_") else f
            if f in EVAL_FIELDS:
                pred.setdefault(f, set()).add(_norm(f, v))
        for f in EVAL_FIELDS:
            g = {_norm(f, v) for v in d["gold"].get(f, [])}
            if not g:
                continue   # 정답이 없는 필드는 채점하지 않는다(없음을 틀림으로 세지 않음)
            pr = pred.get(f, set())
            a, b, c = len(g & pr), len(pr - g), len(g - pr)
            per[f]["tp"] += a; per[f]["fp"] += b; per[f]["fn"] += c
            tp += a; fp += b; fn += c
    prf = lambda t, p_, n: {"precision": round(t / (t + p_), 3) if t + p_ else None, "recall": round(t / (t + n), 3) if t + n else None}
    return {"docs": len(heldout.get("docs", [])), "missing_bodies": missing, **prf(tp, fp, fn), "per_field": {f: prf(**{"t": v["tp"], "p_": v["fp"], "n": v["fn"]}) for f, v in per.items()}}


def failing_sources(conn, days: int = 7) -> List[Dict[str, Any]]:
    rows = conn.execute("""SELECT domain, count(*) n, sum(outcome NOT IN ('ok','unchanged')) bad,
                                  group_concat(DISTINCT CASE WHEN outcome NOT IN ('ok','unchanged') THEN outcome END) outcomes
                           FROM fetch_log WHERE at >= ? GROUP BY domain""", (_ago(days),)).fetchall()
    out = []
    for r in rows:
        if r["n"] >= FAIL_MIN_ATTEMPTS and r["bad"] / r["n"] >= FAIL_RATIO:
            out.append({"domain": r["domain"], "attempts": r["n"], "failed": r["bad"], "outcomes": r["outcomes"] or ""})
    return sorted(out, key=lambda x: -x["failed"])


def persistent_gaps(conn) -> Dict[str, Any]:
    rows = conn.execute("SELECT field, state, count(*) n FROM gap WHERE state<>'resolved' AND first_seen_at <= ? GROUP BY field, state",
                        (_ago(PERSIST_DAYS),)).fetchall()
    return {f"{r['field']}/{r['state']}": r["n"] for r in rows}


def quality_warnings(conn) -> List[str]:
    r = conn.execute("SELECT detail_json FROM job_run WHERE stage='quality' AND status IN ('ok','partial') ORDER BY started_at DESC LIMIT 1").fetchone()
    if not r:
        return ["quality 단계 실행 기록 없음"]
    try:
        d = json.loads(r[0] or "{}")
    except ValueError:
        return ["quality detail 파싱 실패"]
    checks = d.get("checks") or {}
    return [f"{k}: 위반 {v.get('violations')} ({v.get('severity')})" for k, v in checks.items() if isinstance(v, dict) and not v.get("pass", True)]


def current_backoff() -> Dict[str, Any]:
    p = config.REPO_DIR / "sources" / "backoff.json"
    try:
        return json.loads(p.read_text()).get("domains", {})
    except (OSError, ValueError):
        return {}


def safe_changes(failing: List[Dict[str, Any]], robots_reopened: List[str]) -> Dict[str, Any]:
    """결정적 규칙으로만: (1) 7일 실패율 ≥80%·시도 ≥3 도메인 → 7일 백오프. 단 robots/정책 차단은 이미 수집 안 하므로 제외.
    (2) robots 가 다시 허용한 등록 출처 → support_level 'supported' 복귀(근거: 그날 받은 robots.txt)."""
    from ..timeutil import add_seconds
    bo = current_backoff()
    add = {}
    for f in failing:
        if set((f["outcomes"] or "").split(",")) <= {"failed_robots", "failed_policy"}:
            continue
        if f["domain"] in bo and (bo[f["domain"]].get("until") or "") > now_iso():
            continue
        add[f["domain"]] = {"until": add_seconds(now_iso(), BACKOFF_DAYS * 86400), "reason": f"7일 {f['failed']}/{f['attempts']} 실패({f['outcomes']})", "added": today()}
    return {"backoff_add": add, "registry_enable": robots_reopened}


def recheck_robots(conn, keys=("nlk_lod_sparql",), transport=None, clock=None) -> Dict[str, str]:
    """막아 둔 출처의 robots.txt 를 하루 1번 다시 확인(우리 UA·AI 크롤러 규칙 둘 다 통과해야 '열림'). 예산에서 1건씩 차감."""
    from ..bot import http as H
    from ..bot.budget import Budget
    from ..bot.queries import NLK_LOD
    probe = {"nlk_lod_sparql": NLK_LOD + "?query=ASK%7B%7D"}
    bud = Budget(conn)
    clock = clock or H.DomainClock(before_wait=conn.commit)
    gate = H.RobotsGate(transport or H.RequestsTransport(), clock, charge=lambda: bud.take("refresh"))
    out = {}
    for k in keys:
        try:
            ok, why = gate.allowed(probe[k])
        except H.BudgetGate:
            ok, why = False, "budget_exhausted"
        out[k] = "allowed" if ok else why
    return out


class ImproveStage(Stage):
    name = "improve"
    custom = True

    def __init__(self, out_dir: Optional[Path] = None, transport=None, clock=None, open_pr: Optional[bool] = None, force: bool = False):
        self.out = Path(out_dir or IMPROVE_DIR)
        self.transport, self.clock, self.force = transport, clock, force
        self.open_pr = (os.environ.get("GLKG_IMPROVE_PR") == "1") if open_pr is None else open_pr

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        day = today()
        self.out.mkdir(parents=True, exist_ok=True)
        snap_p = self.out / f"metrics-{day}.json"
        if snap_p.exists() and not self.force:
            return {"skipped": "already_ran_today", "day": day}
        # (a) 지표 스냅샷 vs 기준선
        sys.path.insert(0, str(config.REPO_DIR / "ops"))
        import proof_metrics
        conn.commit()
        m = proof_metrics.collect_metrics()
        base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
        delta = {}
        for k in ("works_total", "authors", "editions", "documents"):
            if k in m and k in base:
                delta[k] = {"baseline": base[k], "now": m[k], "delta": m[k] - base[k]}
        pf_now = (m.get("coverage") or {}).get("per_field", {})
        pf_base = (base.get("coverage") or {}).get("per_field", {})
        for f in pf_now:
            delta[f"filled:{f}"] = {"baseline": pf_base.get(f), "now": pf_now[f]}
        # (b) 보류 세트 평가
        ho_p = self.out / "heldout.json"
        if not ho_p.exists():
            ho_p.write_text(json.dumps(build_heldout(conn), ensure_ascii=False, indent=1))
        ev = evaluate(json.loads(ho_p.read_text()))
        # (c) 실패 출처·지속 빈칸·품질 경고·robots 재확인
        fails = failing_sources(conn)
        pg = persistent_gaps(conn)
        qw = quality_warnings(conn)
        robots = recheck_robots(conn, transport=self.transport, clock=self.clock)
        reopened = [k for k, v in robots.items() if v == "allowed"]
        changes = safe_changes(fails, reopened)
        snap = {"day": day, "at": now_iso(), "metrics": m, "delta_vs_baseline": delta, "eval_heldout": ev, "failing_sources": fails,
                "persistent_gaps": pg, "quality_warnings": qw, "robots_recheck": robots, "safe_changes": changes}
        snap_p.write_text(json.dumps(snap, ensure_ascii=False, indent=1, default=str))
        # (d) 제안 파일
        prop = self.out / f"proposal-{day}.md"
        prop.write_text(render_proposal(snap))
        pr = None
        if self.open_pr and (changes["backoff_add"] or changes["registry_enable"]):
            ch = self.out / f"changes-{day}.json"
            ch.write_text(json.dumps(changes, ensure_ascii=False, indent=1))
            r = subprocess.run([sys.executable, str(config.REPO_DIR / "ops" / "improve_pr.py"), str(ch), str(prop)], capture_output=True, text=True, timeout=1800)
            pr = (r.stdout.strip().splitlines() or [""])[-1][:300] + ("" if r.returncode == 0 else f" (rc={r.returncode} {r.stderr[-200:]})")
        ctx.processed += 1
        return {"day": day, "snapshot": str(snap_p), "proposal": str(prop), "precision": ev["precision"], "recall": ev["recall"],
                "failing_sources": len(fails), "quality_warnings": len(qw), "robots": robots, "backoff_add": list(changes["backoff_add"]), "pr": pr}


def render_proposal(s: Dict[str, Any]) -> str:
    L = [f"# 꼴릿 지식그래프 개선 제안 — {s['day']}", "", "자동 생성(규칙 기반). AI 추론을 사실로 올리지 않으며, 차단된 출처는 우회하지 않는다.", "",
         "## 기준선 대비", ""]
    L += [f"- {k}: {v.get('baseline')} → {v.get('now')}" + (f" ({v['delta']:+d})" if "delta" in v else "") for k, v in s["delta_vs_baseline"].items()]
    e = s["eval_heldout"]
    L += ["", "## 보류 세트 추출 평가", "", f"- 문서 {e['docs']}건(본문 없음 {e['missing_bodies']}) · 정밀도 {e['precision']} · 재현율 {e['recall']}"]
    L += [f"- {f}: P={v['precision']} R={v['recall']}" for f, v in e["per_field"].items()]
    L += ["", "## 실패 출처(7일, 실패율 ≥80%)", ""] + ([f"- {f['domain']}: {f['failed']}/{f['attempts']} ({f['outcomes']})" for f in s["failing_sources"]] or ["- 없음"])
    L += ["", "## 지속 빈칸(3일 이상)", ""] + ([f"- {k}: {v}" for k, v in sorted(s["persistent_gaps"].items())] or ["- 없음"])
    L += ["", "## 품질 경고", ""] + ([f"- {w}" for w in s["quality_warnings"]] or ["- 없음"])
    L += ["", "## robots 재확인", ""] + [f"- {k}: {v}" for k, v in s["robots_recheck"].items()]
    c = s["safe_changes"]
    L += ["", "## 자동 PR 대상(안전·결정적 수정만)", ""] + ([f"- 백오프 {d}: {v['until']} 까지 — {v['reason']}" for d, v in c["backoff_add"].items()] + [f"- 출처 재허용: {k}" for k in c["registry_enable"]] or ["- 없음"])
    L += ["", "## 사람 검토가 필요한 제안", "", "- 지속 빈칸이 큰 필드는 새 출처 후보가 필요(키 발급·공식 API). 자동 수정 대상 아님.",
          "- 보류 세트 재현율이 떨어지면 튜닝 세트로만 규칙을 고치고 보류 세트로 다시 잰다."]
    return "\n".join(L) + "\n"
