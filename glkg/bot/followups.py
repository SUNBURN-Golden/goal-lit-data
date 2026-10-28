"""수집 직후 확장: 결과에서 새 질의·URL·후보 출처를 만든다. 전부 같은 트랜잭션 안에서 DB 행만 추가한다(네트워크 없음).
확장 경로: 작품 → 판본(ISBN) → 원제·번역자 → 저자 → 인용 작품·비평 → 논쟁. 하루 후보 500건 상한(discovered)을 센다."""
import re
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from ..registry import Registry
from ..timeutil import now_iso
from . import extractors as X, queries as Q
from .budget import Budget
from .executor import FetchResult
from .http import host_of
from .jobs import Job

MAX_LINKS_PER_DOC = 20
MAX_SITEMAP_URLS = 30


def note_candidate_domain(conn, reg: Registry, url: str, via: str, budget: Budget) -> bool:
    """처음 보는 도메인을 후보 출처로 올린다(레지스트리에 있거나 이미 후보면 무시)."""
    host = host_of(url).removeprefix("www.")
    if not host or reg.classify_host(host) != "unregistered":
        return False
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if conn.execute("SELECT 1 FROM candidate_source WHERE domain=?", (".".join(parts[i:]),)).fetchone():
            return False
    if not budget.take_discovered(1):
        return False
    conn.execute("INSERT OR IGNORE INTO candidate_source(domain,state,in_seed,first_seen_via,sample_url,first_seen_at) VALUES(?,?,0,?,?,?)", (host, "new", via, url, now_iso()))
    return True


def after_collect(conn, reg: Registry, budget: Budget, job: Job, res: FetchResult, stats: Optional[Dict[str, Any]]) -> Dict[str, int]:
    from .parser import norm
    out = {"queries": 0, "urls": 0, "domains": 0}
    if res.outcome not in ("ok", "unchanged") or not res.doc_id:
        return out
    doc_id = res.doc_id
    kind = res.kind
    ex = conn.execute("SELECT field, value FROM extraction WHERE doc_id=?", (doc_id,)).fetchall()
    f: Dict[str, list] = {}
    for r in ex:
        f.setdefault(r["field"], []).append(r["value"])
    cls = job.cls if job.cls != "refresh" else "discovery"

    if kind in ("ol_subject", "ol_author_works"):
        # 페이지네이션(연도·장르당 최대 150건) + 각 작품의 판본 질의(작품→ISBN/판본)
        m = re.search(r"offset=(\d+)", job.url)
        off = int(m.group(1)) if m else 0
        total = int((f.get("total_in_subject") or ["0"])[0] or 0)
        if kind == "ol_subject" and off + 50 < min(total, 150) and len(f.get("ol_work_key", [])) + int((f.get("skipped_non_korean_title") or ["0"])[0]) >= 1:
            nxt = re.sub(r"offset=\d+", f"offset={off + 50}", job.url)
            if Q.enqueue_query(conn, budget, "ol_subject", nxt, "page " + nxt[-40:], cls, 45, origin=f"page:{job.key}"):
                out["queries"] += 1
        for k in f.get("ol_work_key", []):
            row = conn.execute("SELECT entity_id FROM external_id WHERE scheme='openlibrary_work' AND value=? AND entity_type='work'", (k,)).fetchone()
            if row and Q.enqueue_query(conn, budget, "ol_editions", Q.ol_editions_url(k), f"editions {k}", "backfill", 60, origin=f"work:{row['entity_id']}", work_hint=row["entity_id"]):
                out["queries"] += 1

    elif kind in ("openalex", "crossref"):
        # 논문 → 출처 도메인(후보), 인용 확장은 '작품에 연결된 논문'에서만(관련 없는 인용망 폭주 방지)
        for u in (f.get("landing_page", []) + f.get("oa_pdf_url", []))[:10]:
            if note_candidate_domain(conn, reg, u, f"{kind}:{doc_id}", budget):
                out["domains"] += 1
        linked = conn.execute("SELECT DISTINCT work_id FROM doc_label WHERE doc_id=? AND work_id IS NOT NULL", (doc_id,)).fetchall()
        if linked and kind == "openalex":
            ids = [i for i in f.get("cites", [])][:40]
            if ids and Q.enqueue_query(conn, budget, "oa_cited", Q.oa_ids_url(ids), "cited by linked paper", "discovery", 35, origin=f"doc:{doc_id}", work_hint=linked[0]["work_id"]):
                out["queries"] += 1
            for oid in f.get("openalex_id", [])[:3]:
                if Q.enqueue_query(conn, budget, "oa_citing", Q.oa_citing_url(oid), f"citing {oid[-12:]}", "discovery", 30, origin=f"doc:{doc_id}", work_hint=linked[0]["work_id"]):
                    out["queries"] += 1

    elif kind == "html":
        # 본문에서 언급된 작품명 → 새 검색(엔티티 확장). 이미 아는 작품은 제외.
        known = {norm(r["canonical_title"]) for r in conn.execute("SELECT canonical_title FROM work")}
        for t in f.get("mention_title", [])[:5]:
            if norm(t) in known:
                continue
            if Q.enqueue_query(conn, budget, "oa_mention", Q.oa_search_url(f'"{t}"'), f"mention {t}", "discovery", 40, origin=f"doc:{doc_id}"):
                out["queries"] += 1
        # 공식 사이트 → 링크 → 첨부: 같은 도메인의 PDF·다음 페이지만(깊이 ≤ 2), 다른 도메인은 후보 출처로만
        if res.content and job.depth < 2 and job.table == "crawl_frontier":
            base_host = host_of(job.url)
            n = 0
            for href, text in X.html_links(res.content.decode("utf-8", "replace"), job.url):
                if n >= MAX_LINKS_PER_DOC:
                    break
                h = host_of(href)
                if h == base_host and (href.lower().split("?")[0].endswith(".pdf") or re.search(r"다음|next|›|»", text, re.I)):
                    if Q.enqueue_url(conn, budget, href, cls, job.depth + 1, 40, origin=f"link:{job.key}", work_hint=job.work_hint):
                        out["urls"] += 1
                        n += 1
                elif h != base_host and note_candidate_domain(conn, reg, href, f"link:{doc_id}", budget):
                    out["domains"] += 1
                    n += 1

    elif kind == "feed" and res.content and job.depth < config_depth():
        for u in X.feed_links(res.content)[:MAX_SITEMAP_URLS]:
            if u.lower().endswith((".xml", ".xml.gz")) and host_of(u) == host_of(job.url):
                if Q.enqueue_url(conn, budget, u, cls, job.depth + 1, 45, origin=f"sitemap:{job.key}"):
                    out["urls"] += 1
            elif host_of(u) == host_of(job.url):
                if Q.enqueue_url(conn, budget, u, cls, job.depth + 1, 40, origin=f"sitemap:{job.key}"):
                    out["urls"] += 1
    return out


def config_depth() -> int:
    from .. import config
    return config.MAX_LINK_DEPTH
