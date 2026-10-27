"""수집+파싱 한 묶음: 가져오기 → 추출 → 라벨 → 승격을 한 트랜잭션으로 커밋한다.
(죽어도 '가져왔는데 파싱 안 됨' 상태가 남지 않는다. 원문 사본이 있는 문서는 parse 단계가 다시 처리할 수도 있다.)"""
from typing import Optional

from .executor import BudgetExhausted, FetchExecutor, FetchResult
from . import parser


def collect(conn, ex: FetchExecutor, url: str, budget_class: str, origin: str = "", trial: bool = False, hint_work_id: Optional[str] = None, on_done=None):
    """(FetchResult, parse_stats|None)"""
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT collect")
    try:
        res = ex.fetch(url, budget_class, origin=origin, trial=trial)
        stats = None
        if res.is_new:
            stats = parser.parse_document(conn, res.doc_id, res.content, res.kind, url, res.ctype, ex.reg, hint_work_id)
        elif res.outcome == "unchanged":
            d = conn.execute("SELECT parsed_version FROM raw_document WHERE doc_id=?", (res.doc_id,)).fetchone()
            from . import EXTRACTOR_VERSION
            if d and d["parsed_version"] != EXTRACTOR_VERSION:
                stats = parser.parse_document(conn, res.doc_id, res.content, res.kind, url, res.ctype, ex.reg, hint_work_id)
        if on_done:
            on_done(conn, res, stats)   # 작업 상태 갱신·후속 질의 생성도 같은 트랜잭션 → 죽어도 '가져왔는데 작업이 안 끝남' 상태가 남지 않는다
        conn.execute("RELEASE collect")
        conn.commit()
        return res, stats
    except BudgetExhausted:
        conn.execute("ROLLBACK TO collect")
        conn.execute("RELEASE collect")
        conn.commit()
        raise
    except Exception:
        conn.execute("ROLLBACK TO collect")
        conn.execute("RELEASE collect")
        conn.commit()
        raise
