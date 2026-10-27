import json
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from ..db import enqueue_review, log_change, sha, stable_id
from ..timeutil import now_iso

TABLE_PK = {
    "bib_fact": "fact_id", "author_statement": "statement_id", "interpretation": "interp_id", "work_author": "wa_id",
    "edition": "edition_id", "work_theme": "wt_id", "interpretation_relation": "rel_id",
}
CLAIM_TABLES = tuple(TABLE_PK)


def parse_date(s: str) -> Tuple[Optional[str], str]:
    """'2016', '2016-05', '2016-05-16', '2016.05.16' → (정규화 문자열, 정밀도). 해석 불가는 (None,'unknown')."""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?$", s)
    if not m:
        return None, "unknown"
    y, mo, d = m.groups()
    if d:
        return f"{y}-{int(mo):02d}-{int(d):02d}", "day"
    if mo:
        return f"{y}-{int(mo):02d}", "month"
    return y, "year"


def source_kind(access_level: str, path: str, internal: bool) -> str:
    if internal:
        return "internal_record"
    if path == "manual_clip":
        return "clip"
    return {"paper_abstract": "paper", "book_intro": "catalog"}.get(access_level, "article")


def upsert_source(conn, row, policy: Dict[str, Any], reg, now: str) -> str:
    canon = row["canonical_url"] or row["url"]
    sid = stable_id("src", canon)
    internal = not policy["is_external"]
    exists = conn.execute("SELECT 1 FROM source WHERE source_id=?", (sid,)).fetchone()
    if exists:
        conn.execute("UPDATE source SET last_seen_at=? WHERE source_id=?", (now, sid))
        return sid
    retention = reg.retention_until(policy["registry_key"], row["collected_at"] or now)
    conn.execute(
        """INSERT INTO source(source_id,registry_key,url,canonical_url,domain,outlet,kind,access_level,is_external,usage_basis,
           retention_until,published_at,first_seen_at,last_seen_at,legacy_inbox_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, policy["registry_key"], row["url"], canon, row["domain"] or urlparse(row["url"]).netloc, row["outlet"] or "",
         source_kind(row["source_access_level"], row["collection_path"], internal), row["source_access_level"],
         0 if internal else 1, policy["usage_basis"], retention, row["published_at"] or "", now, now, row["id"]),
    )
    return sid


def locator_type(loc: str) -> str:
    if loc.startswith(("works.json:", "posts.json:")) or "pointer" in loc:
        return "legacy_pointer"
    if loc == "activeTab_selection":
        return "dom_path"
    if re.search(r"문단|paragraph|¶", loc):
        return "paragraph"
    if re.search(r"쪽|page|p\.\d", loc):
        return "page"
    if loc.startswith(("$.", "api:", "openalex")):
        return "api_field"
    return "legacy_pointer"


def upsert_evidence(conn, row, source_id: str, locator: str, now: str, passage_text: str = "") -> Optional[str]:
    """근거 '위치'만 저장한다. 인용 조각은 비워 둔다(≤12자 규칙은 스키마 CHECK 와 검증 단계가 지킴). 원문은 해시만."""
    locator = (locator or "").strip()
    if not locator:
        return None
    eid = stable_id("ev", source_id, locator)
    lt = locator_type(locator)
    conn.execute(
        """INSERT OR IGNORE INTO evidence_passage(evidence_id,source_id,locator,locator_type,locator_quality,quote_snippet,passage_hash,collected_at,legacy_inbox_id)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (eid, source_id, locator, lt, "legacy_pointer" if lt == "legacy_pointer" else "precise", "",
         sha(passage_text) if passage_text else None, row["collected_at"] or now, row["id"]),
    )
    if lt == "legacy_pointer":
        enqueue_review(conn, "locator_upgrade", "evidence_passage", eid, "포인터형 위치 → 문단/쪽 단위 위치로 보강 필요", 20)
    return eid


def map_legacy(conn, inbox_id: str, table: str, kg_id: str, chash: str, now: str) -> None:
    conn.execute("INSERT OR IGNORE INTO legacy_map(inbox_id,kg_table,kg_id,content_hash,mapped_at) VALUES(?,?,?,?,?)",
                 (inbox_id, table, kg_id, chash, now))


def insert_claim(conn, table: str, values: Dict[str, Any], run_id: str = None) -> bool:
    """주장 행 삽입(이미 있으면 무시). 새로 들어간 경우 변경 이력에 남긴다."""
    cols = ",".join(values)
    qs = ",".join("?" for _ in values)
    cur = conn.execute(f"INSERT OR IGNORE INTO {table}({cols}) VALUES({qs})", tuple(values.values()))
    if cur.rowcount:
        pk = TABLE_PK[table]
        log_change(conn, table, values[pk], "insert", None, {"status": values.get("epistemic_status")}, "extract/resolve", run_id)
        return True
    return False
