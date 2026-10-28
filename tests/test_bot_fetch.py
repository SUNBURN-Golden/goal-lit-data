import json
import subprocess
from pathlib import Path

import pytest

from glkg import config, db
from glkg.bot import http as H, extractors as X, parser
from glkg.bot.budget import Budget
from glkg.bot.collect import collect
from glkg.bot.executor import BudgetExhausted, FetchExecutor
from glkg.registry import Registry
from tests.bot_helpers import *
from tests.helpers import ROBOTS


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    kg = db.open_kg(tmp_path / "kg.db")
    reg = Registry(robots_path=ROBOTS)
    tr = FakeTransport({"https://api.openalex.org/robots.txt": ROBOTS_OK, "https://openlibrary.org/robots.txt": ROBOTS_OK, "https://ok-news.test/robots.txt": ROBOTS_OK})

    def mk(budget=None, renderer=None):
        return FetchExecutor(kg, reg, tr, no_sleep_clock(tmp_path), budget or Budget(kg), tmp_path / "raw", renderer)
    return kg, reg, tr, mk, tmp_path


def test_robots_ai_block_and_all_block_are_not_fetched(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/robots.txt"] = ROBOTS_BLOCK_AI
    r = mk().fetch("https://ok-news.test/a/1", "discovery")
    assert r.outcome == "failed_robots" and "ai:GPTBot" in r.detail
    tr.routes["https://ok-news.test/robots.txt"] = ROBOTS_BLOCK_ALL
    ex = mk()
    assert ex.fetch("https://ok-news.test/a/1", "discovery").outcome == "failed_robots"
    assert "https://ok-news.test/a/1" not in tr.calls


def test_robots_unreadable_means_no_collection(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/robots.txt"] = (503, "text/plain", "x")
    assert mk().fetch("https://ok-news.test/a/1", "discovery").outcome == "failed_robots"
    tr.routes["https://ok-news.test/robots.txt"] = ConnectionError("down")
    assert mk().fetch("https://ok-news.test/a/2", "discovery").outcome == "failed_robots"


def test_blocked_hosts_and_unknown_hosts_policy(env):
    kg, reg, tr, mk, _ = env
    r = mk().fetch("https://www.instagram.com/p/xyz/", "discovery")
    assert r.outcome == "failed_policy"
    r2 = mk().fetch("https://never-seen.test/x", "discovery")        # 시험 수집 아님 → 정책상 불가
    assert r2.outcome == "failed_policy"
    assert not any("instagram" in c or "never-seen" in c for c in tr.calls)


def test_login_empty_and_forbidden_are_failures(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/l"] = (200, "text/html", LOGIN_HTML, "https://ok-news.test/login?next=1")
    tr.routes["https://ok-news.test/e"] = (200, "text/html", EMPTY_HTML)
    tr.routes["https://ok-news.test/z"] = (200, "text/html", b"")
    tr.routes["https://ok-news.test/f"] = (403, "text/html", "no")
    tr.routes["https://ok-news.test/t"] = (429, "text/html", "slow")
    ex = mk()
    got = {p: ex.fetch("https://ok-news.test/" + p, "discovery").outcome for p in "lezft"}
    assert got == {"l": "failed_login", "e": "failed_empty", "z": "failed_empty", "f": "failed_inaccessible", "t": "failed_rate_limit"}
    assert kg.execute("SELECT count(*) FROM raw_document").fetchone()[0] == 0   # 실패는 문서로 저장하지 않음
    assert kg.execute("SELECT detail FROM fetch_log WHERE url LIKE '%/e'").fetchone()[0] == "needs_browser"


def test_browser_only_for_js_pages_not_for_blocks(env):
    kg, reg, tr, mk, _ = env
    rendered = []
    tr.routes["https://ok-news.test/e"] = (200, "text/html", EMPTY_HTML)
    tr.routes["https://ok-news.test/f"] = (403, "text/html", "no")
    ex = mk(renderer=lambda u: rendered.append(u) or BOOK_HTML)
    assert ex.fetch("https://ok-news.test/f", "discovery").outcome == "failed_inaccessible"
    assert rendered == []                                    # 403 은 브라우저로 우회하지 않는다
    r = ex.fetch("https://ok-news.test/e", "discovery")
    assert r.outcome == "ok" and r.method == "browser" and rendered == ["https://ok-news.test/e"]


def test_hash_dedup_unchanged_and_change_creates_new_version(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/b"] = (200, "text/html", BOOK_HTML)
    ex = mk()
    a, _s = collect(kg, ex, "https://ok-news.test/b?utm_source=x", "discovery")
    b, _s = collect(kg, ex, "https://ok-news.test/b", "discovery")
    assert a.is_new and b.outcome == "unchanged" and a.doc_id == b.doc_id
    assert kg.execute("SELECT count(*) FROM raw_document").fetchone()[0] == 1
    tr.routes["https://ok-news.test/b"] = (200, "text/html", BOOK_HTML.replace("2014", "2015"))
    c, _s = collect(kg, ex, "https://ok-news.test/b", "discovery")
    assert c.is_new and c.doc_id != a.doc_id
    assert kg.execute("SELECT count(*) FROM raw_document").fetchone()[0] == 2
    d = kg.execute("SELECT content_hash, fetched_at, parsed_version FROM raw_document WHERE doc_id=?", (a.doc_id,)).fetchone()
    assert len(d["content_hash"]) == 64 and d["fetched_at"] and d["parsed_version"]


def test_budget_classes_are_separate_and_capped(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/b"] = (200, "text/html", BOOK_HTML)
    bud = Budget(kg, split={"backfill": 3, "discovery": 1, "refresh": 0}, fetch_cap=4)
    ex = mk(bud)
    with pytest.raises(BudgetExhausted):          # discovery 1건 = robots 확인으로 소진 → 본 요청 불가
        ex.fetch("https://ok-news.test/b", "discovery")
    assert tr.calls == ["https://ok-news.test/robots.txt"]
    with pytest.raises(BudgetExhausted):
        ex.fetch("https://ok-news.test/b", "refresh")
    r = ex.fetch("https://ok-news.test/b", "backfill")          # robots 는 캐시 → backfill 예산 1건
    assert r.outcome == "ok" and bud.used("backfill") == 1 and bud.used("refresh") == 0
    assert bud.total_used() == 2


def test_domain_interval_enforced(tmp_path):
    t = [1000.0]
    slept = []
    clk = H.DomainClock(lock_dir=tmp_path, interval=5.0, sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), now=lambda: t[0])
    f = clk.acquire("a.test"); clk.release(f)
    t[0] += 2
    f = clk.acquire("a.test"); clk.release(f)
    assert slept and abs(slept[-1] - 3.0) < 1e-6
    f = clk.acquire("b.test"); clk.release(f)       # 다른 도메인은 기다리지 않음
    assert len(slept) == 1


def test_ol_subject_promotes_new_works_as_source_claims_only(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    ex = mk()
    res, st = collect(kg, ex, "https://openlibrary.org/subjects/korean_fiction.json?limit=3", "discovery")
    assert st["new_works"] == 1 and st["new_candidates"] == 1           # 영문 제목은 후보에서 제외, 작가 없는 건 후보로만
    w = kg.execute("SELECT * FROM work").fetchall()
    assert [x["canonical_title"] for x in w] == ["가상의 바다"]
    wid = w[0]["work_id"]
    facts = {r["predicate"]: r["value_text"] for r in kg.execute("SELECT predicate,value_text FROM bib_fact WHERE subject_id=?", (wid,))}
    assert facts == {"pub_year": "2011"}
    assert kg.execute("SELECT entity_id FROM external_id WHERE scheme='openlibrary_work' AND value='/works/OL1W'").fetchone()[0] == wid
    assert kg.execute("SELECT count(*) FROM bib_fact WHERE epistemic_status!='source_claim'").fetchone()[0] == 0
    assert kg.execute("SELECT count(*) FROM extraction WHERE field='skipped_non_korean_title' AND value='1'").fetchone()[0] == 1
    n = lambda: [kg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("work", "bib_fact", "edition", "work_author", "work_candidate", "extraction", "external_id")]
    before = n()
    res2, _ = collect(kg, ex, "https://openlibrary.org/subjects/korean_fiction.json?limit=3", "discovery")
    assert res2.outcome == "unchanged" and n() == before          # 같은 응답을 다시 → 중복 없음


def test_ol_editions_korean_only_for_required_fields_translations_labeled(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    tr.routes["https://openlibrary.org/works/OL1W/editions.json"] = (200, "application/json", OL_EDITIONS)
    ex = mk()
    collect(kg, ex, "https://openlibrary.org/subjects/korean_fiction.json?limit=3", "discovery")
    res, st = collect(kg, ex, "https://openlibrary.org/works/OL1W/editions.json?limit=50", "backfill")
    eds = {r["isbn"]: r["label"] for r in kg.execute("SELECT isbn,label FROM edition")}
    assert eds == {"9788954632706": "ko", "9780306406157": "translation:eng", "9788936472559": "ko"}
    pubs = sorted(r["value_text"] for r in kg.execute("SELECT value_text FROM bib_fact WHERE subject_type='edition' AND predicate='publisher'"))
    assert pubs == ["가상출판사"]                               # 'Unknown' 출판사는 값으로 취급하지 않음, 영문판 출판사는 한국어 필수 필드에 세지 않음


def test_conflicting_year_is_kept_as_conflict_not_overwritten(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT)
    ex = mk()
    collect(kg, ex, "https://openlibrary.org/subjects/korean_fiction.json?limit=3", "discovery")
    tr.routes["https://openlibrary.org/subjects/"] = (200, "application/json", OL_SUBJECT.replace("2011", "2012"))
    collect(kg, ex, "https://openlibrary.org/subjects/korean_poetry.json?limit=3", "discovery")
    years = sorted(r[0] for r in kg.execute("SELECT value_text FROM bib_fact WHERE predicate='pub_year'"))
    assert years == ["2011", "2012"]                      # 둘 다 남음(덮어쓰기 없음)
    assert kg.execute("SELECT count(*) FROM conflict WHERE status='open'").fetchone()[0] == 1
    assert kg.execute("SELECT count(*) FROM review_queue WHERE kind='source_conflict'").fetchone()[0] == 1


def test_openalex_labels_critic_and_references_and_keeps_independent(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://api.openalex.org/works"] = (200, "application/json", OPENALEX)
    res, st = collect(kg, mk(), "https://api.openalex.org/works?search=x", "discovery")
    labs = {r[0]: r[1] for r in kg.execute("SELECT label, origin_class FROM doc_label")}
    assert labs == {"비평": "independent", "참고문헌": "independent"}
    assert kg.execute("SELECT count(*) FROM extraction WHERE field='cites'").fetchone()[0] == 2
    assert kg.execute("SELECT count(*) FROM work").fetchone()[0] == 0          # 논문은 작품을 만들지 않음
    assert kg.execute("SELECT count(*) FROM extraction WHERE field='abstract_present'").fetchone()[0] == 1
    assert not kg.execute("SELECT 1 FROM extraction WHERE length(value) > 300").fetchone()


def test_html_book_page_and_link_only_hosts_do_not_store_body(env):
    kg, reg, tr, mk, tmp = env
    tr.routes["https://ok-news.test/b"] = (200, "text/html", BOOK_HTML)
    res, st = collect(kg, mk(), "https://ok-news.test/b", "backfill")
    d = kg.execute("SELECT * FROM raw_document").fetchone()
    assert d["body_stored"] == 1 and Path(d["storage_path"]).exists() and str(config.REPO_DIR) not in d["storage_path"]
    assert st["new_works"] == 1
    assert {r[0] for r in kg.execute("SELECT field FROM extraction")} >= {"title", "author", "publisher", "isbn", "pub_year", "pub_date", "text_hash"}
    # 기록 후 등록된 후보 출처(link_only)는 본문을 저장하지 않는다
    kg.execute("INSERT INTO candidate_source(domain,state,usage_basis,first_seen_at) VALUES('lo.test','registered','link_only','2026-10-27T00:00:00+09:00')")
    tr.routes["https://lo.test/robots.txt"] = ROBOTS_OK
    tr.routes["https://lo.test/p"] = (200, "text/html", BOOK_HTML.replace("가상의 산", "다른 책"))
    r = mk().fetch("https://lo.test/p", "discovery")
    assert r.outcome == "ok"
    row = kg.execute("SELECT body_stored, storage_path FROM raw_document WHERE doc_id=?", (r.doc_id,)).fetchone()
    assert row["body_stored"] == 0 and row["storage_path"] is None


def test_extractors_isbn_and_feeds():
    assert X.isbn_ok("978-89-546-3270-6") == "9788954632706"
    assert X.isbn_ok("978-89-546-3270-5") == ""
    assert X.find_isbns("ISBN 978-89-546-3270-6 / x 0306406152") == ["9788954632706", "0306406152"]
    sm = b"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>https://a.test/1</loc></url><url><loc>https://a.test/2</loc></url></urlset>"
    rss = b"<rss><channel><item><link>https://a.test/3</link></item></channel></rss>"
    assert X.feed_links(sm) == ["https://a.test/1", "https://a.test/2"] and X.feed_links(rss) == ["https://a.test/3"]


def _text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)


def _img_with_text(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    import io
    im = Image.new("RGB", (900, 140), "white")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
    except OSError:
        f = ImageFont.load_default()
    d.text((20, 40), text, fill="black", font=f)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def test_pdf_text_page_and_ocr_pages_and_image_ocr(tmp_path):
    f = X.pdf_fields(_text_pdf("ISBN 978-89-546-3270-6 doi 10.1000/abc.1"))
    kinds = {(a, c) for a, b, _l, c in f}
    assert ("isbn", "pattern") in kinds and ("doi", "pattern") in kinds and any(a == "pdf_pages" and b == "1" for a, b, _l, _c in f)
    assert not any(c == "ocr" for _a, _b, _l, c in f)
    png = _img_with_text("ISBN 978-89-546-3270-6")
    fi = X.image_fields(png)
    assert any(a == "isbn" and b == "9788954632706" and c == "ocr" for a, b, _l, c in fi)
    import io
    from PIL import Image
    pdf = io.BytesIO(); Image.open(io.BytesIO(png)).convert("RGB").save(pdf, "PDF")      # 텍스트층 없는 스캔 PDF
    fp = X.pdf_fields(pdf.getvalue())
    assert any(a == "isbn" and c == "ocr" and l == "page 1" for a, b, l, c in fp) and any(a == "ocr_pages" for a, *_ in fp)


def test_pdf_route_through_executor_records_fields(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/d.pdf"] = (200, "application/pdf", _text_pdf("ISBN 978-89-546-3270-6"))
    r, _ = collect(kg, mk(), "https://ok-news.test/d.pdf", "backfill")
    assert r.method == "pdf"
    assert kg.execute("SELECT count(*) FROM extraction WHERE field='isbn' AND value='9788954632706'").fetchone()[0] == 1


def test_crawl_delay_widens_domain_interval(env):
    kg, reg, tr, mk, _ = env
    tr.routes["https://ok-news.test/robots.txt"] = (200, "text/plain", "User-agent: *bot\nCrawl-delay: 10\n\nUser-agent: *\nAllow: /\nSitemap: https://ok-news.test/sm.xml\n")
    ex = mk()
    ok, _ = ex.gate.allowed("https://ok-news.test/a")
    assert ok and ex.clock.per_domain["ok-news.test"] == 10.0
    assert ex.gate.sitemaps["ok-news.test"] == ["https://ok-news.test/sm.xml"]
    assert H.crawl_delay_for("User-agent: ClaudeBot\nCrawl-delay: 10\n") == 0.0
