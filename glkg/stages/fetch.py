"""2단계 증분 수집: 기존 수집기(auto_runner)의 1회 실행을 호출한다.
politeness(도메인 동시 1·5초 간격)·robots 재확인·일 500/200 예산·자동 게시 off 는 수집기 쪽 코드가 그대로 적용된다.
여기서는 KG 쪽 일일 요청 상한과 중복 실행 락, 실패 시 재시도 큐를 추가한다."""
from typing import Callable, Optional

from .. import config
from ..pipeline import Context, Stage


def _default_runner(max_items: int):
    import sys
    sys.path.insert(0, str(config.LEGACY_COLLECTOR_DIR))
    import auto_runner
    return auto_runner.run_once(max_items)


class FetchStage(Stage):
    name = "fetch"
    custom = True

    def __init__(self, runner: Optional[Callable] = None, per_run: int = 5):
        self.runner = runner or _default_runner
        self.per_run = per_run

    def run_custom(self, ctx: Context):
        allowed = min(self.per_run, ctx.remaining("requests"))
        if allowed <= 0:
            return {"budget_exhausted": "requests", "ran": 0}
        stats = self.runner(allowed) or {}
        used = int(stats.get("processed", allowed))
        ctx.consume("requests", max(0, min(used, allowed)))
        ctx.processed += used
        return {"ran": used, "collector": stats.get("results", {})}
