"""1단계 소스 발견: 사이트 작품 기록(works.json)의 근거 URL 중 '수집 허용 + robots 감사 통과' 호스트만 수집 큐 후보로 올린다.
금지 호스트·미감사 호스트·인스타 공개 페이지는 후보로 올리지 않고 건수만 센다(우회 없음)."""
import json
from typing import Callable, Optional

from .. import config
from ..pipeline import Context, Stage
from ..registry import Registry


def _default_enqueue(url: str, slug: str, priority: int) -> None:
    import sys
    sys.path.insert(0, str(config.LEGACY_COLLECTOR_DIR))
    from storage import CollectorStorage  # 기존 수집기 저장소(큐는 거기 있다)
    CollectorStorage().enqueue_url(url=url, target_work_slug=slug, collection_path="auto", priority=priority)


class DiscoverStage(Stage):
    name = "discover"
    custom = True

    def __init__(self, works_json=None, registry: Optional[Registry] = None, enqueue: Optional[Callable] = None, known_urls=None):
        self.works_json = works_json or config.WORKS_JSON
        self.reg = registry or Registry()
        self.enqueue = enqueue or _default_enqueue
        self.known_urls = known_urls  # 이미 큐·수집함에 있는 canonical URL 집합 (None이면 KG source 테이블)

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        try:
            works = json.loads(open(self.works_json, encoding="utf-8").read())
        except Exception as e:  # 입력 없음: 실패 큐로 보낼 항목이 아니라 단계 실패
            raise RuntimeError(f"works_json 읽기 실패: {e}")
        known = set(self.known_urls) if self.known_urls is not None else {r[0] for r in conn.execute("SELECT canonical_url FROM source")}
        stats = {"candidates": 0, "enqueued": 0, "skipped_policy": 0, "skipped_known": 0}
        for slug, w in works.items():
            for fact in w.get("facts", []):
                url = fact[3] if len(fact) > 3 else ""
                if not url:
                    continue
                stats["candidates"] += 1
                pol = self.reg.policy_for_url(url)
                if not pol["may_collect"] or pol["registry_key"] in ("unregistered", "internal_site_records"):
                    stats["skipped_policy"] += 1
                    continue
                if url in known:
                    stats["skipped_known"] += 1
                    continue
                if not ctx.consume("requests"):
                    stats["budget_exhausted"] = "requests"
                    return stats
                self.enqueue(url, slug, 60)
                known.add(url)
                stats["enqueued"] += 1
        return stats
