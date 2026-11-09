"""verify 단계는 일일 rows 예산으로 멈추지 않는다. 다른 단계의 예산·판정 규칙은 그대로다."""
import json

import pytest

from glkg import config, db
from glkg.pipeline import Item, Stage, run_stage
from glkg.stages.assess import AssessStage
from glkg.stages.discover import DiscoverStage
from glkg.stages.extract import ExtractStage
from glkg.stages.fetch import FetchStage
from glkg.stages.verify import VerifyStage
from glkg.timeutil import today
from tests.helpers import make_inbox, row


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    c = db.open_kg(tmp_path / "kg.db")
    yield c
    c.close()


def add_claim(conn, fact_id="fact_1"):
    conn.execute(
        """INSERT INTO bib_fact(fact_id,subject_type,subject_id,predicate,value_text,collected_at,recorded_at,epistemic_status)
           VALUES(?,?,?,?,?,?,?,?)""",
        (fact_id, "work", "work-a", "수상", "가상 문학상", "2026-11-06T10:00:00+09:00",
         "2026-11-06T10:00:00+09:00", "source_claim"),
    )
    conn.commit()


def exhaust(conn, stage, metric, cap):
    conn.execute(
        "INSERT INTO daily_usage(day,stage,metric,used,cap) VALUES(?,?,?,?,?)",
        (today(), stage, metric, cap, cap),
    )
    conn.commit()


def test_verify_has_no_rows_budget():
    assert VerifyStage.budget_metric is None
    assert config.STAGE_DAILY_CAPS.get("verify") == {}


def test_verify_not_skipped_after_rows_cap_exhausted(conn):
    """운영에서 verify/rows 가 50000/50000 으로 소진된 상태를 재현한다."""
    add_claim(conn)
    exhaust(conn, "verify", "rows", 50000)

    r = run_stage(VerifyStage(), conn)

    assert r["status"] == "ok", r
    assert r["processed"] == 1
    assert "budget_exhausted" not in r["detail"]
    row = conn.execute("SELECT used FROM daily_usage WHERE stage='verify' AND metric='rows'").fetchone()
    assert row["used"] == 50000  # 검증은 더 이상 rows 예산을 쓰지 않는다
    assert conn.execute(
        "SELECT 1 FROM verification_result WHERE claim_table='bib_fact' AND claim_id='fact_1'"
    ).fetchone() is not None


def test_verify_ignores_legacy_rows_cap(conn, monkeypatch):
    """배포 config 에 예전 verify cap 이 남아 있어도 검증을 막지 않는다."""
    monkeypatch.setitem(config.STAGE_DAILY_CAPS, "verify", {"rows": 1})
    add_claim(conn)
    exhaust(conn, "verify", "rows", 1)

    r = run_stage(VerifyStage(), conn)

    assert r["status"] == "ok", r
    assert r["processed"] == 1


def test_verify_does_not_create_rows_usage(conn):
    add_claim(conn)

    r = run_stage(VerifyStage(), conn)

    assert r["status"] == "ok" and r["processed"] == 1
    assert conn.execute("SELECT 1 FROM daily_usage WHERE stage='verify'").fetchone() is None


def test_verify_verdict_rules_unchanged(conn):
    """예산 제거가 판정 완화가 아님을 확인한다: 근거·편집 검증이 없으면 확정하지 않는다."""
    add_claim(conn)

    run_stage(VerifyStage(), conn)

    row = conn.execute(
        "SELECT verdict, reasons_json FROM verification_result WHERE claim_table='bib_fact' AND claim_id='fact_1'"
    ).fetchone()
    assert row["verdict"] == "keep_claim"
    reasons = json.loads(row["reasons_json"])
    assert "no_source" in reasons
    assert "no_evidence_locator" in reasons
    assert "no_editorial_verification" in reasons


def test_other_stages_still_skip_on_their_cap(conn, monkeypatch):
    monkeypatch.setitem(config.STAGE_DAILY_CAPS, "extract", {"rows": 1})

    class Capped(Stage):
        name = "extract"
        batch_size = 1
        budget_metric = "rows"

        def next_batch(self, ctx, cursor):
            lo = int(cursor or 0)
            return [Item(key=str(i), ref_id=f"i{i}") for i in range(lo + 1, 4)]

        def handle(self, ctx, item):
            pass

    first = run_stage(Capped(), conn)
    assert first["status"] == "ok" and first["processed"] == 1

    second = run_stage(Capped(), conn)
    assert second["status"] == "skipped_budget", second
    assert second["processed"] == 0
    assert second["detail"]["budget_exhausted"] == "rows"


def test_real_extract_stage_still_skips_on_rows_cap(conn, tmp_path):
    """실제 ExtractStage 도 기존 rows 한도에서 계속 멈춘다(합성 Stage 아님)."""
    inbox = tmp_path / "inbox.db"
    make_inbox(inbox, [row(1)])
    exhaust(conn, "extract", "rows", config.STAGE_DAILY_CAPS["extract"]["rows"])

    r = run_stage(ExtractStage(inbox_path=inbox), conn)

    assert r["status"] == "skipped_budget", r
    assert r["processed"] == 0
    assert r["detail"]["budget_exhausted"] == "rows"


def test_real_fetch_stage_still_skips_on_requests_cap(conn):
    """실제 FetchStage 는 기존 requests 한도에서 runner 를 호출하지 않는다."""
    called = []

    def runner(n):
        called.append(n)
        return {"processed": n}

    exhaust(conn, "fetch", "requests", config.STAGE_DAILY_CAPS["fetch"]["requests"])

    r = run_stage(FetchStage(runner=runner, per_run=5), conn)

    assert r["status"] == "skipped_budget", r
    assert r["processed"] == 0
    assert r["detail"]["budget_exhausted"] == "requests"
    assert called == []


def test_real_discover_stage_still_skips_on_requests_cap(conn, tmp_path):
    """실제 DiscoverStage 는 기존 requests 한도에서 enqueue 하지 않는다."""
    works = tmp_path / "works.json"
    works.write_text(json.dumps({
        "work-a": {"facts": [["수상", "가상 문학상", "", "https://api.openalex.org/works/W1"]]},
    }), encoding="utf-8")
    enqueued = []
    exhaust(conn, "discover", "requests", config.STAGE_DAILY_CAPS["discover"]["requests"])

    r = run_stage(DiscoverStage(works_json=works, enqueue=lambda *a: enqueued.append(a), known_urls=set()), conn)

    assert r["status"] == "skipped_budget", r
    assert r["processed"] == 0
    assert r["detail"]["budget_exhausted"] == "requests"
    assert enqueued == []


def test_real_assess_stage_still_skips_on_discovery_budget(conn):
    """실제 AssessStage 는 bot discovery 예산이 없으면 시험 수집을 하지 않는다."""
    conn.execute(
        "INSERT INTO candidate_source(domain,state,sample_url,first_seen_at) "
        "VALUES('example.test','new','https://example.test/a','2026-11-06T00:00:00+09:00')"
    )
    conn.commit()

    class ExhaustedBudget:
        @staticmethod
        def available(cls):
            return 1

    class Executor:
        budget = ExhaustedBudget()

    r = run_stage(AssessStage(executor=Executor(), per_run=3), conn)

    assert r["status"] == "skipped_budget", r
    assert r["processed"] == 0
    assert r["detail"]["budget_exhausted"] == "discovery"
