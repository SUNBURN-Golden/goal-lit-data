"""기존 수집함(goallit_inbox.db, 리포 밖 비공개) 읽기 전용 접근. 원문은 여기에만 있고 지식그래프 DB로 복사하지 않는다."""
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .db import sha

# 주장 내용을 바꾸는 필드만 해시에 넣는다. (validation_status·conflict 는 상태이므로 검증 단계가 매번 라이브로 읽는다)
CONTENT_FIELDS = (
    "url", "title", "outlet", "published_at", "target_work_slug", "source_access_level", "unverified_category",
    "work_info_json", "edition_info_json", "author_statement", "critical_thesis", "counter_argument",
    "extracted_facts_json", "evidence_locator", "collection_path",
)


def open_inbox(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path or config.INBOX_DB_PATH)
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def content_hash(row) -> str:
    d = {k: row[k] for k in CONTENT_FIELDS}
    return sha(json.dumps(d, ensure_ascii=False, sort_keys=True))


def rows_after(conn: sqlite3.Connection, rowid: int, limit: int) -> List[sqlite3.Row]:
    return conn.execute("SELECT rowid AS _rowid, * FROM items WHERE rowid > ? ORDER BY rowid LIMIT ?", (rowid, limit)).fetchall()


def get_row(conn: sqlite3.Connection, inbox_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT rowid AS _rowid, * FROM items WHERE id = ?", (inbox_id,)).fetchone()


def json_or(s: str, default):
    try:
        v = json.loads(s) if s else default
        return v if v is not None else default
    except Exception:
        return default
