"""gaps 단계(빈칸 탐색기): 작품별 필수 필드 빈칸 상태 갱신 · 역검색 질의 생성 · 확장(저자→다른 작품) · 커버리지 표(out_coverage). 네트워크 없음."""
from typing import Optional

from ..bot import gaps as G, queries as Q
from ..bot.budget import Budget
from ..pipeline import Context, Stage


class GapStage(Stage):
    name = "gaps"
    custom = True

    def __init__(self, budget: Optional[Budget] = None, max_works: int = 200, expand_authors: int = 10):
        self._budget = budget
        self.max_works = max_works
        self.expand_authors = expand_authors

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        bud = self._budget or Budget(conn)
        st = {"works": 0, "opened": 0, "resolved": 0, "changed": 0, "reverse_queries": 0, "author_expansions": 0}
        for r in conn.execute("SELECT work_id FROM work ORDER BY work_id LIMIT ?", (self.max_works,)).fetchall():
            s = G.explore_work(conn, bud, r["work_id"])
            st["works"] += 1
            st["opened"] += s["opened"]
            st["resolved"] += s["resolved"]
            st["changed"] += s["changed"]
            st["reverse_queries"] += s["queries"]
        # 확장: 작품 → 저자 → 저자의 다른 작품 (출처가 준 저자 키가 있을 때만)
        for r in conn.execute("SELECT value FROM external_id WHERE scheme='openlibrary_author' AND entity_type='author' ORDER BY recorded_at LIMIT ?", (self.expand_authors * 20,)).fetchall():
            if st["author_expansions"] >= self.expand_authors:
                break
            if Q.enqueue_query(conn, bud, "ol_author_works", Q.ol_author_works_url(r["value"]), f"works of {r['value']}", "backfill", 48, origin="author-expansion"):
                st["author_expansions"] += 1
        st["coverage"] = G.refresh_coverage(conn)
        ctx.processed += st["works"]
        return st
