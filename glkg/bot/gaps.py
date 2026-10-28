"""빈칸 탐색기: 작품별 필수 필드(title/author/pub_year/publisher/isbn)의 빈칸을 찾고 상태를 붙인다. AI 로 채우지 않는다.

상태(결정 규칙, 추측 없음):
  open           역검색(작품별 Crossref 도서 검색·OpenAlex 제목 검색)이 아직 끝나지 않음
  conflict       같은 필드에 출처가 다른 값이 둘 이상(덮어쓰지 않고 검수 대기)
  not_extracted  이 작품의 근거 문서 중 본문이 있는 HTML/PDF/이미지가 있는데 필드가 추출되지 않음(추출기 한계 가능)
  not_in_source  읽을 수 있는 출처(구조화 레코드)가 있고 역검색도 끝났는데 해당 필드가 없음
  inaccessible   역검색 질의가 모두 실패/건너뜀(접근 불가·키 없음) → 확인할 수 없음
  resolved       빈칸이 채워짐(채운 문서·출처를 resolved_by 에 기록)
"""
import json
from typing import Dict, List, Optional

from ..db import stable_id
from ..timeutil import now_iso
from . import queries as Q

REQUIRED = ("title", "author", "pub_year", "publisher", "isbn")
UNREACHABLE = {"nlk": "국립중앙도서관 API 키 필요(미보유)", "google_books": "Google Books 키 필요(할당 0)"}
REVERSE_KINDS = ("gap_crossref", "gap_openalex")


def filled_fields(conn, work_id: str) -> Dict[str, Optional[str]]:
    """필드 → 근거 source_id (없으면 None). 번역판은 한국어 원서 필수 필드에서 제외."""
    out: Dict[str, Optional[str]] = {"title": "work"}
    r = conn.execute("SELECT source_id FROM work_author WHERE work_id=? AND role='author' AND recorded_to IS NULL LIMIT 1", (work_id,)).fetchone()
    out["author"] = (r["source_id"] or "legacy") if r else None
    for f in ("pub_year", "publisher"):
        r = conn.execute("SELECT source_id FROM bib_fact WHERE subject_type='work' AND subject_id=? AND predicate=? AND value_text!='' AND recorded_to IS NULL LIMIT 1", (work_id, f)).fetchone()
        if not r:
            r = conn.execute("""SELECT b.source_id FROM bib_fact b JOIN edition e ON e.edition_id=b.subject_id
                                WHERE b.subject_type='edition' AND e.work_id=? AND b.predicate=? AND b.value_text!='' AND b.recorded_to IS NULL
                                AND e.label NOT LIKE 'translation:%' AND e.recorded_to IS NULL LIMIT 1""", (work_id, f)).fetchone()
        out[f] = (r["source_id"] or "legacy") if r else None
    r = conn.execute("SELECT source_id FROM edition WHERE work_id=? AND isbn IS NOT NULL AND isbn!='' AND label NOT LIKE 'translation:%' AND recorded_to IS NULL LIMIT 1", (work_id,)).fetchone()
    out["isbn"] = (r["source_id"] or "legacy") if r else None
    return out


def has_conflict(conn, work_id: str, field: str) -> bool:
    if field in ("pub_year", "publisher"):
        vals = {r[0].strip().lower() for r in conn.execute("SELECT value_text FROM bib_fact WHERE subject_type='work' AND subject_id=? AND predicate=? AND recorded_to IS NULL", (work_id, field))}
        vals.discard("")
        return len(vals) > 1
    return False


def reverse_queries(conn, work_id: str) -> List[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT kind, state FROM discovery_query WHERE kind IN ('gap_crossref','gap_openalex') AND json_extract(params_json,'$.work_hint')=?", (work_id,))]


def _text_doc_without_field(conn, work_id: str) -> bool:
    """근거 문서 중 본문 텍스트가 있는 HTML/PDF/이미지가 있는가(→ 규칙 추출기가 놓쳤을 수 있다)."""
    r = conn.execute(
        """SELECT 1 FROM raw_document d JOIN extraction x ON x.doc_id=d.doc_id AND x.field IN ('text_chars','ocr_chars','page_text_chars')
           WHERE d.fetch_method IN ('html','browser','pdf','image') AND CAST(x.value AS INTEGER)>0
           AND d.canonical_url IN (SELECT s.canonical_url FROM source s WHERE s.source_id IN
              (SELECT source_id FROM bib_fact WHERE subject_type='work' AND subject_id=? UNION SELECT source_id FROM work_author WHERE work_id=?)) LIMIT 1""", (work_id, work_id)).fetchone()
    return bool(r)


def set_state(conn, gap_id: str, new: str, detail: str, resolved_by: str = "") -> bool:
    row = conn.execute("SELECT state, detail FROM gap WHERE gap_id=?", (gap_id,)).fetchone()
    if row["state"] == new and row["detail"] == detail:
        return False
    now = now_iso()
    conn.execute("UPDATE gap SET state=?, detail=?, updated_at=?, resolved_at=CASE WHEN ?='resolved' THEN ? ELSE resolved_at END, resolved_by=CASE WHEN ?='resolved' THEN ? ELSE resolved_by END WHERE gap_id=?",
                 (new, detail[:400], now, new, now, new, resolved_by, gap_id))
    if row["state"] != new:
        conn.execute("INSERT INTO gap_history(gap_id,old_state,new_state,detail,at) VALUES(?,?,?,?,?)", (gap_id, row["state"], new, detail[:400], now))
    return True


def explore_work(conn, budget, work_id: str, max_rounds: int = 2) -> Dict[str, int]:
    st = {"opened": 0, "resolved": 0, "changed": 0, "queries": 0}
    now = now_iso()
    fills = filled_fields(conn, work_id)
    missing = [f for f in REQUIRED if not fills.get(f)]
    w = conn.execute("SELECT canonical_title FROM work WHERE work_id=?", (work_id,)).fetchone()
    # 1) 역검색 질의 생성(작품 단위, 중복 없음): 빈 필드가 있을 때만
    if missing:
        au = conn.execute("SELECT a.canonical_name FROM work_author wa JOIN author a ON a.author_id=wa.author_id WHERE wa.work_id=? AND wa.role='author' LIMIT 1", (work_id,)).fetchone()
        author = au[0] if au else ""
        if Q.enqueue_query(conn, budget, "gap_crossref", Q.cr_book_url(w["canonical_title"], author), f"gap crossref {w['canonical_title']}", "backfill", 55, origin="gap:" + ",".join(missing), work_hint=work_id):
            st["queries"] += 1
        if Q.enqueue_query(conn, budget, "gap_openalex", Q.oa_search_url(f'"{w["canonical_title"]}"'), f"gap openalex {w['canonical_title']}", "backfill", 50, origin="gap:" + ",".join(missing), work_hint=work_id):
            st["queries"] += 1
    rq = reverse_queries(conn, work_id)
    pending = any(q["state"] == "queued" for q in rq)
    done = [q for q in rq if q["state"] == "done"]
    failed = [q for q in rq if q["state"] in ("failed", "skipped")]
    checked = sorted({q["kind"].replace("gap_", "") for q in done})
    unreachable = "; ".join(f"{k}: {v}" for k, v in UNREACHABLE.items())
    for f in REQUIRED:
        gid = stable_id("gap", work_id, f)
        row = conn.execute("SELECT * FROM gap WHERE gap_id=?", (gid,)).fetchone()
        if fills.get(f):
            if row and row["state"] != "resolved":
                if set_state(conn, gid, "resolved", "채워짐", resolved_by=str(fills[f])):
                    st["resolved"] += 1
                    st["changed"] += 1
            if f != "title" and has_conflict(conn, work_id, f):
                if row is None:
                    conn.execute("INSERT INTO gap(gap_id,work_id,field,state,detail,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?)", (gid, work_id, f, "open", "", now, now))
                    st["opened"] += 1
                set_state(conn, gid, "conflict", "출처 간 값 충돌(덮어쓰지 않음, 검수 대기)")
            continue
        if row is None:
            conn.execute("INSERT INTO gap(gap_id,work_id,field,state,detail,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?)", (gid, work_id, f, "open", "역검색 대기", now, now))
            st["opened"] += 1
        if has_conflict(conn, work_id, f):
            new, detail = "conflict", "출처 간 값 충돌"
        elif pending or not rq:
            new, detail = "open", "역검색 대기"
        elif _text_doc_without_field(conn, work_id):
            new, detail = "not_extracted", f"본문이 있는 문서에서 규칙 추출기가 {f} 를 못 찾음(추출기 한계 가능). 역검색: {','.join(checked) or '없음'}"
        elif done:
            new, detail = "not_in_source", f"읽을 수 있는 출처에 없음. 확인: {','.join(checked)}. 접근 불가: {unreachable}"
        else:
            new, detail = "inaccessible", f"역검색 질의 모두 실패/건너뜀({len(failed)}건). 접근 불가: {unreachable}"
        if set_state(conn, gid, new, detail):
            st["changed"] += 1
        conn.execute("UPDATE gap SET attempts=? WHERE gap_id=?", (min(len(rq), 99), gid))
    return st


def refresh_coverage(conn) -> Dict[str, object]:
    conn.execute("DELETE FROM out_coverage")
    for r in conn.execute("SELECT work_id FROM work").fetchall():
        fills = filled_fields(conn, r["work_id"])
        for f in REQUIRED:
            g = conn.execute("SELECT state FROM gap WHERE gap_id=?", (stable_id("gap", r["work_id"], f),)).fetchone()
            conn.execute("INSERT INTO out_coverage(work_id,field,filled,gap_state) VALUES(?,?,?,?)", (r["work_id"], f, 1 if fills.get(f) else 0, g["state"] if g else None))
    return coverage_summary(conn)


def coverage_summary(conn) -> Dict[str, object]:
    total = conn.execute("SELECT count(*) FROM out_coverage").fetchone()[0]
    filled = conn.execute("SELECT count(*) FROM out_coverage WHERE filled=1").fetchone()[0]
    per = {r["field"]: f'{r["f"]}/{r["n"]}' for r in conn.execute("SELECT field, sum(filled) f, count(*) n FROM out_coverage GROUP BY field")}
    gaps = {r["state"]: r["n"] for r in conn.execute("SELECT state, count(*) n FROM gap GROUP BY state")}
    opened = sum(gaps.values())
    resolved = gaps.get("resolved", 0)
    classified = sum(v for k, v in gaps.items() if k not in ("open", "resolved"))
    works = conn.execute("SELECT count(*) FROM work").fetchone()[0]
    full = conn.execute("SELECT count(*) FROM (SELECT work_id FROM out_coverage GROUP BY work_id HAVING sum(filled)=count(*))").fetchone()[0]
    return {"works": works, "works_all_required": full, "cells_filled": f"{filled}/{total}", "coverage": round(filled / total, 3) if total else 0.0, "per_field": per,
            "gaps": gaps, "gaps_total": opened, "resolved_rate": round(resolved / opened, 3) if opened else None,
            "classified_rate": round((classified + resolved) / opened, 3) if opened else None}
