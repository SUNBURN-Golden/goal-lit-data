import json

from glkg import config, db
from glkg.bot import extractors as X, gaps as G, queries as Q
from glkg.bot.budget import Budget
from glkg.bot.executor import FetchExecutor, domain_backoff, extractor_kind
from glkg.registry import Registry
from glkg.stages import improve as I
from tests.bot_helpers import *
from tests.helpers import ROBOTS

SPARQL = json.dumps({"results": {"bindings": [
    {"title": {"value": "가상의 바다"}, "isbn": {"value": "978-89-546-3270-6"}, "publisher": {"value": "가상출판사"}, "year": {"value": "2012년"}},
    {"title": {"value": "English Only"}, "isbn": {"value": "9788954632706"}}]}}).encode()
NLK_BLOCK = "User-agent: *\nDisallow: /\nAllow: /$\n"


def test_nlk_lod_extractor_and_kind():
    assert extractor_kind(Q.nlk_lod_url("가상"), "api", "application/json") == "nlk_lod"
    name, f = X.extract_by_kind("nlk_lod", SPARQL, "", "")
    vals = {(a, b) for a, b, _, _ in f}
    assert name == "nlk_lod" and ("isbn", "9788954632706") in vals and ("ed_pub_year", "2012") in vals and ("ed_publisher", "가상출판사") in vals
    assert not any(b == "English Only" for _, b in vals)


def test_sparql_literal_escaped():
    q = Q.nlk_lod_query('a"b\\c } DROP')
    assert 'FILTER(str(?title) = "a b c } DROP")' in q


def test_registry_blocks_lod_and_gaps_do_not_enqueue(tmp_path):
    reg = Registry(robots_path=ROBOTS)
    assert not reg.policy_for_url(Q.NLK_LOD)["may_collect"]
    kg = db.open_kg(tmp_path / "kg.db")
    kg.execute("INSERT INTO work(work_id,slug,canonical_title,created_at) VALUES('w1','w1','가상의 바다','2026-01-01')")
    G.explore_work(kg, Budget(kg), "w1", nlk_lod_allowed=False)
    assert kg.execute("SELECT count(*) FROM discovery_query WHERE kind='gap_nlk_lod'").fetchone()[0] == 0
    G.explore_work(kg, Budget(kg), "w1", nlk_lod_allowed=True)
    assert kg.execute("SELECT count(*) FROM discovery_query WHERE kind='gap_nlk_lod'").fetchone()[0] == 1


def test_backoff_file(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"domains": {"x.org": {"until": "2999-01-01T00:00:00+09:00"}, "y.org": {"until": "2000-01-01T00:00:00+09:00"}}}))
    assert domain_backoff("x.org", p) and not domain_backoff("y.org", p) and not domain_backoff("z.org", p)


def test_heldout_split_is_stable_and_partial():
    ids = [f"doc_{i}" for i in range(200)]
    h = [i for i in ids if I.is_heldout(i)]
    assert 0 < len(h) < 100 and h == [i for i in ids if I.is_heldout(i)]


def test_safe_changes_skip_robots_only_and_existing():
    fails = [{"domain": "a.org", "failed": 5, "attempts": 5, "outcomes": "failed_network"},
             {"domain": "b.org", "failed": 5, "attempts": 5, "outcomes": "failed_robots"}]
    c = I.safe_changes(fails, [])
    assert list(c["backoff_add"]) == ["a.org"] and c["registry_enable"] == []


def test_recheck_robots_respects_block(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    kg = db.open_kg(tmp_path / "kg.db")
    tr = FakeTransport({"https://lod.nl.go.kr/robots.txt": (200, "text/plain", NLK_BLOCK)})
    r = I.recheck_robots(kg, transport=tr, clock=no_sleep_clock(tmp_path))
    assert r["nlk_lod_sparql"] == "robots_disallows_us"
    assert not any("sparql" in u for u in getattr(tr, "calls", []))


def test_evaluate_prf(tmp_path):
    p = tmp_path / "d.bin"
    p.write_bytes(SPARQL)
    ho = {"docs": [{"doc_id": "d", "url": Q.nlk_lod_url("가상"), "fetch_method": "api", "content_type": "application/json", "storage_path": str(p),
                    "gold": {"isbn": ["9788954632706"], "publisher": ["다른출판사"]}}]}
    e = I.evaluate(ho)
    assert e["per_field"]["isbn"] == {"precision": 1.0, "recall": 1.0}
    assert e["per_field"]["publisher"]["recall"] == 0.0
