import json

import pytest

from glkg import config, db
from glkg.bot import evaluator as EV, followups, jobs, queries as Q
from glkg.bot.budget import Budget
from glkg.bot.executor import FetchExecutor
from glkg.pipeline import run_stage
from glkg.registry import Registry
from glkg.stages.assess import AssessStage
from glkg.stages.harvest import HarvestStage
from glkg.stages.seek import SeekStage
from tests.bot_helpers import *
from tests.helpers import ROBOTS

PAPER_HTML = """<html><head><title>t</title>
<meta name="citation_title" content="가상 소설의 서사 구조 연구"/><meta name="citation_author" content="가상연구자"/>
<meta name="citation_publication_date" content="2019/05/01"/><meta name="citation_journal_title" content="가상문학연구"/>
<meta name="citation_doi" content="10.1000/gs.9"/></head><body><p>%s</p></body></html>""" % ("서사와 소설에 대한 비평 문단입니다. " * 30)
JUNK_HTML = "<html><head><title>쇼핑몰 특가</title></head><body><p>%s</p></body></html>" % ("할인 쿠폰 이벤트 " * 40)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    kg = db.open_kg(tmp_path / "kg.db")
    reg = Registry(robots_path=ROBOTS)
    tr = FakeTransport({"https://api.openalex.org/robots.txt": ROBOTS_OK, "https://openlibrary.org/robots.txt": ROBOTS_OK,
                        "https://api.crossref.org/robots.txt": ROBOTS_OK, "https://journal.test/robots.txt": ROBOTS_OK, "https://junk.test/robots.txt": ROBOTS_OK})

    def mk(budget=None):
        return FetchExecutor(kg, reg, tr, no_sleep_clock(tmp_path), budget or Budget(kg), tmp_path / "raw")
    return kg, reg, tr, mk, tmp_path


def harvest(kg, reg, ex, tmp, max_jobs=25):
    return run_stage(HarvestStage(executor=ex, max_jobs=max_jobs, registry=reg), kg, lock_dir=tmp / "locks")


def count(kg, sql, *a):
    return kg.execute(sql, a).fetchone()[0]


def test_seek_makes_combos_once_and_respects_discovered_cap(env):
    kg, reg, tr, mk, tmp = env
    st = SeekStage(seed_per_run=6, years=[2020, 2019])
    run_stage(st, kg, lock_dir=tmp / "locks")
    n1 = count(kg, "SELECT count(*) FROM discovery_query")
    kinds = {r[0] for r in kg.execute("SELECT DISTINCT kind FROM discovery_query")}
    assert n1 == 6 and "ol_subject" in kinds
    run_stage(SeekStage(seed_per_run=50, years=[2020, 2019]), kg, lock_dir=tmp / "locks")
    # 같은 조합을 다시 만들지 않는다: 2년 × (3 장르 + 4 키워드) = 14
    assert count(kg, "SELECT count(*) FROM discovery_query") == 14
    run_stage(SeekStage(seed_per_run=50, years=[2020, 2019]), kg, lock_dir=tmp / "locks")
    assert count(kg, "SELECT count(*) FROM discovery_query") == 14
    # 하루 후보 상한
    b = Budget(kg, discovered_cap=3)
    kg.execute("DELETE FROM daily_usage"); kg.commit()
    taken = sum(1 for i in range(10) if Q.enqueue_query(kg, b, "oa_combo", f"https://api.openalex.org/works?search=q{i}", "q", "discovery"))
    assert taken == 3


def test_harvest_ol_subject_creates_works_then_editions_with_followups(env):
    kg, reg, tr, mk, tmp = env
    url = Q.ol_subject_url("korean_fiction", 2011)
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    tr.routes["https://openlibrary.org/works/OL1W/editions.json"] = (200, "application/json", OL_EDITIONS)
    Q.enqueue_query(kg, None, "ol_subject", url, "t", "discovery", 50)
    kg.commit()
    r = harvest(kg, reg, mk(), tmp)
    assert r["status"] == "ok"
    assert count(kg, "SELECT count(*) FROM work") == 1                      # 저자 있는 한글 제목 1건만 승격
    assert count(kg, "SELECT count(*) FROM work_candidate") >= 1             # 저자 없는 건 후보로 남음
    # 작품 → 판본 질의가 backfill 로 생겼고 같은 실행에서 처리됨
    assert count(kg, "SELECT count(*) FROM discovery_query WHERE kind='ol_editions' AND state='done'") == 1
    assert count(kg, "SELECT count(*) FROM edition") >= 1
    b = Budget(kg).snapshot()
    assert b["requests"]["backfill"].split("/")[0] != "0" and b["requests"]["discovery"].split("/")[0] != "0"


def test_rerun_requeued_jobs_makes_no_duplicates(env):
    kg, reg, tr, mk, tmp = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    tr.routes["https://openlibrary.org/works/OL1W/editions.json"] = (200, "application/json", OL_EDITIONS)
    Q.enqueue_query(kg, None, "ol_subject", Q.ol_subject_url("korean_fiction", 2011), "t", "discovery", 50)
    kg.commit()
    harvest(kg, reg, mk(), tmp)
    snap = [count(kg, f"SELECT count(*) FROM {t}") for t in ("work", "edition", "bib_fact", "raw_document", "extraction", "work_candidate", "external_id")]
    kg.execute("UPDATE discovery_query SET state='queued'"); kg.commit()
    harvest(kg, reg, mk(), tmp)
    assert snap == [count(kg, f"SELECT count(*) FROM {t}") for t in ("work", "edition", "bib_fact", "raw_document", "extraction", "work_candidate", "external_id")]
    assert count(kg, "SELECT count(*) FROM fetch_log WHERE outcome='unchanged'") >= 2


def test_resume_after_crash_mid_job_leaves_job_queued_and_no_partial_rows(env, monkeypatch):
    kg, reg, tr, mk, tmp = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    Q.enqueue_query(kg, None, "ol_subject", Q.ol_subject_url("korean_fiction", 2011), "t", "discovery", 50)
    kg.commit()
    orig = followups.after_collect

    def boom(*a, **k):
        raise RuntimeError("프로세스 종료 흉내")
    monkeypatch.setattr(followups, "after_collect", boom)
    harvest(kg, reg, mk(), tmp)
    assert count(kg, "SELECT count(*) FROM discovery_query WHERE state='queued'") == 1       # 작업은 그대로 대기
    assert count(kg, "SELECT count(*) FROM work") == 0 and count(kg, "SELECT count(*) FROM raw_document") == 0   # 부분 커밋 없음
    monkeypatch.setattr(followups, "after_collect", orig)
    harvest(kg, reg, mk(), tmp)
    assert count(kg, "SELECT count(*) FROM discovery_query WHERE kind='ol_subject' AND state='done'") == 1
    assert count(kg, "SELECT count(*) FROM work") == 1


def test_budget_classes_are_separate(env):
    kg, reg, tr, mk, tmp = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    for i in range(5):
        Q.enqueue_query(kg, None, "ol_subject", Q.ol_subject_url("korean_fiction", 2000 + i), "t", "discovery", 50)
    kg.commit()
    b = Budget(kg, split={"backfill": 10, "discovery": 3, "refresh": 2})
    r = harvest(kg, reg, mk(b), tmp)
    # robots 요청도 예산을 쓴다: discovery 3건이면 robots 1 + 수집 2
    assert b.used("discovery") == 3
    assert count(kg, "SELECT count(*) FROM discovery_query WHERE state='done'") == 2
    assert b.used("backfill") == 0 or b.used("backfill") <= 10


def test_job_state_transitions_backoff_and_rate_limit(env):
    kg, reg, tr, mk, tmp = env
    a, b_, c = ("https://openlibrary.org/works/OL404W/editions.json", "https://openlibrary.org/works/OL500W/editions.json", "https://openlibrary.org/works/OL429W/editions.json")
    tr.routes[a] = (404, "text/plain", "nf")
    tr.routes[b_] = ConnectionError("down")
    tr.routes[c] = (429, "text/plain", "slow")
    for u in (a, b_, c):
        Q.enqueue_url(kg, None, u, "backfill", 0, 50)
    kg.commit()
    harvest(kg, reg, mk(), tmp)
    st = {r["url"]: r for r in kg.execute("SELECT * FROM crawl_frontier")}
    assert st[a]["state"] == "skipped"                                   # 404 는 영구 실패
    assert st[b_]["state"] == "queued" and st[b_]["attempts"] == 1 and st[b_]["next_try_at"]      # 일시 실패 백오프
    assert st[c]["state"] == "queued" and st[c]["next_try_at"] > st[b_]["next_try_at"]            # 429 는 1시간 쉼
    for _ in range(3):
        kg.execute("UPDATE crawl_frontier SET next_try_at=NULL WHERE url=?", (b_,)); kg.commit()
        harvest(kg, reg, mk(), tmp)
    assert kg.execute("SELECT state FROM crawl_frontier WHERE url=?", (b_,)).fetchone()[0] == "failed"


def test_new_domain_flow_openalex_to_candidate_to_registered_and_junk_deprioritized(env):
    kg, reg, tr, mk, tmp = env
    oa = json.loads(OPENALEX)
    oa["results"][1]["primary_location"] = {"landing_page_url": "https://junk.test/x"}
    tr.routes["https://api.openalex.org/works"] = (200, "application/json", json.dumps(oa))
    tr.routes["https://journal.test/a/1"] = (200, "text/html", PAPER_HTML)
    tr.routes["https://junk.test/x"] = (200, "text/html", JUNK_HTML)
    Q.enqueue_query(kg, None, "oa_combo", Q.oa_search_url("한국 소설 비평", 2019, True), "t", "discovery", 50)
    kg.commit()
    harvest(kg, reg, mk(), tmp)
    doms = {r["domain"]: r for r in kg.execute("SELECT * FROM candidate_source")}
    assert set(doms) == {"journal.test", "junk.test"} and all(r["in_seed"] == 0 and r["state"] == "new" for r in doms.values())
    # 시험 수집 전에는 해당 도메인에서 작품·주장이 생기지 않는다
    r = run_stage(AssessStage(executor=mk()), kg, lock_dir=tmp / "locks")
    doms = {r["domain"]: r for r in kg.execute("SELECT * FROM candidate_source")}
    assert doms["journal.test"]["state"] == "registered" and doms["journal.test"]["usage_basis"] == "metadata_only"
    assert doms["junk.test"]["state"] == "deprioritized" and "관련성" in doms["junk.test"]["decision_reason"]
    assert doms["journal.test"]["score_total"] >= EV.REGISTER_MIN_TOTAL
    # 등록 도메인은 이제 정책상 수집 가능, 후순위 도메인은 아님
    ex = mk()
    from glkg.bot.policy import effective_policy
    assert effective_policy(kg, reg, "https://journal.test/a/2")["may_collect"] is True
    assert effective_policy(kg, reg, "https://junk.test/y")["may_collect"] is False
    # 등록된 도메인의 시험 페이지는 재큐되어 같은 해시여도 다시 파싱된다
    assert count(kg, "SELECT count(*) FROM crawl_frontier WHERE url LIKE 'https://journal.test/%' AND state='queued'") == 1


def test_assess_blocked_by_robots_and_ai_block(env):
    kg, reg, tr, mk, tmp = env
    tr.routes["https://nogo.test/robots.txt"] = ROBOTS_BLOCK_AI
    tr.routes["https://nogo.test/p"] = (200, "text/html", PAPER_HTML)
    kg.execute("INSERT INTO candidate_source(domain,state,in_seed,first_seen_via,sample_url,first_seen_at) VALUES('nogo.test','new',0,'t','https://nogo.test/p','2026-10-27')")
    kg.commit()
    run_stage(AssessStage(executor=mk()), kg, lock_dir=tmp / "locks")
    row = kg.execute("SELECT state, decision_reason FROM candidate_source WHERE domain='nogo.test'").fetchone()
    assert row["state"] == "blocked" and "failed_robots" in row["decision_reason"]
    assert "https://nogo.test/p" not in tr.calls


def test_stale_done_jobs_requeue_as_refresh(env):
    kg, reg, tr, mk, tmp = env
    Q.enqueue_query(kg, None, "ol_subject", Q.ol_subject_url("korean_fiction", 2011), "t", "discovery", 50)
    kg.execute("UPDATE discovery_query SET state='done', last_run_at='2026-01-01T00:00:00+09:00'"); kg.commit()
    assert jobs.requeue_stale(kg) == 1
    r = kg.execute("SELECT budget_class,state FROM discovery_query").fetchone()
    assert (r["budget_class"], r["state"]) == ("refresh", "queued")


def test_same_body_at_two_urls_makes_two_documents_not_a_crash(env):
    kg, reg, tr, mk, tmp = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    ex = mk()
    a = ex.fetch(Q.ol_subject_url("korean_fiction", 2001), "discovery")
    b = ex.fetch(Q.ol_subject_url("korean_fiction", 2002), "discovery")
    assert a.outcome == b.outcome == "ok" and a.doc_id != b.doc_id
