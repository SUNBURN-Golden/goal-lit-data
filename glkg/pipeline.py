"""데몬 공통 기반: 중복 실행 락 · 체크포인트 · 제한 재시도 · 실패 큐 · 일일 상한 · 실패 지점부터 재개.

단계(Stage)는 두 가지 중 하나를 구현한다.
  - 배치형:   next_batch(ctx, cursor) -> [Item]  /  handle(ctx, item)     (커서는 배치 커밋 후에만 전진)
  - 커스텀형: run_custom(ctx) -> dict                                       (네트워크 등, 단계가 직접 진행)
handle()은 멱등이어야 한다(배치 도중 죽으면 같은 배치를 다시 처리한다).
"""
import fcntl
import json
import os
import socket
import sqlite3
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .timeutil import add_seconds, now_iso, today


@dataclass
class Item:
    key: str            # 커서로 쓰는 정렬 키 (문자열 비교 가능한 값)
    ref_id: str         # 실패 큐에 남길 식별자
    payload: Any = None


class BudgetExceeded(Exception):
    pass


class Context:
    def __init__(self, conn: sqlite3.Connection, stage: str, run_id: str, caps: Dict[str, int]):
        self.conn = conn
        self.stage = stage
        self.run_id = run_id
        self.caps = caps
        self.processed = 0
        self.failed = 0
        self.detail: Dict[str, Any] = {}

    # ---- 일일 상한 ----
    def remaining(self, metric: str) -> int:
        cap = self.caps.get(metric)
        if cap is None:
            return 10 ** 9
        row = self.conn.execute("SELECT used FROM daily_usage WHERE day=? AND stage=? AND metric=?", (today(), self.stage, metric)).fetchone()
        return max(0, cap - (row["used"] if row else 0))

    def require(self, metric: str, n: int = 1) -> None:
        if not self.consume(metric, n):
            raise BudgetExceeded(metric)

    def consume(self, metric: str, n: int = 1) -> bool:
        """상한 안이면 사용량을 올리고 True, 넘으면 False(사용량 불변)."""
        cap = self.caps.get(metric)
        if cap is None:
            return True
        if self.remaining(metric) < n:
            return False
        self.conn.execute(
            "INSERT INTO daily_usage(day,stage,metric,used,cap) VALUES(?,?,?,?,?) "
            "ON CONFLICT(day,stage,metric) DO UPDATE SET used=used+excluded.used, cap=excluded.cap",
            (today(), self.stage, metric, n, cap),
        )
        return True


class Stage:
    name = "base"
    batch_size = 50
    budget_metric: Optional[str] = None   # 항목 1개당 소모할 상한 지표 (없으면 무제한)
    custom = False

    def next_batch(self, ctx: Context, cursor: str) -> List[Item]:
        return []

    def load_ref(self, ctx: Context, ref_id: str) -> Optional[Item]:
        """실패 큐 재시도용: ref_id로 항목을 다시 만든다. 지원하지 않으면 None."""
        return None

    def handle(self, ctx: Context, item: Item) -> None:
        raise NotImplementedError

    def run_custom(self, ctx: Context) -> Dict[str, Any]:
        raise NotImplementedError

    def on_cycle_complete(self, ctx: Context) -> None:
        """배치형이 끝(빈 배치)에 도달했을 때 호출. 한 바퀴 끝난 뒤 커서를 비운다."""


# ---------- 락 ----------
@contextmanager
def stage_lock(stage: str, lock_dir: Optional[Path] = None):
    d = Path(lock_dir or config.LOCK_DIR)
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"{stage}.lock", "w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


# ---------- 체크포인트 ----------
def _state(conn, stage: str) -> sqlite3.Row:
    conn.execute("INSERT OR IGNORE INTO stage_state(stage) VALUES(?)", (stage,))
    return conn.execute("SELECT * FROM stage_state WHERE stage=?", (stage,)).fetchone()


def get_cursor(conn, stage: str) -> str:
    return _state(conn, stage)["cursor"]


def set_cursor(conn, stage: str, cursor: str) -> None:
    _state(conn, stage)
    conn.execute("UPDATE stage_state SET cursor=? WHERE stage=?", (cursor, stage))


# ---------- 실패 큐 / 제한 재시도 ----------
def record_failure(conn, stage: str, ref_kind: str, ref_id: str, error: str, payload: Any = None) -> str:
    """실패를 큐에 남긴다. 시도 횟수가 MAX_ATTEMPTS에 닿으면 dead(수동 확인 대상)로 둔다. 반환: 상태."""
    now = now_iso()
    row = conn.execute("SELECT * FROM failed_queue WHERE stage=? AND ref_kind=? AND ref_id=?", (stage, ref_kind, ref_id)).fetchone()
    attempts = (row["attempts"] + 1) if row else 1
    state = "dead" if attempts >= config.MAX_ATTEMPTS else "pending"
    backoff = config.RETRY_BACKOFF_SEC[min(attempts - 1, len(config.RETRY_BACKOFF_SEC) - 1)]
    nxt = None if state == "dead" else add_seconds(now, backoff)
    conn.execute(
        """INSERT INTO failed_queue(stage,ref_kind,ref_id,payload_json,error,attempts,max_attempts,first_failed_at,last_failed_at,next_retry_at,state)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(stage,ref_kind,ref_id) DO UPDATE SET attempts=excluded.attempts, error=excluded.error,
             last_failed_at=excluded.last_failed_at, next_retry_at=excluded.next_retry_at, state=excluded.state""",
        (stage, ref_kind, ref_id, json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else "{}",
         error[:500], attempts, config.MAX_ATTEMPTS, now, now, nxt, state),
    )
    return state


def resolve_failure(conn, stage: str, ref_kind: str, ref_id: str) -> None:
    conn.execute("UPDATE failed_queue SET state='resolved', next_retry_at=NULL WHERE stage=? AND ref_kind=? AND ref_id=?", (stage, ref_kind, ref_id))


def due_failures(conn, stage: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM failed_queue WHERE stage=? AND state='pending' AND (next_retry_at IS NULL OR next_retry_at<=?) ORDER BY fq_id",
        (stage, now_iso()),
    ).fetchall()


# ---------- 실행 ----------
def _start_run(conn, stage: str, status: str = "running", cursor: str = "") -> str:
    run_id = f"{stage}-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    conn.execute("INSERT INTO job_run(run_id,stage,started_at,status,cursor_start) VALUES(?,?,?,?,?)", (run_id, stage, now_iso(), status, cursor))
    conn.commit()
    return run_id


def _finish_run(conn, ctx_or_id, stage: str, status: str, processed=0, failed=0, cursor_end="", detail=None) -> None:
    run_id = ctx_or_id
    conn.execute("UPDATE job_run SET finished_at=?, status=?, processed=?, failed=?, cursor_end=?, detail_json=? WHERE run_id=?",
                 (now_iso(), status, processed, failed, cursor_end, json.dumps(detail or {}, ensure_ascii=False, default=str), run_id))
    st = _state(conn, stage)
    fails = 0 if status in ("ok", "partial") else (st["consecutive_failures"] + (1 if status == "failed" else 0))
    conn.execute("UPDATE stage_state SET last_status=?, last_run_id=?, last_finished_at=?, consecutive_failures=?, lease_owner=NULL, lease_expires_at=NULL WHERE stage=?",
                 (status, run_id, now_iso(), fails, stage))
    conn.commit()


def run_stage(stage: Stage, conn: sqlite3.Connection, max_batches: int = 1000, lock_dir: Optional[Path] = None) -> Dict[str, Any]:
    name = stage.name
    caps = dict(config.STAGE_DAILY_CAPS.get(name, {}))
    with stage_lock(name, lock_dir) as got:
        if not got:
            rid = _start_run(conn, name, "running")
            _finish_run(conn, rid, name, "skipped_locked")
            return {"stage": name, "status": "skipped_locked"}
        st = _state(conn, name)
        if st["paused"]:
            rid = _start_run(conn, name)
            _finish_run(conn, rid, name, "skipped_paused", cursor_end=st["cursor"])
            return {"stage": name, "status": "skipped_paused"}
        cursor0 = st["cursor"]
        rid = _start_run(conn, name, cursor=cursor0)
        conn.execute("UPDATE stage_state SET last_started_at=?, lease_owner=?, lease_expires_at=? WHERE stage=?",
                     (now_iso(), f"{socket.gethostname()}:{os.getpid()}", add_seconds(now_iso(), config.LEASE_SEC), name))
        conn.commit()
        ctx = Context(conn, name, rid, caps)
        status = "ok"
        try:
            if stage.custom:
                ctx.detail.update(stage.run_custom(ctx) or {})
                conn.commit()
            else:
                _run_batches(stage, ctx, max_batches)
            if ctx.failed:
                status = "partial"
            if ctx.detail.get("budget_exhausted"):
                status = "ok" if ctx.processed else "skipped_budget"
        except Exception as e:  # noqa: BLE001  단계 전체가 죽은 경우: 커서는 마지막 커밋 지점에 남아 있다
            conn.rollback()
            ctx.detail["fatal"] = f"{type(e).__name__}: {e}"
            ctx.detail["trace"] = traceback.format_exc()[-600:]
            status = "failed"
        _finish_run(conn, rid, name, status, ctx.processed, ctx.failed, get_cursor(conn, name), ctx.detail)
        return {"stage": name, "status": status, "processed": ctx.processed, "failed": ctx.failed, "detail": ctx.detail}


def _handle_one(stage: Stage, ctx: Context, item: Item, is_retry: bool = False) -> bool:
    conn = ctx.conn
    if not is_retry:
        fq = conn.execute("SELECT state, next_retry_at FROM failed_queue WHERE stage=? AND ref_kind='item' AND ref_id=?", (stage.name, item.ref_id)).fetchone()
        if fq and (fq["state"] == "dead" or (fq["state"] == "pending" and (fq["next_retry_at"] or "") > now_iso())):
            ctx.detail["skipped_in_failed_queue"] = ctx.detail.get("skipped_in_failed_queue", 0) + 1
            return True  # dead 는 수동 확인 전까지, pending 은 재시도 시각 전까지 건너뜀
    if stage.budget_metric and not ctx.consume(stage.budget_metric):
        ctx.detail["budget_exhausted"] = stage.budget_metric
        return False
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT item")
    try:
        stage.handle(ctx, item)
        conn.execute("RELEASE item")
        ctx.processed += 1
        if is_retry:
            resolve_failure(conn, stage.name, "item", item.ref_id)
    except BudgetExceeded:
        conn.execute("ROLLBACK TO item")
        conn.execute("RELEASE item")
        ctx.detail["budget_exhausted"] = stage.budget_metric or "rows"
        return False
    except Exception as e:  # noqa: BLE001
        conn.execute("ROLLBACK TO item")
        conn.execute("RELEASE item")
        ctx.failed += 1
        record_failure(conn, stage.name, "item", item.ref_id, f"{type(e).__name__}: {e}")
    return True


def _run_batches(stage: Stage, ctx: Context, max_batches: int) -> None:
    conn = ctx.conn
    # 1) 실패 큐에서 재시도 시점이 된 항목 먼저
    for fq in due_failures(conn, stage.name):
        it = stage.load_ref(ctx, fq["ref_id"])
        if it is None:
            continue
        if not _handle_one(stage, ctx, it, is_retry=True):
            conn.commit()
            return
    conn.commit()
    # 2) 커서 다음부터 배치 처리. 커서는 배치를 커밋한 뒤에만 전진한다.
    for _ in range(max_batches):
        cursor = get_cursor(conn, stage.name)
        batch = stage.next_batch(ctx, cursor)
        if not batch:
            stage.on_cycle_complete(ctx)
            set_cursor(conn, stage.name, "")
            conn.commit()
            return
        last_key = cursor
        for it in batch:
            if not _handle_one(stage, ctx, it):
                set_cursor(conn, stage.name, last_key)  # 상한 도달: 처리한 곳까지만 커서 전진
                conn.commit()
                return
            last_key = it.key
        set_cursor(conn, stage.name, last_key)
        conn.commit()
