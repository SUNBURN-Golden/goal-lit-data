import json

import pytest

from glkg import config, db
from glkg.bot import gaps as G, queries as Q
from glkg.bot.budget import Budget
from glkg.bot.executor import FetchExecutor
from glkg.pipeline import run_stage
from glkg.registry import Registry
from glkg.stages.gaps import GapStage
from glkg.stages.harvest import HarvestStage
from tests.bot_helpers import *
from tests.helpers import ROBOTS

CROSSREF_BOOK = json.dumps({"message": {"items": [
    {"DOI": "10.1000/book1", "title": ["가상의 바다"], "type": "book", "author": [{"family": "가상", "given": "작가"}], "publisher": "가상출판사",
     "issued": {"date-parts": [[2012]]}, "ISBN": ["9788954632706"]},
    {"DOI": "10.1000/book2", "title": ["Some English Book"], "type": "book", "author": [{"family": "Kim", "given": "A"}], "publisher": "X", "issued": {"date-parts": [[2012]]}, "ISBN": []}]}})
OL_AUTHOR_WORKS = json.dumps({"size": 2, "entries": [
    {"key": "/works/OL9W", "title": "가상의 하늘", "first_publish_date": "2015", "authors": [{"author": {"key": "/authors/OL1A"}}]},
    {"key": "/works/OL8W", "title": "Skyline", "first_publish_date": "2016", "authors": [{"author": {"key": "/authors/OL1A"}}]}]})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    kg = db.open_kg(tmp_path / "kg.db")
    reg = Registry(robots_path=ROBOTS)
    tr = FakeTransport({"https://api.openalex.org/robots.txt": ROBOTS_OK, "https://openlibrary.org/robots.txt": ROBOTS_OK, "https://api.crossref.org/robots.txt": ROBOTS_OK,
                        "https://openlibrary.org/subjects/": (200, "application/json", OL_SUBJECT)})
    ex = FetchExecutor(kg, reg, tr, no_sleep_clock(tmp_path), Budget(kg), tmp_path / "raw")
    return kg, reg, tr, ex, tmp_path


def harvest(kg, reg, ex, tmp, n=40):
    return run_stage(HarvestStage(executor=ex, max_jobs=n, registry=reg), kg, lock_dir=tmp / "locks")


def gaps(kg, tmp):
    return run_stage(GapStage(), kg, lock_dir=tmp / "locks")["detail"]


def seed_work(kg, reg, ex, tmp):
    Q.enqueue_query(kg, None, "ol_subject", Q.ol_subject_url("korean_fiction", 2011), "t", "discovery", 50)
    kg.commit()
    # 주제 목록 한 건만 처리하고 멈춘다(판본 질의는 아직 큐에 둠)
    harvest(kg, reg, ex, tmp, n=1)
    return kg.execute("SELECT work_id FROM work").fetchone()[0]


def gstate(kg, wid):
    return {r["field"]: (r["state"], r["detail"]) for r in kg.execute("SELECT * FROM gap WHERE work_id=?", (wid,))}


def test_gaps_open_with_reverse_queries_and_partial_coverage(env):
    kg, reg, tr, ex, tmp = env
    wid = seed_work(kg, reg, ex, tmp)
    d = gaps(kg, tmp)
    g = gstate(kg, wid)
    assert g["publisher"][0] == "open" and g["isbn"][0] == "open"      # 아직 역검색 전
    assert "title" not in g and "author" not in g and "pub_year" not in g   # 채워진 필드는 빈칸이 아님
    assert kg.execute("SELECT count(*) FROM discovery_query WHERE kind='gap_crossref'").fetchone()[0] == 1
    assert d["coverage"]["per_field"]["title"] == "1/1" and d["coverage"]["per_field"]["isbn"] == "0/1"


def test_reverse_search_resolves_gaps_with_sourced_claims_only(env):
    kg, reg, tr, ex, tmp = env
    tr.routes["https://api.crossref.org/works"] = (200, "application/json", CROSSREF_BOOK)
    tr.routes["https://api.openalex.org/works"] = (200, "application/json", json.dumps({"results": []}))
    tr.routes["https://openlibrary.org/works/OL1W/editions.json"] = (404, "text/plain", "nf")
    wid = seed_work(kg, reg, ex, tmp)
    gaps(kg, tmp)
    harvest(kg, reg, ex, tmp)
    d = gaps(kg, tmp)
    g = gstate(kg, wid)
    assert g["isbn"][0] == "resolved" and g["publisher"][0] == "resolved"
    assert kg.execute("SELECT resolved_by FROM gap WHERE work_id=? AND field='isbn'", (wid,)).fetchone()[0].startswith("src_")
    # 영문 제목 도서는 작품이 되지 않았다
    assert kg.execute("SELECT count(*) FROM work").fetchone()[0] == 1
    # 채움은 전부 출처가 있는 source_claim, AI 추론 없음
    assert kg.execute("SELECT count(*) FROM bib_fact WHERE epistemic_status IN ('ai_inference','confirmed_fact')").fetchone()[0] == 0
    assert kg.execute("SELECT count(*) FROM bib_fact WHERE source_id IS NULL").fetchone()[0] == 0
    assert d["coverage"]["resolved_rate"] > 0 and d["coverage"]["per_field"]["isbn"] == "1/1"
    hist = kg.execute("SELECT count(*) FROM gap_history WHERE new_state='resolved'").fetchone()[0]
    assert hist >= 2
    # 같은 단계를 다시 돌려도 이력이 늘지 않는다
    n = kg.execute("SELECT count(*) FROM gap_history").fetchone()[0]
    gaps(kg, tmp); gaps(kg, tmp)
    assert kg.execute("SELECT count(*) FROM gap_history").fetchone()[0] == n


def test_unfilled_gap_is_labeled_by_evidence_not_guessed(env):
    kg, reg, tr, ex, tmp = env
    tr.routes["https://api.crossref.org/works"] = (200, "application/json", json.dumps({"message": {"items": []}}))
    tr.routes["https://api.openalex.org/works"] = (200, "application/json", json.dumps({"results": []}))
    tr.routes["https://openlibrary.org/works/OL1W/editions.json"] = (404, "text/plain", "nf")
    wid = seed_work(kg, reg, ex, tmp)
    gaps(kg, tmp)
    harvest(kg, reg, ex, tmp)
    gaps(kg, tmp)
    st, detail = gstate(kg, wid)["isbn"]
    assert st == "not_in_source" and "crossref" in detail and "nlk" in detail      # 읽을 수 있는 곳엔 없고, 접근 못 한 출처도 밝힘
    # 역검색 질의가 모두 실패하면 inaccessible
    kg.execute("UPDATE discovery_query SET state='failed' WHERE kind IN ('gap_crossref','gap_openalex')"); kg.commit()
    gaps(kg, tmp)
    assert gstate(kg, wid)["isbn"][0] == "inaccessible"
    # 본문이 있는 문서에서 못 뽑았으면 not_extracted
    kg.execute("UPDATE discovery_query SET state='done' WHERE kind IN ('gap_crossref','gap_openalex')")
    src = kg.execute("SELECT source_id, canonical_url FROM source LIMIT 1").fetchone()
    kg.execute("INSERT INTO raw_document(doc_id,url,canonical_url,domain,fetch_method,http_status,content_type,content_hash,byte_len,fetched_at,body_stored,retention_until,origin,budget_class) VALUES('doc_t','u',?, 'd','html',200,'text/html','h',1,'2026-10-27',0,'2027-01-01','t','backfill')", (src["canonical_url"],))
    kg.execute("INSERT INTO extraction(extraction_id,doc_id,extractor,extractor_version,field,value,locator,method,extracted_at) VALUES('x1','doc_t','html_meta','v','text_chars','900','text','pattern','2026-10-27')")
    kg.commit()
    gaps(kg, tmp)
    assert gstate(kg, wid)["isbn"][0] == "not_extracted"


def test_conflicting_sources_stay_conflict_and_both_values_kept(env):
    kg, reg, tr, ex, tmp = env
    wid = seed_work(kg, reg, ex, tmp)
    from glkg.bot import parser
    from glkg.bot.policy import effective_policy
    pol = effective_policy(kg, reg, "https://openlibrary.org/x")
    kg.execute("INSERT INTO source(source_id,url,canonical_url,domain,outlet,kind,access_level,is_external,usage_basis,first_seen_at,last_seen_at) VALUES('src_other','https://b.test/1','https://b.test/1','b.test','b','catalog','book_intro',1,'metadata_only','2026-10-27','2026-10-27')")
    parser.add_work_claim(kg, wid, "pub_year", "2013", "src_other", "https://b.test/1", parser.ensure_evidence(kg, "src_other", "x", "structured", "2026-10-27T00:00:00+09:00"), "2026-10-27T00:00:00+09:00", pol)
    kg.commit()
    gaps(kg, tmp)
    assert gstate(kg, wid)["pub_year"][0] == "conflict"
    vals = {r[0] for r in kg.execute("SELECT value_text FROM bib_fact WHERE subject_type='work' AND subject_id=? AND predicate='pub_year'", (wid,))}
    assert vals == {"2011", "2013"}                                         # 덮어쓰지 않음
    assert kg.execute("SELECT count(*) FROM review_queue").fetchone()[0] >= 1


def test_author_expansion_finds_other_works_by_known_author_key(env):
    kg, reg, tr, ex, tmp = env
    tr.routes["https://openlibrary.org/authors/OL1A/works.json"] = (200, "application/json", OL_AUTHOR_WORKS)
    wid = seed_work(kg, reg, ex, tmp)
    d = gaps(kg, tmp)
    assert d["author_expansions"] == 1
    harvest(kg, reg, ex, tmp)
    titles = {r[0] for r in kg.execute("SELECT canonical_title FROM work")}
    assert titles == {"가상의 바다", "가상의 하늘"}                          # 영문 제목은 제외, 저자는 출처 키로 연결
    wa = kg.execute("SELECT count(*) FROM work_author wa JOIN work w ON w.work_id=wa.work_id WHERE w.canonical_title='가상의 하늘' AND wa.role='author'").fetchone()[0]
    assert wa == 1
