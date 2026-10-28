#!/usr/bin/env python3
"""경로 1 자동 수집 실행기 (auto_runner.py) — auto_collector.AutoCollector를 실제로 구동하는 루프.

(2026-10-27 추가) 기존에는 auto_collector.py가 라이브러리일 뿐 진입점이 없어서 데몬에서 한 번도 실행되지 않았다.
이 실행기가 crawl_queue(collection_path='auto')를 우선순위 순으로 소비한다.

안전 규칙(config.py/politeness.py 그대로 사용, 완화 없음):
- 일 후보 500 / 본문 200 예산, 도메인 동시 1 · 요청 간격 >= 5초, 링크 깊이 <= 2
- research/robots/_summary.json 감사에서 ok/no-robots-rules 로 확인된 호스트만 대상(미감사 호스트·BLOCKS-AI·네이버·DC·인스타·연합뉴스 등 제외)
- 매 호스트마다 live robots.txt 재확인: 우리 UA 불허이거나 AI 크롤러(GPTBot/ClaudeBot/CCBot/anthropic-ai/Google-Extended) 중 하나라도 불허면 수집 안 함, robots 조회 실패 시 수집 안 함
- 우회·프록시·UA 위장 없음, AUTO_PUBLISH_TO_SITE=False 이므로 사이트 반영은 하지 않고 수집함에만 저장
"""
import argparse
import json
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Any, Dict, Optional

from auto_collector import AutoCollector
from config import AUTO_PUBLISH_TO_SITE, BRAND, LOGS_DIR, ROBOTS_SUMMARY_PATH, USER_AGENT, WORKS_JSON_PATH
from politeness import extract_domain, is_trap_or_disallowed_url
from storage import CollectorStorage, now_kst_iso

OWN_HOSTS = ("goallit.soulbound.co.kr",)
AI_AGENTS = ("GPTBot", "ClaudeBot", "anthropic-ai", "CCBot", "Google-Extended")
MAX_ATTEMPTS = 3
RECHECK_DAYS = 7

_STOP = False
_robots_cache: Dict[str, Any] = {}


def _on_signal(signum, frame):  # noqa: ARG001
    global _STOP
    _STOP = True


def log(msg: str) -> None:
    print(f"[{now_kst_iso()}] {msg}", flush=True)


def audited_ok_hosts() -> set:
    try:
        d = json.loads(ROBOTS_SUMMARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {h for h, v in d.items() if v.get("status") in ("ok", "no-robots-rules") and not v.get("star_blocks_all")}


def robots_allows(ac: AutoCollector, url: str) -> bool:
    """live robots.txt 확인. 불확실하면 False(수집 안 함)."""
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    key = host
    now = time.time()
    ent = _robots_cache.get(key)
    if not ent or now - ent[0] > 86400:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{p.scheme}://{host}/robots.txt"
        ok = False
        try:
            with ac.politeness.domain_slot(robots_url):
                req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        rp.parse(resp.read(500_000).decode("utf-8", "replace").splitlines())
                        ok = True
                except urllib.error.HTTPError as e:
                    if e.code in (404, 410):
                        rp.parse([])  # robots 없음 = 제한 없음
                        ok = True
        except Exception:
            ok = False
        ent = (now, rp if ok else None)
        _robots_cache[key] = ent
    rp = ent[1]
    if rp is None:
        return False
    if not rp.can_fetch(USER_AGENT, url):
        return False
    return all(rp.can_fetch(a, url) for a in AI_AGENTS)


def _set_state(storage: CollectorStorage, qid: str, state: str, cursor: str = "") -> None:
    with storage._connect() as conn:
        conn.execute(
            "UPDATE crawl_queue SET state = ?, cursor_pos = ?, updated_at = ? WHERE queue_id = ?",
            (state, cursor, now_kst_iso(), qid),
        )


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "").lower()


_BRACKETS = re.compile(r"[「『《〈<\"'“‘]\s*([^」』》〉>\"'”’]{1,60})\s*[」』》〉>\"'”’]")


def _title_matches(work_title: str, paper_title: str, abstract: str) -> bool:
    """작품명이 논문 제목/초록에 나오는지. 짧은 작품명(정규화 5자 미만, 예: '고래')은 「」『』《》 등으로 인용된 경우만 인정."""
    nt = _norm(work_title)
    if len(nt) < 5:
        for text in (paper_title, abstract):
            if any(_norm(m) == nt for m in _BRACKETS.findall(text or "")):
                return True
        return False
    return nt in _norm(paper_title) or nt in _norm(abstract)


def _work_titles() -> Dict[str, str]:
    try:
        w = json.loads(WORKS_JSON_PATH.read_text(encoding="utf-8"))
        return {slug: v.get("title", "") for slug, v in w.items()}
    except Exception:
        return {}


def handle_work_seed(ac: AutoCollector, row: Dict[str, Any], titles: Dict[str, str]) -> str:
    """사이트 내부 작품 앵커(#unverified-N): 작품명으로 OpenAlex 공개 API를 검색해 학술 메타데이터 후보를 수집한다."""
    slug = row["target_work_slug"]
    title = titles.get(slug, "")
    if not title or len(_norm(title)) < 2:
        return "skipped_no_title"
    api = "https://api.openalex.org/works?per-page=5&select=id,title,abstract_inverted_index&search=" + urllib.parse.quote(title)
    ok, _ = ac.politeness.try_consume_fetch_budget(1)
    if not ok:
        return "budget_exhausted"
    try:
        with ac.politeness.domain_slot(api):
            req = urllib.request.Request(api, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.status
                data = json.loads(resp.read().decode("utf-8", "replace"))
        ac.politeness.record_response_status(api, status)
    except urllib.error.HTTPError as e:
        ac.politeness.record_response_status(api, e.code)
        return "http_error"
    except Exception:
        return "error"
    nt = _norm(title)
    added = 0
    for w in data.get("results", []):
        t = w.get("title") or ""
        inv = w.get("abstract_inverted_index") or {}
        abstract = " ".join(inv.keys()) if isinstance(inv, dict) else ""
        # 작품명이 논문 제목/초록에 실제로 나온 것만 채택(엉뚱한 작품에 붙는 오탐 방지)
        if _title_matches(title, t, abstract):
            if ac.fetch_openalex_work(w.get("id", ""), slug):
                added += 1
    return f"done:openalex_matches={added}"


def handle_external(ac: AutoCollector, row: Dict[str, Any], allowed: set) -> str:
    url = row["url"]
    host = urllib.parse.urlsplit(url).netloc.lower()
    disallowed, reason = is_trap_or_disallowed_url(url, depth=int(row["link_depth"]))
    if disallowed:
        return f"blocked:{reason}"
    if host not in allowed and ("www." + host) not in allowed and host.lstrip("www.") not in allowed:
        return "blocked:not_in_robots_audit"
    if not robots_allows(ac, url):
        return "blocked:robots_live"
    saved = ac.fetch_html_page(url, row["target_work_slug"], link_depth=int(row["link_depth"]), follow_links=True)
    return "done" if saved else "fetch_failed"


def seed_queue(ac: AutoCollector, allowed: set) -> int:
    """큐가 비면 works.json의 근거 URL(감사 통과 호스트)을 재확인 후보로 넣는다."""
    n = 0
    try:
        w = json.loads(WORKS_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return 0
    for slug, v in w.items():
        for fact in v.get("facts", []):
            url = fact[3] if len(fact) > 3 else ""
            host = urllib.parse.urlsplit(url).netloc.lower()
            if url and host in allowed and not is_trap_or_disallowed_url(url)[0]:
                ac.storage.enqueue_url(url=url, target_work_slug=slug, collection_path="auto", priority=60)
                n += 1
    return n


def requeue_stale(storage: CollectorStorage) -> None:
    import datetime as dt
    cutoff = (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))) - dt.timedelta(days=RECHECK_DAYS)).isoformat(timespec="seconds")
    with storage._connect() as conn:
        conn.execute(
            "UPDATE crawl_queue SET state='queued', updated_at=? WHERE brand=? AND collection_path='auto' AND state='done' AND updated_at < ?",
            (now_kst_iso(), BRAND, cutoff),
        )


def run_once(max_items: int = 5, ac: Optional[AutoCollector] = None) -> Dict[str, Any]:
    assert AUTO_PUBLISH_TO_SITE is False, "AUTO_PUBLISH_TO_SITE must stay False"
    ac = ac or AutoCollector()
    allowed = audited_ok_hosts()
    titles = _work_titles()
    requeue_stale(ac.storage)
    stats: Dict[str, Any] = {"processed": 0, "results": {}}
    with ac.storage._connect() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) c FROM crawl_queue WHERE brand=? AND collection_path='auto' AND state='queued'", (BRAND,)
        ).fetchone()["c"]
    if pending == 0:
        stats["seeded"] = seed_queue(ac, allowed)
    with ac.storage._connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM crawl_queue WHERE brand=? AND collection_path='auto' AND state='queued' "
            "ORDER BY priority DESC, updated_at ASC LIMIT ?", (BRAND, int(max_items))).fetchall()]
    for row in rows:
        if _STOP:
            break
        qid = row["queue_id"]
        host = urllib.parse.urlsplit(row["url"]).netloc.lower()
        try:
            if host in OWN_HOSTS:
                res = handle_work_seed(ac, row, titles)
            else:
                res = handle_external(ac, row, allowed)
        except RuntimeError as e:  # domain_in_backoff 등
            res = f"deferred:{e}"
        except Exception as e:  # noqa: BLE001
            res = f"error:{type(e).__name__}"
        stats["processed"] += 1
        stats["results"][res.split(":")[0]] = stats["results"].get(res.split(":")[0], 0) + 1
        attempts = int(row["cursor_pos"].split(":")[1]) if row["cursor_pos"].startswith("attempt:") else 0
        if res.startswith("done") or res.startswith("skipped") or res.startswith("blocked"):
            _set_state(ac.storage, qid, "done" if res.startswith("done") else ("blocked" if res.startswith("blocked") else "skipped"), res)
        elif res.startswith("budget") or res.startswith("deferred"):
            _set_state(ac.storage, qid, "queued", row["cursor_pos"])
            if res.startswith("budget"):
                break
        else:
            attempts += 1
            _set_state(ac.storage, qid, "failed" if attempts >= MAX_ATTEMPTS else "queued", f"attempt:{attempts}:{res}"[:60] if attempts >= MAX_ATTEMPTS else f"attempt:{attempts}")
        log(f"{row['url'][:90]} -> {res}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Goal Lit auto collector runner")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=1800, help="루프 주기(초)")
    ap.add_argument("--max-items", type=int, default=5, help="회당 처리할 큐 항목 수")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log(f"auto_runner start (once={args.once}, interval={args.interval}s, max_items={args.max_items})")
    while not _STOP:
        try:
            st = run_once(args.max_items)
            summ = CollectorStorage().get_status_summary()
            (LOGS_DIR / "latest_status.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"cycle done: {st} stage_counts={summ['stage_counts']}")
        except Exception as e:  # noqa: BLE001
            log(f"cycle error: {type(e).__name__}: {e}")
        if args.once:
            break
        for _ in range(args.interval):
            if _STOP:
                break
            time.sleep(1)
    log("auto_runner stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
