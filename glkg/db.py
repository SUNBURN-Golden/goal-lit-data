"""SQLite 연결·마이그레이션·공통 헬퍼."""
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import config
from .timeutil import now_iso


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path or config.KG_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: Optional[Path] = None) -> list:
    """schema/migrations/NNN_name.sql 을 번호순으로 한 번씩만 적용한다. 적용한 버전 목록을 돌려준다."""
    mdir = Path(migrations_dir or config.MIGRATIONS_DIR)
    conn.execute("CREATE TABLE IF NOT EXISTS _migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT version FROM _migrations")}
    applied = []
    for f in sorted(mdir.glob("[0-9][0-9][0-9]_*.sql")):
        ver = int(f.name[:3])
        if ver in done:
            continue
        conn.executescript("BEGIN;\n" + f.read_text(encoding="utf-8") + "\nCOMMIT;")
        conn.execute("INSERT INTO _migrations(version,name,applied_at) VALUES(?,?,?)", (ver, f.name, now_iso()))
        conn.commit()
        applied.append(ver)
    return applied


def open_kg(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


def stable_id(prefix: str, *parts: Any, n: int = 14) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:n]}"


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def log_change(conn, table: str, row_id: str, op: str, old: Any = None, new: Any = None, reason: str = "", run_id: str = None) -> None:
    conn.execute(
        "INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,run_id,at) VALUES(?,?,?,?,?,?,?,?)",
        (table, row_id, op, json.dumps(old, ensure_ascii=False) if old is not None else None,
         json.dumps(new, ensure_ascii=False) if new is not None else None, reason, run_id, now_iso()),
    )


def enqueue_review(conn, kind: str, ref_table: str, ref_id: str, reason: str, priority: int = 50) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO review_queue(kind,ref_table,ref_id,reason,priority,created_at) VALUES(?,?,?,?,?,?)",
        (kind, ref_table, ref_id, reason, priority, now_iso()),
    )
