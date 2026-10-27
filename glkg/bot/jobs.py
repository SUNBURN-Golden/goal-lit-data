"""작업 대기열(질의 + 수집 대기 URL) 조회·상태 전이. 예산 클래스(backfill/discovery/refresh)별로 따로 뽑는다."""
import json
from dataclasses import dataclass
from typing import Optional

from .. import config
from ..timeutil import add_seconds, now_iso

PERMANENT = {"failed_robots", "failed_policy", "failed_login", "failed_inaccessible", "failed_unsupported_type"}
TRANSIENT = {"failed_network", "failed_rate_limit", "failed_http"}
RATE_LIMIT_PAUSE = 3600


@dataclass
class Job:
    table: str          # discovery_query | crawl_frontier
    key: str
    url: str
    cls: str
    kind: str = "url"
    work_hint: str = ""
    depth: int = 0
    attempts: int = 0


def next_job(conn, cls: str) -> Optional[Job]:
    now = now_iso()
    r = conn.execute("SELECT * FROM discovery_query WHERE state='queued' AND budget_class=? ORDER BY priority DESC, created_at LIMIT 1", (cls,)).fetchone()
    # 다른 도메인이 429 로 쉬는 동안 같은 클래스의 다른 작업을 먼저 하도록 next_try_at 필터는 frontier 쪽에서만 건다
    if r:
        p = json.loads(r["params_json"])
        return Job("discovery_query", r["query_id"], p["url"], cls, r["kind"], p.get("work_hint", ""))
    f = conn.execute("SELECT * FROM crawl_frontier WHERE state='queued' AND budget_class=? AND (next_try_at IS NULL OR next_try_at<=?) ORDER BY priority DESC, created_at LIMIT 1", (cls, now)).fetchone()
    if f:
        return Job("crawl_frontier", f["url"], f["url"], cls, "url", f["work_hint"], f["depth"], f["attempts"])
    return None


def mark(conn, job: Job, outcome: str, detail: str = "", result_count: Optional[int] = None) -> None:
    now = now_iso()
    pk = "query_id" if job.table == "discovery_query" else "url"
    att = conn.execute(f"SELECT {'0' if job.table == 'discovery_query' else 'attempts'} FROM {job.table} WHERE {pk}=?", (job.key,)).fetchone()
    attempts = (att[0] if att else 0)
    if outcome in ("ok", "unchanged"):
        state, nxt = "done", None
    elif outcome in PERMANENT or (outcome == "failed_empty") or (outcome == "failed_http" and detail in ("http_404", "http_410")):
        state, nxt = "skipped", None
    else:  # 일시 실패: 60/300/1800초 백오프, 3회 후 failed. 429는 1시간 쉼
        attempts += 1
        if outcome == "failed_rate_limit":
            state, nxt = "queued", add_seconds(now, RATE_LIMIT_PAUSE)
        elif attempts >= config.MAX_ATTEMPTS:
            state, nxt = "failed", None
        else:
            state, nxt = "queued", add_seconds(now, config.RETRY_BACKOFF_SEC[min(attempts - 1, len(config.RETRY_BACKOFF_SEC) - 1)])
    if job.table == "discovery_query":
        if state == "queued":
            # 쿼리 표엔 next_try_at 이 없어 재시도 시각을 params 에 둔다(다음 pick 에서는 priority 를 낮춰 뒤로 보냄)
            conn.execute("UPDATE discovery_query SET last_run_at=?, priority=priority-5 WHERE query_id=?", (now, job.key))
        else:
            conn.execute("UPDATE discovery_query SET state=?, last_run_at=?, result_count=COALESCE(?,result_count) WHERE query_id=?", (state, now, result_count, job.key))
    else:
        conn.execute("UPDATE crawl_frontier SET state=?, attempts=?, next_try_at=?, last_outcome=? WHERE url=?",
                     (state, attempts, nxt if state == "queued" else None, outcome + (f":{detail}" if detail else ""), job.key))


def requeue_stale(conn, hours: int = 168, limit: int = 20) -> int:
    """완료된 작업 중 갱신 주기가 지난 것을 refresh 클래스로 되돌린다(내용 해시가 같으면 'unchanged' 로만 기록)."""
    cutoff = add_seconds(now_iso(), -hours * 3600)
    n = 0
    for r in conn.execute("SELECT query_id FROM discovery_query WHERE state='done' AND budget_class!='refresh' AND last_run_at < ? ORDER BY last_run_at LIMIT ?", (cutoff, limit)).fetchall():
        conn.execute("UPDATE discovery_query SET state='queued', budget_class='refresh', priority=10 WHERE query_id=?", (r["query_id"],))
        n += 1
    for r in conn.execute("SELECT url FROM crawl_frontier WHERE state='done' AND budget_class!='refresh' AND COALESCE(next_try_at,'') < ? AND created_at < ? ORDER BY created_at LIMIT ?", (cutoff, cutoff, max(0, limit - n))).fetchall():
        conn.execute("UPDATE crawl_frontier SET state='queued', budget_class='refresh', priority=10 WHERE url=?", (r["url"],))
        n += 1
    return n
