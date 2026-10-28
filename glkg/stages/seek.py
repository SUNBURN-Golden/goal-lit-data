"""seek 단계(소스 탐색기): 질의 조합 생성 · 작품별 판본/비평 질의 · 갱신 주기 지난 작업 재큐. 네트워크 요청 없음(DB 행만 추가).
- 장르×연도: Open Library 주제 목록(허용 경로) 연도별
- 장르×연도×키워드: OpenAlex 한국어 논문 검색(비평·서평·해석·인터뷰)
- 작품→판본, 작품→비평: 작품마다 판본 질의(backfill), 제목 검색(OpenAlex·Crossref, backfill)
대기열이 넘치지 않게 상한을 두고, 하루 후보 500건 상한(discovered)을 센다."""
from typing import Optional

from ..bot import queries as Q
from ..bot.budget import Budget
from ..bot.jobs import requeue_stale
from ..pipeline import Context, Stage

MAX_QUEUED = {"discovery": 80, "backfill": 120}
SEED_PER_RUN = 12


class SeekStage(Stage):
    name = "seek"
    custom = True

    def __init__(self, budget: Optional[Budget] = None, seed_per_run: int = SEED_PER_RUN, subjects=None, keywords=None, years=None):
        self._budget = budget
        self.seed_per_run = seed_per_run
        self.subjects = subjects or Q.SUBJECTS
        self.keywords = Q.KEYWORDS if keywords is None else keywords
        self.years = years or list(range(Q.YEAR_MAX, Q.YEAR_MIN - 1, -1))

    def _queued(self, conn, cls) -> int:
        return conn.execute("SELECT count(*) FROM discovery_query WHERE state='queued' AND budget_class=?", (cls,)).fetchone()[0]

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        bud = self._budget or Budget(conn)
        st = {"combos": 0, "work_queries": 0, "requeued": 0}
        # 1) 장르 × 연도 (최근 연도 우선) + 장르 × 연도 × 키워드
        room = min(self.seed_per_run, MAX_QUEUED["discovery"] - self._queued(conn, "discovery"))
        for y in self.years:
            for subj, kor in self.subjects.items():
                if room <= 0:
                    break
                if not bud.discovered_left():
                    st["discovered_cap"] = True
                    break
                if Q.enqueue_query(conn, bud, "ol_subject", Q.ol_subject_url(subj, y), f"{subj} {y}", "discovery", 50 + (y - Q.YEAR_MIN), origin="combo:genre×year"):
                    st["combos"] += 1
                    room -= 1
            for kw in self.keywords:
                if room <= 0:
                    break
                for subj, kor in list(self.subjects.items())[:1]:   # 키워드는 대표 장르(소설)만: "한국 소설 {키워드}"
                    if Q.enqueue_query(conn, bud, "oa_combo", Q.oa_search_url(f"한국 {kor} {kw}", y, korean=True), f"한국 {kor} {kw} {y}", "discovery", 40 + (y - Q.YEAR_MIN), origin="combo:genre×year×keyword"):
                        st["combos"] += 1
                        room -= 1
            if room <= 0:
                break
        # 2) 작품마다: 판본 질의(OL 키가 있으면 followups 가 이미 만듦) + 비평 질의(제목 검색)
        if self._queued(conn, "backfill") < MAX_QUEUED["backfill"]:
            rows = conn.execute(
                """SELECT w.work_id, w.canonical_title FROM work w
                   WHERE NOT EXISTS (SELECT 1 FROM discovery_query q WHERE q.kind='oa_title' AND json_extract(q.params_json,'$.work_hint')=w.work_id)
                   ORDER BY w.created_at DESC LIMIT 20""").fetchall()
            for r in rows:
                title = r["canonical_title"]
                if Q.enqueue_query(conn, bud, "oa_title", Q.oa_search_url(f'"{title}"'), f"critics of {title}", "backfill", 45, origin="work→critics", work_hint=r["work_id"]):
                    st["work_queries"] += 1
                if Q.enqueue_query(conn, bud, "cr_title", Q.cr_search_url(title), f"crossref {title}", "backfill", 40, origin="work→critics", work_hint=r["work_id"]):
                    st["work_queries"] += 1
        st["requeued"] = requeue_stale(conn)
        ctx.processed += st["combos"] + st["work_queries"]
        return st
