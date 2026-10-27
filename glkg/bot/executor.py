"""수집 실행기. 우선순위: API/피드 → HTML → (동적 페이지에 한해) 브라우저 렌더 → PDF/이미지.
브라우저는 '자바스크립트로만 그려지는 빈 페이지'에서만 쓰고, 401/403/로그인/robots 차단은 절대 우회하지 않는다.
저장: 내용 해시·수집 시각·추출기 버전. 로그인 페이지와 빈 본문은 실패로 기록한다."""
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .. import config
from ..db import stable_id
from ..registry import Registry
from ..timeutil import add_seconds, now_iso
from . import http as H
from .budget import Budget
from .policy import effective_policy, storable_body

RAW_DIR = config.DATA_DIR / "kg_raw"


class BudgetExhausted(Exception):
    def __init__(self, cls: str):
        super().__init__(cls)
        self.cls = cls


@dataclass
class FetchResult:
    outcome: str
    detail: str = ""
    doc_id: Optional[str] = None
    is_new: bool = False
    content: bytes = b""
    ctype: str = ""
    method: str = ""
    kind: str = ""
    status: Optional[int] = None
    registry_key: str = ""


def extractor_kind(url: str, method: str, ctype: str) -> str:
    host = H.host_of(url)
    if host == "api.openalex.org":
        return "openalex"
    if host == "api.crossref.org":
        return "crossref"
    if host == "query.wikidata.org":
        return "wikidata_sparql"
    if host.endswith("wikidata.org") and "/w/api.php" in url:
        return "wikidata_entities"
    if method in ("html", "browser"):
        return "html"
    return method  # pdf / image / feed / api(unknown json)


def method_for(ctype: str, content: bytes) -> str:
    if "json" in ctype:
        return "api"
    if "xml" in ctype or "rss" in ctype or "atom" in ctype:
        return "feed"
    if "html" in ctype or (not ctype and content[:15].lower().startswith((b"<!doctype", b"<html"))):
        return "html"
    if "pdf" in ctype or content[:5] == b"%PDF-":
        return "pdf"
    if ctype.startswith("image/"):
        return "image"
    return ""


class FetchExecutor:
    def __init__(self, conn, registry: Optional[Registry] = None, transport=None, clock: Optional[H.DomainClock] = None,
                 budget: Optional[Budget] = None, raw_dir: Optional[Path] = None, renderer: Optional[Callable[[str], str]] = None):
        self.conn = conn
        self.reg = registry or Registry()
        self.transport = transport or H.RequestsTransport()
        self.clock = clock or H.DomainClock()
        self.budget = budget or Budget(conn)
        self.raw_dir = Path(raw_dir or RAW_DIR)
        self.renderer = renderer  # 선택: 동적 페이지용. 없으면 'needs_browser' 실패로 기록
        self._cls = "discovery"
        self.gate = H.RobotsGate(self.transport, self.clock, charge=lambda: self.budget.take(self._cls))

    # ---- 기록 ----
    def _log(self, url, outcome, detail="", method="", status=None, cls=""):
        self.conn.execute("INSERT INTO fetch_log(url,domain,at,method,http_status,outcome,detail,budget_class) VALUES(?,?,?,?,?,?,?,?)",
                          (url, H.host_of(url), now_iso(), method, status, outcome, detail[:300], cls))

    def _store(self, doc_id: str, content: bytes) -> str:
        d = self.raw_dir / doc_id[4:6]
        d.mkdir(parents=True, exist_ok=True)
        p = d / (doc_id + ".bin")
        p.write_bytes(content)
        p.chmod(0o600)
        return str(p)

    # ---- 본체 ----
    def fetch(self, url: str, budget_class: str = "discovery", origin: str = "", trial: bool = False,
              headers: Optional[Dict[str, str]] = None) -> FetchResult:
        self._cls = budget_class
        canon = H.canonicalize(url)
        pol = effective_policy(self.conn, self.reg, url)
        if not pol["may_collect"] and not (trial and pol["trial_allowed"]):
            self._log(url, "failed_policy", f"{pol['registry_key']}:{pol['support_level']}:{pol['via']}", cls=budget_class)
            return FetchResult("failed_policy", pol["via"], registry_key=pol["registry_key"])
        try:
            ok, why = self.gate.allowed(url)
        except H.BudgetGate:
            raise BudgetExhausted(budget_class)
        if not ok:
            self._log(url, "failed_robots", why, cls=budget_class)
            return FetchResult("failed_robots", why, registry_key=pol["registry_key"])
        if not self.budget.take(budget_class):
            raise BudgetExhausted(budget_class)
        host = H.host_of(url)
        lock = self.clock.acquire(host)
        try:
            resp = self.transport.get(url, headers=headers)
        except Exception as e:  # noqa: BLE001
            self.clock.release(lock)
            self._log(url, "failed_network", f"{type(e).__name__}", cls=budget_class)
            return FetchResult("failed_network", type(e).__name__, registry_key=pol["registry_key"])
        self.clock.release(lock)
        outcome, detail = H.classify_body(resp)
        method = ""
        content, ctype = resp.content, resp.ctype
        if outcome == "failed_empty" and detail == "empty_or_js_only_body" and self.renderer is not None:
            # 동적 페이지: robots 는 이미 통과. 렌더 한 번 = 요청 1건 추가.
            if self.budget.take(budget_class):
                try:
                    html = self.renderer(url)
                    resp2 = H.Response(url=url, status=200, headers={"content-type": "text/html"}, content=html.encode(), final_url=url)
                    o2, d2 = H.classify_body(resp2)
                    if o2 == "ok":
                        outcome, detail, content, ctype, method = "ok", "", resp2.content, "text/html", "browser"
                    else:
                        outcome, detail = o2, d2
                except Exception as e:  # noqa: BLE001
                    detail = f"browser_error:{type(e).__name__}"
            else:
                raise BudgetExhausted(budget_class)
        elif outcome == "failed_empty" and detail == "empty_or_js_only_body":
            detail = "needs_browser"
        if outcome != "ok":
            self._log(url, outcome, detail, status=resp.status, cls=budget_class)
            return FetchResult(outcome, detail, status=resp.status, registry_key=pol["registry_key"])
        method = method or method_for(ctype, content)
        if not method:
            self._log(url, "failed_unsupported_type", ctype, status=resp.status, cls=budget_class)
            return FetchResult("failed_unsupported_type", ctype, status=resp.status, registry_key=pol["registry_key"])
        h = H.sha256(content)
        kind = extractor_kind(url, method, ctype)
        prev = self.conn.execute("SELECT doc_id FROM raw_document WHERE canonical_url=? AND content_hash=?", (canon, h)).fetchone()
        if prev:
            self._log(url, "unchanged", "same content hash", method, resp.status, budget_class)
            return FetchResult("unchanged", doc_id=prev["doc_id"], content=content, ctype=ctype, method=method, kind=kind, status=resp.status, registry_key=pol["registry_key"])
        now = now_iso()
        doc_id = "doc_" + h[:16]
        body = storable_body(pol)
        path = self._store(doc_id, content) if body else None
        days = pol.get("retention_days") or 365
        self.conn.execute(
            """INSERT INTO raw_document(doc_id,url,canonical_url,domain,fetch_method,http_status,content_type,content_hash,byte_len,fetched_at,body_stored,
               storage_path,retention_until,origin,budget_class) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, url, canon, host, method, resp.status, ctype, h, len(content), now, 1 if body else 0, path, add_seconds(now, days * 86400)[:10], origin, budget_class))
        self._log(url, "ok", "", method, resp.status, budget_class)
        return FetchResult("ok", doc_id=doc_id, is_new=True, content=content, ctype=ctype, method=method, kind=kind, status=resp.status, registry_key=pol["registry_key"])
