"""assess 단계(출처 평가기): 새 후보 도메인을 시험 수집 1건(예산 discovery 클래스, robots 통과 시)으로 점수화해 등록/후순위/차단."""
from typing import Optional

from ..bot import evaluator as EV, queries as Q
from ..bot.budget import Budget
from ..bot.collect import collect
from ..bot.executor import BudgetExhausted, FetchExecutor
from ..pipeline import Context, Stage
from ..registry import Registry


class AssessStage(Stage):
    name = "assess"
    custom = True

    def __init__(self, executor: Optional[FetchExecutor] = None, per_run: int = 3):
        self.executor = executor
        self.per_run = per_run

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        ex = self.executor or FetchExecutor(conn, Registry(), budget=Budget(conn))
        bud = ex.budget
        st = {"trialed": 0, "registered": 0, "deprioritized": 0, "blocked": 0, "retry_later": 0}
        rows = conn.execute("SELECT * FROM candidate_source WHERE state='new' ORDER BY first_seen_at LIMIT ?", (self.per_run,)).fetchall()
        for r in rows:
            if bud.available("discovery") < 2:
                st["budget_exhausted"] = "discovery"
                break
            try:
                res, _ = collect(conn, ex, r["sample_url"], "discovery", origin="trial:" + r["domain"], trial=True)
            except BudgetExhausted:
                st["budget_exhausted"] = "discovery"
                break
            conn.execute("BEGIN") if not conn.in_transaction else None
            state = EV.evaluate_outcome(conn, r, res)
            if state == "registered":
                # 공식 사이트 → 링크 → 첨부로 이어가기: 시험 페이지 재수집(파싱·링크 확장)과 robots 의 Sitemap 선언
                Q.enqueue_url(conn, None, r["sample_url"], "discovery", 1, 55, origin="registered:" + r["domain"])
                for sm in (ex.gate.sitemaps.get(r["domain"]) or ex.gate.sitemaps.get("www." + r["domain"]) or [])[:2]:
                    Q.enqueue_url(conn, None, sm, "discovery", 1, 50, origin="sitemap:" + r["domain"])
            conn.commit()
            st["trialed"] += 1
            st[{"new": "retry_later"}.get(state, state)] += 1
            ctx.processed += 1
        return st
