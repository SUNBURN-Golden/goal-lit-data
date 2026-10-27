"""수집 예산: 재수집(refresh)·탐색(discovery)·보충(backfill)을 따로 센다. 합계는 하루 요청 200건(기존 상한) 이하.
daily_usage 에 stage='bot:<class>', metric='requests' 로 기록한다(재시작해도 유지)."""
from typing import Dict

from .. import config
from ..timeutil import today

CLASSES = ("backfill", "discovery", "refresh")
# 하루 200건 = 보충 120 + 탐색 60 + 재수집 20. 넘겨 쓰기 없음(예측 가능).
DEFAULT_SPLIT = {"backfill": 120, "discovery": 60, "refresh": 20}


class Budget:
    def __init__(self, conn, split: Dict[str, int] = None, fetch_cap: int = None, discovered_cap: int = None):
        self.conn = conn
        self.split = dict(split or DEFAULT_SPLIT)
        self.fetch_cap = config.MAX_DAILY_FETCHED if fetch_cap is None else fetch_cap
        self.discovered_cap = config.MAX_DAILY_DISCOVERED if discovered_cap is None else discovered_cap
        assert sum(self.split.values()) <= self.fetch_cap, "split exceeds daily fetch cap"

    def _used(self, stage: str, metric: str = "requests") -> int:
        r = self.conn.execute("SELECT used FROM daily_usage WHERE day=? AND stage=? AND metric=?", (today(), stage, metric)).fetchone()
        return r["used"] if r else 0

    def used(self, cls: str) -> int:
        return self._used("bot:" + cls)

    def total_used(self) -> int:
        """봇 3종 + 기존 수집기('fetch' 단계) 요청 합계."""
        return sum(self.used(c) for c in CLASSES) + self._used("fetch")

    def available(self, cls: str) -> int:
        return max(0, min(self.split[cls] - self.used(cls), self.fetch_cap - self.total_used()))

    def take(self, cls: str, n: int = 1) -> bool:
        if self.available(cls) < n:
            return False
        self._bump("bot:" + cls, "requests", n, self.split[cls])
        return True

    def discovered_left(self) -> int:
        return max(0, self.discovered_cap - self._used("bot:discovery", "discovered"))

    def take_discovered(self, n: int = 1) -> bool:
        if self.discovered_left() < n:
            return False
        self._bump("bot:discovery", "discovered", n, self.discovered_cap)
        return True

    def _bump(self, stage: str, metric: str, n: int, cap: int) -> None:
        self.conn.execute(
            "INSERT INTO daily_usage(day,stage,metric,used,cap) VALUES(?,?,?,?,?) "
            "ON CONFLICT(day,stage,metric) DO UPDATE SET used=used+excluded.used, cap=excluded.cap", (today(), stage, metric, n, cap))

    def snapshot(self) -> Dict[str, object]:
        return {"day": today(), "requests": {c: f"{self.used(c)}/{self.split[c]}" for c in CLASSES}, "legacy_fetch": self._used("fetch"),
                "total": f"{self.total_used()}/{self.fetch_cap}", "discovered": f"{self._used('bot:discovery', 'discovered')}/{self.discovered_cap}"}
