"""비식별 합성 픽스처: 실제 수집함 스키마(tests/fixtures/inbox_items.sql)에 가짜 행을 넣는다."""
import json
import sqlite3
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
ROBOTS = FIX / "robots_summary.json"


def make_inbox(path: Path, rows):
    c = sqlite3.connect(str(path))
    c.executescript((FIX / "inbox_items.sql").read_text(encoding="utf-8"))
    cols = [r[1] for r in c.execute("PRAGMA table_info(items)")]
    for r in rows:
        base = {k: "" for k in cols}
        base.update(dict(brand="goal-lit", collection_path="migrated_fact", bundle_type="research_bundle", validation_status="verified_ready",
                         is_cluster_primary=1, conflict=0, collected_at="2026-10-20T10:00:00+09:00", dedup_cluster_id="c0",
                         work_info_json="{}", edition_info_json="{}", extracted_facts_json="[]", fulltext_allowed=0))
        base.update(r)
        base.setdefault("canonical_url", base["url"])
        if not base["canonical_url"]:
            base["canonical_url"] = base["url"]
        keys = [k for k in base if k in cols]
        c.execute(f"INSERT INTO items({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [base[k] for k in keys])
    c.commit()
    c.close()


def row(i, **kw):
    d = dict(id=f"fx_{i}", url=f"https://ok-news.test/a/{i}", canonical_url=f"https://ok-news.test/a/{i}", domain="ok-news.test", title=f"T{i}",
             outlet="가상일보", target_work_slug="work-a", source_access_level="press_article", evidence_locator=f"문단 {i}")
    d.update(kw)
    return d
