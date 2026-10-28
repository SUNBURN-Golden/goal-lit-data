import json
from pathlib import Path

import pytest

from glkg import db, registry

FIX = Path(__file__).parent / "fixtures" / "robots_summary.json"


@pytest.fixture()
def reg():
    return registry.Registry(robots_path=FIX)


def test_every_source_declares_required_fields(reg):
    for s in reg.sources:
        for k in ("key", "name", "kind", "support_level", "domains", "access_method", "collectable_fields", "usage_basis", "unsupported_scope"):
            assert k in s, (s.get("key"), k)
        assert s["support_level"] in ("supported", "limited", "unsupported")
        assert "refresh_hours" in s and "retention_days" in s
        if s["support_level"] == "unsupported":
            assert s["collectable_fields"] == [] or s["key"].startswith("instagram_graph")
            assert s["unsupported_scope"]


def test_instagram_is_registered_but_never_auto_collected(reg):
    keys = {"instagram_graph_api", "instagram_public_pages", "manual_clip_extension"}
    assert keys <= set(reg.by_key)
    assert reg.by_key["instagram_public_pages"]["support_level"] == "unsupported"
    assert reg.by_key["instagram_graph_api"]["support_level"] == "unsupported"
    assert reg.by_key["instagram_graph_api"]["credential_status"] == "not_configured"
    assert reg.by_key["instagram_graph_api"]["requires_user_approval"] == "계정 연결"
    p = reg.policy_for_url("https://www.instagram.com/someone/p/abc/")
    assert p["may_collect"] is False and p["registry_key"] == "instagram_public_pages"


def test_no_evasion_features_declared(reg):
    text = json.dumps(reg.data, ensure_ascii=False)
    gr = reg.data["global_rules"]
    assert any("프록시" in n for n in gr["never"]) and any("계정 교체" in n for n in gr["never"])
    assert any("사람 위장" in n for n in gr["never"]) and any("robots" in n for n in gr["never"])
    assert "rotate" not in text.lower() and "stealth" not in text.lower()


def test_existing_limits_kept(reg):
    gr = reg.data["global_rules"]
    assert (gr["max_daily_discovered"], gr["max_daily_fetched"], gr["max_concurrent_per_domain"], gr["min_domain_interval_sec"], gr["auto_publish_to_site"]) == (500, 200, 1, 5, False)


def test_audit_driven_hosts(reg):
    assert reg.classify_host("ok-news.test") == "press_robots_ok"
    assert reg.classify_host("www.ok-news.test") == "press_robots_ok"
    assert reg.classify_host("open-no-rules.test") == "press_robots_ok"
    assert reg.classify_host("blocked-news.test") == "press_ai_blocked"
    assert reg.policy_for_url("https://blocked-news.test/a")["may_collect"] is False
    assert reg.classify_host("never-seen.test") == "unregistered"
    assert reg.policy_for_url("https://never-seen.test/a")["may_collect"] is False


def test_naver_blog_and_dc_blocked(reg):
    for u in ("https://blog.naver.com/x/1", "https://booking.naver.com/a", "https://gall.dcinside.com/b"):
        assert reg.policy_for_url(u)["may_collect"] is False


def test_policy_blocked_wins_over_audit_ok():
    # dbpia·www.kci 는 감사 ok 이더라도 정책 제외
    data = registry.load_json()
    audit = {"www.dbpia.co.kr": {"status": "ok"}, "www.kci.go.kr": {"status": "ok"}}
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.json"; p.write_text(_j.dumps(audit))
        r = registry.Registry(data, robots_path=p)
        assert r.policy_for_url("https://www.dbpia.co.kr/x")["may_collect"] is False
        assert r.policy_for_url("https://www.kci.go.kr/x")["may_collect"] is False


def test_sync_to_db_and_retention(tmp_path, reg):
    c = db.open_kg(tmp_path / "kg.db")
    n = registry.sync_to_db(c, reg)
    assert n == len(reg.sources)
    assert c.execute("SELECT support_level FROM source_registry WHERE registry_key='instagram_public_pages'").fetchone()[0] == "unsupported"
    assert reg.retention_until("openalex_api", "2026-10-27T10:00:00+09:00") == "2027-10-27"
    assert reg.retention_until("internal_site_records", "2026-10-27T10:00:00+09:00") is None


def test_subdomain_falls_back_to_parent_rule(reg):
    assert reg.classify_host("gall.dcinside.com") == "other_blocked_policy"
    assert reg.classify_host("m.blog.naver.com") == "naver_blog_booking"
