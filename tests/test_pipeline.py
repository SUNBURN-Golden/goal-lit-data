"""파이프라인 기반: 락·체크포인트·재시도·실패 큐·상한·실패 지점 재개."""
import pytest

from glkg import config, db, pipeline
from glkg.pipeline import Item, Stage, run_stage, stage_lock


class Counting(Stage):
    name = "extract"
    batch_size = 3

    def __init__(self, n=10, fail_on=(), die_on=None):
        self.n, self.fail_on, self.die_on = n, set(fail_on), die_on
        self.seen = []

    def next_batch(self, ctx, cursor):
        lo = int(cursor or 0)
        return [Item(key=str(i), ref_id=f"i{i}") for i in range(lo + 1, min(lo + self.batch_size, self.n) + 1)]

    def load_ref(self, ctx, ref_id):
        return Item(key=ref_id[1:], ref_id=ref_id)

    def handle(self, ctx, item):
        if item.key == self.die_on:
            raise KeyboardInterrupt  # 프로세스 중단 흉내(Exception 아님 → 단계 밖으로 전파)
        if int(item.key) in self.fail_on:
            raise ValueError("boom")
        self.seen.append(int(item.key))


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    c = db.open_kg(tmp_path / "kg.db")
    yield c
    c.close()


def test_runs_all_and_checkpoints(conn):
    s = Counting()
    r = run_stage(s, conn)
    assert r["status"] == "ok" and s.seen == list(range(1, 11))
    assert pipeline.get_cursor(conn, "extract") == ""  # 한 바퀴 끝나면 커서 초기화


def test_duplicate_run_lock(conn, tmp_path):
    with stage_lock("extract", config.LOCK_DIR) as got:
        assert got
        r = run_stage(Counting(), conn)
        assert r["status"] == "skipped_locked"
    assert run_stage(Counting(), conn)["status"] == "ok"


def test_failure_goes_to_queue_with_limited_retries(conn, monkeypatch):
    s = Counting(fail_on={4})
    assert run_stage(s, conn)["status"] == "partial"
    fq = conn.execute("SELECT * FROM failed_queue").fetchone()
    assert fq["attempts"] == 1 and fq["state"] == "pending"
    # 재시도 시점을 당겨서 계속 실패 → 최대 시도 후 dead
    for _ in range(5):
        conn.execute("UPDATE failed_queue SET next_retry_at='2000-01-01T00:00:00+09:00' WHERE state='pending'")
        conn.commit()
        run_stage(Counting(fail_on={4}), conn)
    fq = conn.execute("SELECT * FROM failed_queue").fetchone()
    assert fq["state"] == "dead" and fq["attempts"] == config.MAX_ATTEMPTS


def test_retry_success_resolves_failure(conn):
    run_stage(Counting(fail_on={2}), conn)
    conn.execute("UPDATE failed_queue SET next_retry_at='2000-01-01T00:00:00+09:00'")
    conn.commit()
    s = Counting()
    run_stage(s, conn)
    assert conn.execute("SELECT state FROM failed_queue").fetchone()["state"] == "resolved"


def test_resume_from_failure_point(conn):
    s1 = Counting(die_on="5")
    with pytest.raises(KeyboardInterrupt):
        run_stage(s1, conn)
    conn.rollback()
    # 배치(4~6) 시작 전 커밋 지점 = 3
    assert pipeline.get_cursor(conn, "extract") == "3"
    s2 = Counting()
    run_stage(s2, conn)
    assert s2.seen[0] == 4 and s2.seen[-1] == 10  # 1~3은 다시 하지 않는다


def test_daily_budget_caps_and_resumes(conn, monkeypatch):
    monkeypatch.setitem(config.STAGE_DAILY_CAPS, "extract", {"rows": 4})

    class B(Counting):
        budget_metric = "rows"
    s = B()
    r = run_stage(s, conn)
    assert len(s.seen) == 4 and r["status"] == "ok"
    s2 = B()
    r2 = run_stage(s2, conn)
    assert s2.seen == [] and r2["status"] == "skipped_budget"  # 오늘 상한 소진
