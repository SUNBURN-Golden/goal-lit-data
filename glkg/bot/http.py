"""예의 바른 HTTP 수집기.

기존 규칙 그대로: 도메인 동시 1 · 요청 간격 ≥5초 · robots.txt 확인(우리 UA 불허이거나 AI 크롤러 중 하나라도 불허면 수집 안 함,
robots 조회 실패 시에도 수집 안 함) · 정직한 UA · 우회·프록시·UA 위장 없음.
로그인 페이지·401/403·빈 본문은 '실패'로 기록하고 어떤 우회도 하지 않는다.
"""
import fcntl
import hashlib
import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

from .. import config

UA = "GoalLitBot/1.0 (+https://goallit.soulbound.co.kr; literary research; honors robots.txt)"
AI_AGENTS = ("GPTBot", "ClaudeBot", "anthropic-ai", "CCBot", "Google-Extended")
TIMEOUT = 25


class BudgetGate(Exception):
    """예산 때문에 robots 확인도 못 함 — 수집하지 않고 나중에."""


@dataclass
class Response:
    url: str
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    final_url: str = ""

    @property
    def ctype(self) -> str:
        return (self.headers.get("content-type") or self.headers.get("Content-Type") or "").split(";")[0].strip().lower()


class RequestsTransport:
    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: int = TIMEOUT, max_bytes: int = 15_000_000) -> Response:
        import requests
        h = {"User-Agent": UA, "Accept": "*/*"}
        h.update(headers or {})
        r = requests.get(url, headers=h, timeout=timeout, allow_redirects=True, stream=True)
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > max_bytes:
                break
        return Response(url=url, status=r.status_code, headers={k.lower(): v for k, v in r.headers.items()}, content=buf, final_url=r.url)


def canonicalize(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref", "spm")]
    q.sort()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urllib.parse.urlencode(q), ""))


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().split(":")[0]


class DomainClock:
    """도메인별 동시 1건 + 최소 간격. 프로세스가 달라도 파일 락으로 지킨다."""

    def __init__(self, lock_dir: Optional[Path] = None, interval: Optional[float] = None, sleep=time.sleep, now=time.time, before_wait=None):
        self.before_wait = before_wait   # 간격 대기·네트워크 직전에 호출: DB 쓰기 잠금을 쥔 채 잠들지 않도록 커밋한다
        self.dir = Path(lock_dir or config.LOCK_DIR) / "domains"
        self.interval = config.MIN_DOMAIN_INTERVAL_SEC if interval is None else interval
        self.sleep, self.now = sleep, now
        self.per_domain: Dict[str, float] = {}   # robots Crawl-delay 로 늘어난 간격

    def acquire(self, domain: str):
        if self.before_wait:
            self.before_wait()
        self.dir.mkdir(parents=True, exist_ok=True)
        f = open(self.dir / (re.sub(r"[^a-z0-9.-]", "_", domain) + ".lock"), "a+")
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            last = float(f.read().strip() or 0)
        except ValueError:
            last = 0.0
        wait = max(self.interval, self.per_domain.get(domain, 0)) - (self.now() - last)
        if wait > 0:
            self.sleep(wait)
        return f

    def release(self, f) -> None:
        f.seek(0)
        f.truncate()
        f.write(str(self.now()))
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def crawl_delay_for(robots_text: str, ua_name: str = "goallitbot") -> float:
    """우리 UA에 해당하는 그룹('*' 또는 '*bot' 같은 와일드카드 포함)의 Crawl-delay 중 최댓값(초)."""
    import fnmatch
    agents, best, in_rules = [], 0.0, False
    for line in robots_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(v.lower())
        else:
            in_rules = True
            if k == "crawl-delay":
                try:
                    d = float(v)
                except ValueError:
                    continue
                if any(a == "*" or fnmatch.fnmatch(ua_name, a) for a in agents):
                    best = max(best, d)
    return best


class RobotsGate:
    """(허용 여부, 사유). robots 응답 캐시는 호스트당 24시간."""

    def __init__(self, transport, clock: DomainClock, ttl: int = 86400, now=time.time, charge=None):
        self.transport, self.clock, self.ttl, self.now = transport, clock, ttl, now
        self.charge = charge  # robots.txt 요청도 일일 예산에서 센다 (callable -> bool)
        self.cache: Dict[str, Tuple[float, Optional[urllib.robotparser.RobotFileParser], str]] = {}
        self.requests_made = 0
        self.sitemaps: Dict[str, list] = {}

    def _load(self, scheme: str, host: str):
        hit = self.cache.get(host)
        if hit and self.now() - hit[0] < self.ttl:
            return hit
        url = f"{scheme}://{host}/robots.txt"
        if self.charge and not self.charge():
            raise BudgetGate()
        f = self.clock.acquire(host)
        try:
            self.requests_made += 1
            r = self.transport.get(url, timeout=15)
        except Exception as e:  # noqa: BLE001
            r = None
            err = f"robots_unreadable:{type(e).__name__}"
        finally:
            self.clock.release(f)
        if r is None:
            entry = (self.now(), None, err)
        elif r.status in (404, 410):
            rp = urllib.robotparser.RobotFileParser()
            rp.parse([])
            entry = (self.now(), rp, "no_robots")
        elif r.status == 200:
            rp = urllib.robotparser.RobotFileParser()
            text = r.content.decode("utf-8", "replace")
            rp.parse(text.splitlines())
            self.sitemaps[host] = list(rp.site_maps() or [])
            d = crawl_delay_for(text)
            if d:
                self.clock.per_domain[host] = max(d, self.clock.interval)
            entry = (self.now(), rp, "ok")
        else:
            entry = (self.now(), None, f"robots_status_{r.status}")  # 401/403/5xx: 안전하게 수집 안 함
        if entry[1] is None and (entry[2].startswith("robots_unreadable") or re.fullmatch(r"robots_status_5\d\d", entry[2])):
            # 일시 장애(타임아웃·SSL·5xx): 수집은 안 하되 24시간 동안 '막힘'으로 굳히지 않고 10분 뒤 다시 확인한다
            entry = (self.now() - self.ttl + min(self.ttl, 600), entry[1], entry[2])
        self.cache[host] = entry
        return entry

    def allowed(self, url: str) -> Tuple[bool, str]:
        p = urllib.parse.urlsplit(url)
        _, rp, why = self._load(p.scheme or "https", p.netloc.lower())
        if rp is None:
            return False, why
        if not rp.can_fetch(UA, url):
            return False, "robots_disallows_us"
        for a in AI_AGENTS:
            if not rp.can_fetch(a, url):
                return False, f"robots_blocks_ai:{a}"
        return True, why


LOGIN_URL = re.compile(r"/(login|signin|sign-in|log-in|auth|accounts?/login|member/login|users?/sign_in)(/|\?|$)", re.I)


def visible_text_len(html: str) -> int:
    t = re.sub(r"(?is)<(script|style|noscript|template).*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return len(re.sub(r"\s+", " ", t).strip())


def classify_body(resp: Response) -> Tuple[str, str]:
    """('ok'|'failed_login'|'failed_empty'|'failed_inaccessible'|'failed_http'|'failed_rate_limit', 사유)"""
    s = resp.status
    if s == 429:
        return "failed_rate_limit", "http_429"
    if s in (401, 403):
        return "failed_inaccessible", f"http_{s}"
    if s >= 400 or s < 200:
        return "failed_http", f"http_{s}"
    if not resp.content:
        return "failed_empty", "zero_bytes"
    if "html" in resp.ctype or (not resp.ctype and resp.content[:15].lower().startswith((b"<!doctype", b"<html"))):
        html = resp.content.decode("utf-8", "replace")
        fu = resp.final_url or resp.url
        if LOGIN_URL.search(urllib.parse.urlsplit(fu).path) or (re.search(r'type=["\']?password', html, re.I) and visible_text_len(html) < 2500):
            return "failed_login", "login_page"
        if visible_text_len(html) < 200 and not re.search(r"application/ld\+json|name=[\"']citation_title", html, re.I):
            return "failed_empty", "empty_or_js_only_body"
    return "ok", ""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
