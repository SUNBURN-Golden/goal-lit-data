"""harvest 단계(수집 실행기·스케줄러): 예산 클래스(refresh/backfill/discovery)별로 작업을 번갈아 뽑아 한 건씩 수집한다.
한 작업 = 한 트랜잭션(가져오기+파싱+라벨+승격+작업 상태+후속 질의). 도중에 죽어도 작업은 queued 로 남아 이어서 처리된다."""
from typing import Optional

from ..bot import followups, jobs
from ..bot.budget import CLASSES, Budget
from ..bot.collect import collect
from ..bot.executor import BudgetExhausted, FetchExecutor
from ..pipeline import Context, Stage
from ..registry import Registry

ORDER = ("refresh", "backfill", "discovery")


class HarvestStage(Stage):
    name = "harvest"
    custom = True

    def __init__(self, executor: Optional[FetchExecutor] = None, max_jobs: int = 25, registry: Optional[Registry] = None):
        self.executor = executor
        self.max_jobs = max_jobs
        self.registry = registry

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        reg = self.registry or Registry()
        ex = self.executor or FetchExecutor(conn, reg, budget=Budget(conn))
        bud = ex.budget
        st = {"jobs": 0, "by_class": {c: 0 for c in CLASSES}, "outcomes": {}, "spawned": {"queries": 0, "urls": 0, "domains": 0}}
        exhausted = set()
        crashes = 0
        while st["jobs"] < self.max_jobs and len(exhausted) < len(ORDER):
            progressed = False
            for cls in ORDER:
                if cls in exhausted or st["jobs"] >= self.max_jobs:
                    continue
                if bud.available(cls) < 1:
                    exhausted.add(cls)
                    continue
                job = jobs.next_job(conn, cls)
                if not job:
                    continue            # 대기열이 비었을 뿐: 이번 작업이 새 작업을 만들 수 있으니 영구 제외하지 않는다
                spawned = {}

                def on_done(c, res, stats, job=job, spawned=spawned):
                    jobs.mark(c, job, res.outcome, res.detail, (stats or {}).get("fields"))
                    spawned.update(followups.after_collect(c, reg, bud, job, res, stats))

                try:
                    res, _ = collect(conn, ex, job.url, cls, origin=f"{job.table}:{job.key[:40]}", hint_work_id=job.work_hint or None, on_done=on_done)
                except BudgetExhausted:
                    exhausted.add(cls)
                    continue
                except Exception as e:  # noqa: BLE001
                    crashes += 1
                    conn.execute("BEGIN") if not conn.in_transaction else None
                    jobs.mark(conn, job, "failed_network", type(e).__name__)
                    conn.commit()
                    ctx.failed += 1
                    if crashes >= 5:
                        st["aborted"] = "too_many_crashes"
                        return st
                    continue
                st["jobs"] += 1
                st["by_class"][cls] += 1
                st["outcomes"][res.outcome] = st["outcomes"].get(res.outcome, 0) + 1
                for k, v in spawned.items():
                    st["spawned"][k] += v
                ctx.processed += 1
                progressed = True
            if not progressed:
                break
        if st["jobs"] < self.max_jobs:
            st["stopped"] = "budget_exhausted_or_queue_empty"
        if len(exhausted) == len(ORDER):
            st["budget_exhausted"] = "all_classes"
        st["budget"] = bud.snapshot()
        return st
