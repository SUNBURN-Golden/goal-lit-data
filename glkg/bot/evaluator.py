"""출처 평가기: 새 도메인을 시험 수집(robots 통과 시 1건)하고 5개 점수로 등록/후순위를 결정한다.
점수는 전부 규칙 기반이다(AI 추정 없음). 점수 근거는 candidate_source 에 남는다."""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from ..timeutil import now_iso
from .http import host_of

LEXICON = ["소설", "문학", "시집", "작가", "비평", "서평", "서사", "한국", "국문", "시인", "희곡", "웹소설", "독자", "출판",
           "literature", "novel", "fiction", "poetry", "korean", "literary", "narrative", "criticism"]
REGISTER_MIN_TOTAL = 0.6
REGISTER_MIN_RELEVANCE = 0.34
REGISTER_MIN_EXTRACTABILITY = 0.4
WEIGHTS = {"relevance": 0.3, "novelty": 0.15, "extractability": 0.25, "original": 0.15, "rights": 0.15}


def _fields(conn, doc_id: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for r in conn.execute("SELECT field, value FROM extraction WHERE doc_id=?", (doc_id,)):
        out.setdefault(r["field"], []).append(r["value"])
    return out


def score_doc(conn, doc_id: str, domain: str) -> Dict[str, Any]:
    f = _fields(conn, doc_id)
    blob = " ".join(" ".join(f.get(k, [])) for k in ("title", "venue", "site_name", "keyword", "paper_author", "author", "publisher")).lower()
    hits = sum(1 for t in LEXICON if t in blob)
    known_titles = {r[0] for r in conn.execute("SELECT canonical_title FROM work")}
    rel = min(1.0, hits / 3)
    if any(t and t in blob for t in known_titles if len(t) >= 2):
        rel = 1.0
    have = [bool(f.get("title")), bool(f.get("author") or f.get("paper_author")), bool(f.get("pub_date") or f.get("pub_year")),
            bool(f.get("doi") or f.get("isbn")), bool(f.get("publisher") or f.get("venue") or f.get("site_name"))]
    ext = sum(have) / 5
    canon = (f.get("canonical_link") or [""])[0]
    if f.get("doi") or (canon and host_of(canon).removeprefix("www.") == domain.removeprefix("www.")):
        orig = 1.0
    elif canon and host_of(canon) and host_of(canon).removeprefix("www.") != domain.removeprefix("www."):
        orig = 0.2     # 정본이 다른 도메인 → 전재·복제 가능성
    else:
        orig = 0.5
    # 새로움: 이 문서의 식별자·제목이 이미 다른 문서에서 본 것이면 낮춘다
    ids = (f.get("doi", []) + f.get("isbn", []) + f.get("title", []))[:10]
    seen = 0
    for v in ids:
        if conn.execute("SELECT 1 FROM extraction WHERE value=? AND doc_id!=? LIMIT 1", (v, doc_id)).fetchone():
            seen += 1
    nov = 1.0 - (seen / len(ids) if ids else 0.5)
    structured = any(r[0] in ("structured", "meta") for r in conn.execute("SELECT DISTINCT method FROM extraction WHERE doc_id=?", (doc_id,)))
    rights = 0.9 if (structured and ext >= 0.4) else 0.4
    basis = "metadata_only" if rights >= 0.9 else "link_only"
    s = {"relevance": rel, "novelty": nov, "extractability": ext, "original": orig, "rights": rights}
    total = sum(WEIGHTS[k] * v for k, v in s.items())
    return {"scores": s, "total": round(total, 3), "usage_basis": basis, "hits": hits}


def decide(sc: Dict[str, Any]) -> (str, str):
    s = sc["scores"]
    if sc["total"] >= REGISTER_MIN_TOTAL and s["relevance"] >= REGISTER_MIN_RELEVANCE and s["extractability"] >= REGISTER_MIN_EXTRACTABILITY:
        return "registered", f"total={sc['total']} rel={s['relevance']:.2f} ext={s['extractability']:.2f}"
    why = []
    if s["relevance"] < REGISTER_MIN_RELEVANCE:
        why.append("낮은 관련성")
    if s["extractability"] < REGISTER_MIN_EXTRACTABILITY:
        why.append("추출 가능한 필드 부족")
    if sc["total"] < REGISTER_MIN_TOTAL:
        why.append(f"총점 {sc['total']}<{REGISTER_MIN_TOTAL}")
    return "deprioritized", "; ".join(why)


def apply_decision(conn, domain: str, state: str, reason: str, sc: Optional[Dict[str, Any]] = None) -> None:
    s = (sc or {}).get("scores", {})
    conn.execute(
        """UPDATE candidate_source SET state=?, decision_reason=?, score_relevance=?, score_novelty=?, score_extractability=?, score_original=?, score_rights=?,
           score_total=?, usage_basis=COALESCE(?, usage_basis), evaluated_at=? WHERE domain=?""",
        (state, reason[:300], s.get("relevance"), s.get("novelty"), s.get("extractability"), s.get("original"), s.get("rights"),
         (sc or {}).get("total"), (sc or {}).get("usage_basis"), now_iso(), domain))


OUTCOME_STATE = {"failed_robots": "blocked", "failed_policy": "blocked", "failed_login": "deprioritized", "failed_inaccessible": "blocked",
                 "failed_unsupported_type": "deprioritized", "failed_empty": "deprioritized"}


def evaluate_outcome(conn, row, res) -> str:
    """시험 수집 결과 → 후보 출처 상태. 반환: 최종 상태 문자열."""
    dom = row["domain"]
    if res.outcome in ("ok", "unchanged") and res.doc_id:
        sc = score_doc(conn, res.doc_id, dom)
        state, reason = decide(sc)
        apply_decision(conn, dom, state, reason, sc)
        if state == "registered":
            # 시험 문서도 등록 정책으로 다시 파싱되게(같은 해시여도 버전 불일치로 재처리) 한다
            conn.execute("UPDATE raw_document SET parsed_version=NULL WHERE doc_id=?", (res.doc_id,))
        return state
    if res.outcome in OUTCOME_STATE:
        st = OUTCOME_STATE[res.outcome]
        apply_decision(conn, dom, st, f"{res.outcome}:{res.detail}")
        return st
    # 일시 실패: 3회까지 'new' 유지
    m = re.match(r"transient:(\d+)", row["decision_reason"] or "")
    n = (int(m.group(1)) if m else 0) + 1
    if n >= 3:
        apply_decision(conn, dom, "deprioritized", f"transient_x{n}:{res.outcome}")
        return "deprioritized"
    conn.execute("UPDATE candidate_source SET decision_reason=? WHERE domain=?", (f"transient:{n}", dom))
    return "new"
