"""추출·라벨·승격. 문서 한 건 → extraction 행 → doc_label → (서지 레코드면) 작품 후보·주장.
주장은 전부 source_claim 으로만 들어간다(확정은 verify 단계의 규칙이 외부 출처·근거를 갖췄을 때만).
원문에 없는 관계는 만들지 않는다. 같은 필드에 다른 값이 오면 덮어쓰지 않고 conflict+검수 대기로 남긴다."""
import re
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from ..db import enqueue_review, stable_id
from ..registry import Registry
from ..timeutil import add_seconds, now_iso
from ..stages import common as C
from . import EXTRACTOR_VERSION, extractors as X, labeler
from .policy import effective_policy

GROUP_RE = re.compile(r"^(results\[\d+\]|works\[\d+\]|entries\[\d+\]|items\[\d+\])")
LISTY = {"author", "paper_author", "translator", "isbn", "cites", "cites_doi", "keyword", "title", "publisher", "pub_year", "original_title"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[『』「」《》〈〉\"'“”‘’()\[\]{}·:;,.!?\-_/\\\s]+", "", s)
    return s


def group_fields(fields: List[X.Field]) -> "OrderedDict[str, Dict[str, List[Tuple[str, str, str]]]]":
    g: "OrderedDict[str, Dict[str, list]]" = OrderedDict()
    for f, v, loc, method in fields:
        m = GROUP_RE.match(loc)
        key = m.group(1) if m else "$"
        g.setdefault(key, {}).setdefault(f, [])
        if v not in [x[0] for x in g[key][f]]:
            g[key][f].append((v, loc, method))
    return g


def _vals(grp, f):
    return [x[0] for x in grp.get(f, [])]


def entity_url(grp, fallback: str) -> str:
    for f, pat in (("ol_work_key", "https://openlibrary.org{}"), ("openalex_id", "{}"), ("doi", "https://doi.org/{}")):
        v = _vals(grp, f)
        if v:
            return pat.format(v[0])
    return fallback


def ensure_source(conn, url: str, canon: str, domain: str, pol: Dict[str, Any], kind: str, now: str) -> str:
    sid = stable_id("src", canon)
    if conn.execute("SELECT 1 FROM source WHERE source_id=?", (sid,)).fetchone():
        conn.execute("UPDATE source SET last_seen_at=? WHERE source_id=?", (now, sid))
        return sid
    skind = {"book_record": "catalog", "paper": "paper"}.get(kind, "article")
    acc = {"book_record": "book_intro", "paper": "paper_abstract"}.get(kind, pol.get("default_access_level") or "press_article")
    rk = pol["registry_key"] if conn.execute("SELECT 1 FROM source_registry WHERE registry_key=?", (pol["registry_key"],)).fetchone() else None
    days = pol.get("retention_days") or 365
    conn.execute(
        """INSERT INTO source(source_id,registry_key,url,canonical_url,domain,outlet,kind,access_level,is_external,usage_basis,retention_until,published_at,first_seen_at,last_seen_at)
           VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
        (sid, rk, url, canon, domain, domain, skind, acc, pol["usage_basis"], add_seconds(now, days * 86400)[:10], "", now, now))
    return sid


def ensure_evidence(conn, source_id: str, locator: str, method: str, now: str) -> str:
    eid = stable_id("ev", source_id, locator)
    lt = "page" if locator.startswith(("page ", "doc:page")) else "api_field" if method == "structured" and "meta[" not in locator and "ld+json" not in locator else "dom_path"
    if method == "ocr":
        lt = "section"
    conn.execute("INSERT OR IGNORE INTO evidence_passage(evidence_id,source_id,locator,locator_type,locator_quality,quote_snippet,collected_at) VALUES(?,?,?,?,?,?,?)",
                 (eid, source_id, locator, lt, "precise", "", now))
    return eid


def ensure_author(conn, name: str, now: str) -> str:
    aid = stable_id("au", name)
    conn.execute("INSERT OR IGNORE INTO author(author_id,canonical_name,created_at) VALUES(?,?,?)", (aid, name, now))
    return aid


# ---------- 작품 찾기·후보 ----------
def find_work(conn, title: str, authors: List[str], isbns: List[str]) -> Tuple[Optional[str], str]:
    for i in isbns:
        r = conn.execute("SELECT work_id FROM edition WHERE isbn=? AND recorded_to IS NULL LIMIT 1", (i,)).fetchone()
        if r:
            return r["work_id"], "isbn"
    nt = norm(title)
    if not nt:
        return None, ""
    cands = [r["work_id"] for r in conn.execute("SELECT work_id, canonical_title FROM work") if norm(r["canonical_title"]) == nt]
    cands += [r["entity_id"] for r in conn.execute("SELECT entity_id, alias FROM entity_alias WHERE entity_type='work'") if norm(r["alias"]) == nt and r["entity_id"] not in cands]
    for wid in cands:
        known = {norm(r["canonical_name"]) for r in conn.execute(
            "SELECT a.canonical_name FROM work_author wa JOIN author a USING(author_id) WHERE wa.work_id=? AND wa.recorded_to IS NULL", (wid,))}
        if not known or not authors or known & {norm(a) for a in authors}:
            return wid, "title" + ("+author" if known and authors else "")
    return None, ""


def _claim_common(sid, url, evid, now, pol):
    return dict(source_id=sid, source_url=url, evidence_id=evid, collected_at=now, recorded_at=now, epistemic_status="source_claim",
                usage_basis=pol["usage_basis"], note="bot")


def add_work_claim(conn, work_id: str, pred: str, value: str, sid: str, url: str, evid: str, now: str, pol) -> str:
    """같은 술어에 다른 값이 이미 있으면 새 값도 남기되 conflict 로 표시(덮어쓰지 않음). 반환: new|same|conflict|dup"""
    fid = stable_id("bf", work_id, pred, norm(value), sid)
    existing = conn.execute("SELECT fact_id, value_text FROM bib_fact WHERE subject_type='work' AND subject_id=? AND predicate=? AND recorded_to IS NULL", (work_id, pred)).fetchall()
    others = [e for e in existing if norm(e["value_text"]) != norm(value)]
    ins = C.insert_claim(conn, "bib_fact", dict(fact_id=fid, subject_type="work", subject_id=work_id, predicate=pred, value_text=value, **_claim_common(sid, url, evid, now, pol)))
    if not ins:
        return "dup"
    if others and pred in ("pub_year", "publisher", "original_title"):
        for o in others:
            cid = stable_id("cf", o["fact_id"], fid)
            conn.execute("INSERT OR IGNORE INTO conflict(conflict_id,kind,a_table,a_id,b_table,b_id,detail,detected_at) VALUES(?,?,?,?,?,?,?,?)",
                         (cid, "bib_value", "bib_fact", o["fact_id"], "bib_fact", fid, f"{pred}: '{o['value_text']}' vs '{value}'", now))
            enqueue_review(conn, "source_conflict", "conflict", cid, f"{pred} 값 충돌(덮어쓰지 않음)", 70)
        return "conflict"
    return "same" if existing else "new"


def add_author_claim(conn, work_id, name, role, sid, url, evid, now, pol):
    aid = ensure_author(conn, name, now)
    wid = stable_id("wa", work_id, aid, role, sid)
    C.insert_claim(conn, "work_author", dict(wa_id=wid, work_id=work_id, author_id=aid, role=role, **_claim_common(sid, url, evid, now, pol)))


def add_edition(conn, work_id, isbn, sid, url, evid, now, pol, label=""):
    eid = stable_id("ed", work_id, isbn)
    C.insert_claim(conn, "edition", dict(edition_id=eid, work_id=work_id, isbn=isbn, label=label, identity_status="provisional",
                                         **_claim_common(sid, url, evid, now, pol)))


def register_external(conn, etype: str, eid: str, scheme: str, value: str, sid: str, now: str) -> None:
    if value:
        conn.execute("INSERT OR IGNORE INTO external_id(entity_type,entity_id,scheme,value,source_id,recorded_at) VALUES(?,?,?,?,?,?)", (etype, eid, scheme, value, sid, now))


def work_by_external(conn, scheme: str, value: str) -> Optional[str]:
    r = conn.execute("SELECT entity_id FROM external_id WHERE scheme=? AND value=? AND entity_type='work'", (scheme, value)).fetchone()
    return r["entity_id"] if r else None


def add_edition_fact(conn, edition_id: str, pred: str, value: str, sid, url, evid, now, pol) -> None:
    fid = stable_id("bf", edition_id, pred, norm(value), sid)
    C.insert_claim(conn, "bib_fact", dict(fact_id=fid, subject_type="edition", subject_id=edition_id, predicate=pred, value_text=value, **_claim_common(sid, url, evid, now, pol)))


def promote_editions(conn, grps, doc_url: str, sid: str, doc_id: str, now: str, pol, stats) -> None:
    m = re.search(r"/works/(OL\w+W)/editions", doc_url)
    work_id = work_by_external(conn, "openlibrary_work", "/works/" + m.group(1)) if m else None
    if not work_id:
        stats["editions_unmapped"] = stats.get("editions_unmapped", 0) + 1
        return
    for gkey, grp in grps.items():
        if (_vals(grp, "doc_kind") or [""])[0] != "edition_record":
            continue
        lang = (_vals(grp, "edition_lang") or ["und"])[0]
        ev = lambda f: ensure_evidence(conn, sid, f"doc:{doc_id}#{(grp.get(f) or [('', gkey, 'structured')])[0][1]}", "structured", now)
        for i in _vals(grp, "isbn")[:2]:
            add_edition(conn, work_id, i, sid, doc_url, ev("isbn"), now, pol, label="ko" if lang == "kor" else f"translation:{lang}")  # 번역판은 한국어 원서 필수 필드에 세지 않음
            eid = stable_id("ed", work_id, i)
            if lang != "kor":
                continue
            for f in ("publisher", "pub_year"):
                for v in _vals(grp, f)[:1]:
                    add_edition_fact(conn, eid, f, v, sid, doc_url, ev(f), now, pol)
        if lang == "kor":
            for v in _vals(grp, "original_title")[:1]:
                add_work_claim(conn, work_id, "original_title", v, sid, doc_url, ev("original_title"), now, pol)
            for t in _vals(grp, "translator")[:2]:
                add_author_claim(conn, work_id, t, "translator", sid, doc_url, ev("translator"), now, pol)
        stats["editions_seen"] = stats.get("editions_seen", 0) + 1


def promote_book(conn, grp, sid: str, url: str, doc_id: str, now: str, pol, stats: Dict[str, int]) -> Optional[str]:
    title = (_vals(grp, "title") or _vals(grp, "label_ko") or [""])[0]
    if not title or re.fullmatch(r"Q\d+", title):
        return None
    authors = _vals(grp, "author")
    isbns = _vals(grp, "isbn")
    work_id, how = find_work(conn, title, authors, isbns)
    cand_id = stable_id("cand", norm(title), norm(authors[0]) if authors else "")
    year = (_vals(grp, "pub_year") or [""])[0]
    publisher = (_vals(grp, "publisher") or [""])[0]
    is_new = False
    if work_id is None:
        cur = conn.execute("INSERT OR IGNORE INTO work_candidate(cand_id,title,author_name,first_year,isbn,publisher,origin,doc_id,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (cand_id, title, authors[0] if authors else "", year, isbns[0] if isbns else "", publisher, pol["registry_key"], doc_id, "new", now))
        is_new = bool(cur.rowcount)
        if not authors:
            stats["candidates_no_author"] = stats.get("candidates_no_author", 0) + 1
            if is_new:
                stats["new_candidates"] = stats.get("new_candidates", 0) + 1
            return None  # 작가 없는 후보는 작품으로 올리지 않는다(빈칸 탐색 대상)
        wslug = "c-" + cand_id[-10:]
        work_id = stable_id("wk", wslug)
        ins = conn.execute("INSERT OR IGNORE INTO work(work_id,slug,canonical_title,kind,created_at) VALUES(?,?,?,?,?)", (work_id, wslug, title, "", now))
        if ins.rowcount:
            is_new = True
        conn.execute("UPDATE work_candidate SET state='promoted', known_work_id=? WHERE cand_id=?", (work_id, cand_id))
        if is_new:
            stats["new_works"] = stats.get("new_works", 0) + 1
    else:
        conn.execute("INSERT OR IGNORE INTO work_candidate(cand_id,title,author_name,first_year,isbn,publisher,origin,doc_id,known_work_id,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (cand_id, title, authors[0] if authors else "", year, isbns[0] if isbns else "", publisher, pol["registry_key"], doc_id, work_id, "duplicate", now))
        stats["matched_known"] = stats.get("matched_known", 0) + 1
    ev = lambda f: ensure_evidence(conn, sid, f"doc:{doc_id}#{(grp.get(f) or [('', '', '')])[0][1]}", (grp.get(f) or [('', '', 'structured')])[0][2], now)
    for k in _vals(grp, "ol_work_key")[:1]:
        register_external(conn, "work", work_id, "openlibrary_work", k, sid, now)
    for nm, k in zip(authors, _vals(grp, "ol_author_key")):
        register_external(conn, "author", ensure_author(conn, nm, now), "openlibrary_author", k, sid, now)
    for a in authors[:6]:
        add_author_claim(conn, work_id, a, "author", sid, url, ev("author"), now, pol)
    for t in _vals(grp, "translator")[:3]:
        add_author_claim(conn, work_id, t, "translator", sid, url, ev("translator"), now, pol)
    for f, pred in (("pub_year", "pub_year"), ("publisher", "publisher"), ("original_title", "original_title")):
        for v in _vals(grp, f)[:2]:
            add_work_claim(conn, work_id, pred, v, sid, url, ev(f), now, pol)
    for i in isbns[:4]:
        add_edition(conn, work_id, i, sid, url, ev("isbn"), now, pol)
    return work_id


# ---------- 문서 하나 ----------
def parse_document(conn, doc_id: str, content: bytes, kind: str, url: str, ctype: str, reg: Optional[Registry] = None, hint_work_id: Optional[str] = None) -> Dict[str, Any]:
    reg = reg or Registry()
    now = now_iso()
    pol = effective_policy(conn, reg, url)
    extractor, fields = X.extract_by_kind(kind, content, url, ctype)
    conn.execute("DELETE FROM extraction WHERE doc_id=? AND extractor_version=?", (doc_id, EXTRACTOR_VERSION))  # 같은 버전 재실행은 멱등
    for f, v, loc, method in fields:
        conn.execute("INSERT INTO extraction(extraction_id,doc_id,extractor,extractor_version,field,value,locator,method,extracted_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (stable_id("ex", doc_id, EXTRACTOR_VERSION, f, loc, v), doc_id, extractor, EXTRACTOR_VERSION, f, v, loc, method, now))
    stats: Dict[str, int] = {"fields": len(fields)}
    row = conn.execute("SELECT canonical_url, domain FROM raw_document WHERE doc_id=?", (doc_id,)).fetchone()
    reg_kind = reg.get(pol["registry_key"])["kind"] if pol["registry_key"] in reg.by_key else "article"
    groups = group_fields(fields)
    labels_done = set()
    for gkey, grp in groups.items():
        dk = (_vals(grp, "doc_kind") or [""])[0]
        plain = {k: [x[0] for x in v] for k, v in grp.items()}
        for label, oc, basis in labeler.label_doc(dk, plain, pol["registry_key"], reg_kind):
            wid_hint = None
            if label in ("비평", "해석", "참고문헌") and hint_work_id:
                wt = conn.execute("SELECT canonical_title FROM work WHERE work_id=?", (hint_work_id,)).fetchone()
                if wt and norm(wt["canonical_title"]) and norm(wt["canonical_title"]) in norm((plain.get("title") or [""])[0]):
                    wid_hint = hint_work_id
                    basis += ";title_contains_work"
            if (label) in labels_done and wid_hint is None:
                continue
            labels_done.add(label)
            conn.execute("INSERT OR REPLACE INTO doc_label(doc_id,label,origin_class,basis,work_id,labeled_at) VALUES(?,?,?,?,?,?)", (doc_id, label, oc, basis, wid_hint, now))
        if dk == "book_record" and pol["may_collect"]:
            gurl = entity_url(grp, url)
            sid = ensure_source(conn, gurl, gurl if gurl != url else row["canonical_url"], row["domain"], pol, dk, now)
            promote_book(conn, grp, sid, gurl, doc_id, now, pol, stats)
    if any((_vals(g, "doc_kind") or [""])[0] == "edition_record" for g in groups.values()) and pol["may_collect"]:
        sid = ensure_source(conn, url, row["canonical_url"], row["domain"], pol, "book_record", now)
        promote_editions(conn, groups, url, sid, doc_id, now, pol, stats)
    conn.execute("UPDATE raw_document SET parsed_version=?, parsed_at=? WHERE doc_id=?", (EXTRACTOR_VERSION, now, doc_id))
    return stats
