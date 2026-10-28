"""질의 생성기: 장르 × 연도 × 키워드 조합, 작품→판본, 작품→비평, 인용 확장. 모든 URL은 공개 API/허용 경로뿐이다."""
import json
import urllib.parse as up
from typing import Dict, Iterable, List, Optional

from ..db import stable_id
from ..timeutil import now_iso

OL = "https://openlibrary.org"
OA = "https://api.openalex.org"
CR = "https://api.crossref.org"
OA_SELECT = "id,display_name,publication_year,doi,language,type,authorships,primary_location,best_oa_location,referenced_works,cited_by_count,keywords"
CR_SELECT = "DOI,title,author,issued,publisher,type,reference"

# 장르(Open Library 주제 슬러그) · 비평 키워드. 새 장르·키워드는 여기 한 줄로 늘린다.
SUBJECTS = {"korean_fiction": "소설", "korean_short_stories": "단편소설", "korean_poetry": "시"}
KEYWORDS = ["비평", "서평", "해석", "인터뷰"]
YEAR_MAX, YEAR_MIN = 2025, 1990


def ol_subject_url(subject: str, year: Optional[int] = None, offset: int = 0, limit: int = 50) -> str:
    q = {"limit": limit, "offset": offset}
    if year:
        q["published_in"] = f"{year}-{year}"
    return f"{OL}/subjects/{subject}.json?" + up.urlencode(q)


def ol_editions_url(work_key: str, limit: int = 50) -> str:
    return f"{OL}{work_key}/editions.json?limit={limit}"


def oa_search_url(text: str, year: Optional[int] = None, korean: bool = False) -> str:
    q = {"search": text, "per-page": 15, "select": OA_SELECT}
    f = []
    if year:
        f.append(f"publication_year:{year}")
    if korean:
        f.append("language:ko")
    if f:
        q["filter"] = ",".join(f)
    return f"{OA}/works?" + up.urlencode(q)


def oa_ids_url(ids: Iterable[str]) -> str:
    short = "|".join(i.rsplit("/", 1)[-1] for i in ids)
    return f"{OA}/works?" + up.urlencode({"filter": "openalex:" + short, "per-page": 50, "select": OA_SELECT})


def oa_citing_url(openalex_id: str) -> str:
    return f"{OA}/works?" + up.urlencode({"filter": "cites:" + openalex_id.rsplit("/", 1)[-1], "per-page": 25, "select": OA_SELECT})


def cr_search_url(text: str) -> str:
    return f"{CR}/works?" + up.urlencode({"query.bibliographic": text, "rows": 10, "select": CR_SELECT})


def enqueue_query(conn, budget, kind: str, url: str, text: str, cls: str, priority: int = 50, origin: str = "", work_hint: str = "") -> bool:
    qid = stable_id("q", kind, url)
    if conn.execute("SELECT 1 FROM discovery_query WHERE query_id=?", (qid,)).fetchone():
        return False          # 이미 있는 질의는 후보 상한을 쓰지 않는다
    if budget is not None and not budget.take_discovered(1):
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO discovery_query(query_id,kind,api,query_text,params_json,budget_class,priority,origin,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (qid, kind, up.urlsplit(url).netloc, text[:200], json.dumps({"url": url, "work_hint": work_hint}, ensure_ascii=False), cls, priority, origin, "queued", now_iso()))
    return bool(cur.rowcount)


def enqueue_url(conn, budget, url: str, cls: str, depth: int = 0, priority: int = 40, origin: str = "", work_hint: str = "") -> bool:
    from .http import canonicalize, host_of
    if conn.execute("SELECT 1 FROM crawl_frontier WHERE url=?", (canonicalize(url),)).fetchone():
        return False
    if budget is not None and not budget.take_discovered(1):
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO crawl_frontier(url,domain,origin,depth,priority,budget_class,state,created_at,work_hint) VALUES(?,?,?,?,?,?,?,?,?)",
        (canonicalize(url), host_of(url), origin, depth, priority, cls, "queued", now_iso(), work_hint))
    return bool(cur.rowcount)
