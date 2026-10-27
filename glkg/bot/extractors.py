"""규칙 기반 추출기(AI 호출 없음). 출력은 (field, value, locator, method) 목록.
- 구조화 API(OpenAlex·Wikidata·Crossref)와 문서 메타(JSON-LD·citation_*·og:*)를 우선한다.
- 본문에서 값을 뽑는 건 ISBN·DOI 같은 '식별자 패턴'뿐이다(문장은 저장하지 않는다).
- 초록·본문 텍스트는 추출 결과에 넣지 않는다(존재 여부와 해시만).
- PDF는 페이지 텍스트(pdftotext), 텍스트가 없는 페이지와 이미지는 OCR(tesseract kor+eng).
"""
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import EXTRACTOR_VERSION

Field = Tuple[str, str, str, str]  # field, value, locator, method

ISBN_RE = re.compile(r"(?<![\dA-Za-z])(97[89][-\s]?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,7}[-\s]?\d|\d{9}[\dXx])(?![\dA-Za-z])")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+[^\s\"'<>.,;)]")
MAX_VALUE = 300


def isbn_ok(s: str) -> str:
    d = re.sub(r"[^0-9Xx]", "", s).upper()
    if len(d) == 13 and d[:3] in ("978", "979"):
        t = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d[:12]))
        return d if (10 - t % 10) % 10 == int(d[12]) else ""
    if len(d) == 10:
        try:
            t = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d[:9])) + (10 if d[9] == "X" else int(d[9]))
        except ValueError:
            return ""
        return d if t % 11 == 0 else ""
    return ""


def find_isbns(text: str) -> List[str]:
    out = []
    for m in ISBN_RE.finditer(text or ""):
        v = isbn_ok(m.group(1))
        if v and v not in out:
            out.append(v)
    return out


def _v(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x)).strip()[:MAX_VALUE]


def _add(out: List[Field], field: str, value: Any, locator: str, method: str = "structured") -> None:
    v = _v(value) if value not in (None, "", [], {}) else ""
    if v:
        out.append((field, v, locator, method))


# ---------- OpenAlex ----------
def openalex(doc: Dict[str, Any]) -> List[Field]:
    out: List[Field] = []
    items = doc.get("results") if isinstance(doc.get("results"), list) else [doc]
    for i, w in enumerate(items):
        loc = f"results[{i}]" if doc.get("results") is not None else "$"
        _add(out, "doc_kind", "paper", loc)
        _add(out, "title", w.get("display_name") or w.get("title"), f"{loc}.display_name")
        _add(out, "openalex_id", w.get("id"), f"{loc}.id")
        _add(out, "pub_year", w.get("publication_year"), f"{loc}.publication_year")
        _add(out, "doi", (w.get("doi") or "").replace("https://doi.org/", ""), f"{loc}.doi")
        _add(out, "language", w.get("language"), f"{loc}.language")
        for a in w.get("authorships", []) or []:
            _add(out, "paper_author", (a.get("author") or {}).get("display_name"), f"{loc}.authorships")
        src = ((w.get("primary_location") or {}).get("source") or {})
        _add(out, "venue", src.get("display_name"), f"{loc}.primary_location.source")
        _add(out, "landing_page", (w.get("primary_location") or {}).get("landing_page_url"), f"{loc}.primary_location.landing_page_url")
        oa = (w.get("best_oa_location") or {})
        _add(out, "oa_pdf_url", oa.get("pdf_url"), f"{loc}.best_oa_location.pdf_url")
        _add(out, "license", oa.get("license"), f"{loc}.best_oa_location.license")
        for r in (w.get("referenced_works") or [])[:60]:
            _add(out, "cites", r, f"{loc}.referenced_works")
        for c in (w.get("keywords") or [])[:8]:
            _add(out, "keyword", c.get("display_name") if isinstance(c, dict) else c, f"{loc}.keywords")
        _add(out, "cited_by_count", w.get("cited_by_count"), f"{loc}.cited_by_count")
        if w.get("abstract_inverted_index"):
            _add(out, "abstract_present", "1", f"{loc}.abstract_inverted_index")
    return out


# ---------- Open Library (robots 허용 경로만: /subjects/*.json, /works/*/editions.json) ----------
HANGUL = re.compile(r"[가-힣]")


def _ol_year(s: Any) -> str:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", str(s or ""))
    return m.group(1) if m else ""


def ol_subject(doc: Dict[str, Any]) -> List[Field]:
    """주제 목록: 한글 제목 작품만 후보로 삼는다(영문 제목 번역서는 원제 근거가 없어 같은 작품으로 합치지 않는다)."""
    out: List[Field] = []
    skipped = 0
    for i, w in enumerate(doc.get("works") or []):
        loc = f"works[{i}]"
        title = w.get("title") or ""
        if not HANGUL.search(title):
            skipped += 1
            continue
        _add(out, "doc_kind", "book_record", loc)
        _add(out, "title", title, f"{loc}.title")
        _add(out, "ol_work_key", w.get("key"), f"{loc}.key")
        _add(out, "pub_year", _ol_year(w.get("first_publish_year")), f"{loc}.first_publish_year")
        for a in (w.get("authors") or [])[:4]:
            _add(out, "author", a.get("name"), f"{loc}.authors")
            _add(out, "ol_author_key", a.get("key"), f"{loc}.authors")
    _add(out, "skipped_non_korean_title", skipped, "$", "pattern")
    _add(out, "total_in_subject", doc.get("work_count"), "$.work_count")
    return out


def ol_editions(doc: Dict[str, Any]) -> List[Field]:
    """한 작품의 판본 목록. 한국어 판본(languages=kor 또는 한글 제목)만 필수 필드 근거가 되고, 나머지는 번역판 표시로 남긴다."""
    out: List[Field] = []
    for i, e in enumerate(doc.get("entries") or []):
        loc = f"entries[{i}]"
        langs = [(x.get("key") or "").rsplit("/", 1)[-1] for x in (e.get("languages") or [])]
        korean = "kor" in langs or bool(HANGUL.search(e.get("title") or ""))
        _add(out, "doc_kind", "edition_record", loc)
        _add(out, "edition_lang", "kor" if korean else (langs[0] if langs else "und"), f"{loc}.languages")
        _add(out, "title", e.get("title"), f"{loc}.title")
        for k in (e.get("isbn_13") or [])[:2] + (e.get("isbn_10") or [])[:1]:
            _add(out, "isbn", isbn_ok(str(k)), f"{loc}.isbn")
        pubs = [p for p in (e.get("publishers") or []) if p and p.strip().lower() not in ("unknown", "n/a", "s.n.")]
        _add(out, "publisher", pubs[0] if pubs else "", f"{loc}.publishers")
        _add(out, "pub_year", _ol_year(e.get("publish_date")), f"{loc}.publish_date")
        if korean and e.get("translated_from"):
            _add(out, "original_title", e.get("translation_of"), f"{loc}.translation_of")
            for c in e.get("contributions") or []:
                if re.search(r"translator|옮김|역", str(c), re.I):
                    _add(out, "translator", re.sub(r"\s*\(?translator\)?", "", str(c), flags=re.I).strip(), f"{loc}.contributions")
    return out


def ol_author_works(doc: Dict[str, Any], url: str = "") -> List[Field]:
    """저자 → 다른 작품(허용 경로 /authors/OL..A/works.json). 저자명은 문서에 없어 키만 남기고, 이름은 승격 단계가 외부 식별자표로 찾는다."""
    out: List[Field] = []
    m = re.search(r"/authors/(OL\w+A)/works", url)
    url_key = "/authors/" + m.group(1) if m else ""
    skipped = 0
    for i, w in enumerate(doc.get("entries") or []):
        loc = f"entries[{i}]"
        title = w.get("title") or ""
        if not HANGUL.search(title):
            skipped += 1
            continue
        _add(out, "doc_kind", "book_record", loc)
        _add(out, "title", title, f"{loc}.title")
        _add(out, "ol_work_key", w.get("key"), f"{loc}.key")
        _add(out, "pub_year", _ol_year(w.get("first_publish_date")), f"{loc}.first_publish_date")
        keys = [((a.get("author") or {}).get("key")) for a in (w.get("authors") or [])] or [url_key]
        for k in keys[:3]:
            _add(out, "ol_author_key", k, f"{loc}.authors")
    _add(out, "skipped_non_korean_title", skipped, "$", "pattern")
    return out


# ---------- Crossref ----------
BOOK_TYPES = {"book", "monograph", "edited-book", "reference-book", "book-set", "book-series"}


def crossref(doc: Dict[str, Any]) -> List[Field]:
    out: List[Field] = []
    msg = doc.get("message") or {}
    items = msg.get("items") if isinstance(msg.get("items"), list) else [msg]
    for i, w in enumerate(items):
        loc = f"items[{i}]" if isinstance(msg.get("items"), list) else "message"
        title0 = (w.get("title") or [""])[0]
        is_book = w.get("type") in BOOK_TYPES
        if is_book and not HANGUL.search(title0):
            continue        # 한글 제목 도서만 작품 후보로(영문 번역서는 같은 작품으로 합치지 않는다)
        _add(out, "doc_kind", "book_record" if is_book else "paper", loc)
        _add(out, "title", title0, f"{loc}.title")
        _add(out, "doi", w.get("DOI"), f"{loc}.DOI")
        if is_book:
            for k in (w.get("ISBN") or [])[:3]:
                _add(out, "isbn", isbn_ok(str(k)), f"{loc}.ISBN")
        yr = ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        ed = "ed_" if (is_book and w.get("ISBN")) else ""   # ISBN 이 있는 도서의 발행 연도·출판사는 '그 판본'의 값(초판 연도와 섞어 충돌로 만들지 않는다)
        _add(out, ed + "pub_year", yr, f"{loc}.issued")
        _add(out, ed + "publisher", w.get("publisher"), f"{loc}.publisher")
        for a in w.get("author", []) or []:
            _add(out, "author" if is_book else "paper_author", " ".join(x for x in (a.get("family"), a.get("given")) if x), f"{loc}.author")
        for r in (w.get("reference") or [])[:80]:
            if r.get("DOI"):
                _add(out, "cites_doi", r["DOI"], f"{loc}.reference")
    return out


# ---------- HTML ----------
def html_meta(html: str, url: str = "") -> List[Field]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out: List[Field] = []
    meta = {}
    for m in soup.find_all("meta"):
        k = (m.get("property") or m.get("name") or "").lower()
        if k and m.get("content"):
            meta.setdefault(k, []).append(m["content"])
    for k, f in (("citation_title", "title"), ("og:title", "title"), ("og:site_name", "site_name"), ("citation_publisher", "publisher"),
                 ("citation_doi", "doi"), ("citation_isbn", "isbn"), ("citation_publication_date", "pub_date"), ("citation_date", "pub_date"),
                 ("article:published_time", "pub_date"), ("citation_journal_title", "venue"), ("og:type", "og_type"), ("citation_pdf_url", "oa_pdf_url")):
        for v in meta.get(k, [])[:1]:
            _add(out, f, isbn_ok(v) if f == "isbn" else v, f"meta[{k}]")
    for v in meta.get("citation_author", [])[:10] + meta.get("author", [])[:3]:
        _add(out, "paper_author" if "citation_author" in meta else "author", v, "meta[author]")
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        _add(out, "canonical_link", canon["href"], "link[rel=canonical]")
    for i, tag in enumerate(soup.find_all("script", type="application/ld+json")):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for node in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
            if not isinstance(node, dict):
                continue
            t = node.get("@type")
            t = t[0] if isinstance(t, list) and t else t
            loc = f"ld+json[{i}].{t}"
            if t in ("Book", "CreativeWork", "Article", "NewsArticle", "ScholarlyArticle", "BlogPosting", "Review"):
                _add(out, "doc_kind", {"Book": "book_record", "Review": "review"}.get(t, "article"), loc)
                _add(out, "title", node.get("name") or node.get("headline"), loc + ".name")
                au = node.get("author")
                for a in (au if isinstance(au, list) else [au]) if au else []:
                    _add(out, "author", a.get("name") if isinstance(a, dict) else a, loc + ".author")
                pb = node.get("publisher")
                _add(out, "publisher", pb.get("name") if isinstance(pb, dict) else pb, loc + ".publisher")
                _add(out, "isbn", isbn_ok(str(node.get("isbn", ""))) if node.get("isbn") else "", loc + ".isbn")
                dp = node.get("datePublished") or node.get("copyrightYear")
                _add(out, "pub_date", dp, loc + ".datePublished")
                if t == "Book" and re.match(r"^\d{4}", str(dp or "")):   # 도서 레코드의 발행일만 발행연도로 본다(기사 날짜는 제외)
                    _add(out, "pub_year", str(dp)[:4], loc + ".datePublished")
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    for isb in find_isbns(text)[:5]:
        _add(out, "isbn", isb, "text:isbn", "pattern")
    for m in list(DOI_RE.finditer(text))[:3]:
        _add(out, "doi", m.group(0), "text:doi", "pattern")
    seen = []
    for m in re.finditer(r"[『《]([^』》\n]{2,40})[』》]", text):   # 본문에 언급된 작품명 후보(제목 문자열만 저장, 문장은 저장하지 않음)
        t = m.group(1).strip()
        if t not in seen and HANGUL.search(t):
            seen.append(t)
    for t in seen[:10]:
        _add(out, "mention_title", t, "text:mention", "pattern")
    _add(out, "text_chars", len(re.sub(r"\s+", " ", text).strip()), "text", "pattern")
    _add(out, "text_hash", hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()[:16], "text", "pattern")
    return out


def html_links(html: str, base: str) -> List[Tuple[str, str]]:
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "lxml")
    res = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        if href.startswith("http"):
            res.append((href, (a.get_text() or "").strip()[:60]))
    return res


def feed_links(xml_bytes: bytes) -> List[str]:
    """RSS/Atom/sitemap(urlset·sitemapindex)에서 URL 목록."""
    from lxml import etree
    try:
        root = etree.fromstring(xml_bytes, parser=etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    except Exception:
        return []
    if root is None:
        return []
    urls = []
    for el in root.iter():
        tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
        if tag == "loc" and el.text:
            urls.append(el.text.strip())
        elif tag == "link":
            if el.text and el.text.strip().startswith("http"):
                urls.append(el.text.strip())
            elif el.get("href", "").startswith("http") and el.get("rel", "alternate") == "alternate":
                urls.append(el.get("href"))
    return urls


# ---------- PDF / 이미지 ----------
def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, timeout=kw.pop("timeout", 120), **kw)


def ocr_image(path: Path, lang: str = "kor+eng") -> str:
    r = _run(["tesseract", str(path), "stdout", "-l", lang, "--psm", "6"])
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def pdf_fields(data: bytes, max_ocr_pages: int = 3, max_pages: int = 40) -> List[Field]:
    """페이지별 텍스트(pdftotext) → 식별자 패턴만 추출. 텍스트가 없는 페이지는 OCR(최대 max_ocr_pages)."""
    out: List[Field] = []
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "d.pdf"
        pdf.write_bytes(data)
        info = _run(["pdfinfo", str(pdf)]).stdout.decode("utf-8", "replace")
        m = re.search(r"Pages:\s+(\d+)", info)
        pages = int(m.group(1)) if m else 0
        _add(out, "doc_kind", "pdf", "pdf")
        _add(out, "pdf_pages", pages, "pdf", "pattern")
        t = re.search(r"Title:\s+(.+)", info)
        if t and len(t.group(1).strip()) > 3:
            _add(out, "title", t.group(1).strip(), "pdfinfo.Title")
        ocr_used = 0
        for p in range(1, min(pages, max_pages) + 1):
            txt = _run(["pdftotext", "-f", str(p), "-l", str(p), "-layout", str(pdf), "-"]).stdout.decode("utf-8", "replace")
            method = "pattern"
            if len(txt.strip()) < 20 and ocr_used < max_ocr_pages:
                _run(["pdftoppm", "-f", str(p), "-l", str(p), "-r", "150", "-png", str(pdf), str(Path(td) / "pg")])
                png = next(iter(sorted(Path(td).glob("pg*.png"))), None)
                if png:
                    txt = ocr_image(png)
                    png.unlink()
                    method = "ocr"
                    ocr_used += 1
            if txt.strip():
                _add(out, "page_text_hash", hashlib.sha256(txt.encode()).hexdigest()[:16], f"page {p}", method)
                _add(out, "page_text_chars", len(txt.strip()), f"page {p}", method)
            for isb in find_isbns(txt)[:3]:
                _add(out, "isbn", isb, f"page {p}", method)
            for d in list(DOI_RE.finditer(txt))[:2]:
                _add(out, "doi", d.group(0), f"page {p}", method)
        if ocr_used:
            _add(out, "ocr_pages", ocr_used, "pdf", "ocr")
    return out


def image_fields(data: bytes) -> List[Field]:
    out: List[Field] = [("doc_kind", "image", "image", "pattern")]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "i.img"
        p.write_bytes(data)
        txt = ocr_image(p)
    if txt.strip():
        _add(out, "ocr_chars", len(txt.strip()), "image", "ocr")
        _add(out, "page_text_hash", hashlib.sha256(txt.encode()).hexdigest()[:16], "image", "ocr")
    for isb in find_isbns(txt)[:3]:
        _add(out, "isbn", isb, "image", "ocr")
    for d in list(DOI_RE.finditer(txt))[:2]:
        _add(out, "doi", d.group(0), "image", "ocr")
    return out


def extract_by_kind(kind: str, content: bytes, url: str, ctype: str) -> Tuple[str, List[Field]]:
    """(extractor 이름, 필드). kind: openalex / ol_subject / ol_editions / crossref / html / pdf / image / feed(링크만)"""
    if kind == "openalex":
        return "openalex", openalex(json.loads(content))
    if kind == "ol_subject":
        return "ol_subject", ol_subject(json.loads(content))
    if kind == "ol_editions":
        return "ol_editions", ol_editions(json.loads(content))
    if kind == "ol_author_works":
        return "ol_author_works", ol_author_works(json.loads(content), url)
    if kind == "crossref":
        return "crossref", crossref(json.loads(content))
    if kind == "pdf":
        return "pdf", pdf_fields(content)
    if kind == "image":
        return "image", image_fields(content)
    if kind == "html":
        return "html_meta", html_meta(content.decode("utf-8", "replace"), url)
    return "none", []
