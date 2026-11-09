"""6단계 근거 검증: 주장 행마다 판정(verdict)만 만든다. 상태 변경은 history 단계가 적용."""
import json
from typing import List, Optional

from .. import inbox as ib
from ..pipeline import Context, Item, Stage
from .. import rules
from ..timeutil import now_iso
from .common import CLAIM_TABLES, TABLE_PK


class VerifyStage(Stage):
    name = "verify"
    batch_size = 100
    budget_metric = None  # 준태님 11/5 지시: 검증은 일일 rows 예산 없음. 다른 단계의 수집 한도는 그대로 둔다.

    def __init__(self, inbox_path=None):
        self.inbox_path = inbox_path
        self._inbox = None

    @property
    def inbox(self):
        if self._inbox is None:
            try:
                self._inbox = ib.open_inbox(self.inbox_path)
            except Exception:
                self._inbox = False
        return self._inbox

    def next_batch(self, ctx: Context, cursor: str) -> List[Item]:
        tbl, _, last = cursor.partition("|")
        tables = list(CLAIM_TABLES)
        start = tables.index(tbl) if tbl in tables else 0
        for t in tables[start:]:
            pk = TABLE_PK[t]
            lo = last if t == tbl else ""
            rows = ctx.conn.execute(f"SELECT * FROM {t} WHERE recorded_to IS NULL AND {pk} > ? ORDER BY {pk} LIMIT ?", (lo, self.batch_size)).fetchall()
            if rows:
                return [Item(key=f"{t}|{r[pk]}", ref_id=f"{t}|{r[pk]}", payload=(t, r)) for r in rows]
        return []

    def load_ref(self, ctx, ref_id):
        t, _, i = ref_id.partition("|")
        if t not in TABLE_PK:
            return None
        r = ctx.conn.execute(f"SELECT * FROM {t} WHERE {TABLE_PK[t]}=?", (i,)).fetchone()
        return Item(key=ref_id, ref_id=ref_id, payload=(t, r)) if r else None

    def handle(self, ctx: Context, item: Item) -> None:
        conn = ctx.conn
        table, claim = item.payload
        claim = dict(claim)
        cid = claim[TABLE_PK[table]]
        src = conn.execute("SELECT * FROM source WHERE source_id=?", (claim.get("source_id"),)).fetchone() if claim.get("source_id") else None
        ev = conn.execute("SELECT * FROM evidence_passage WHERE evidence_id=?", (claim.get("evidence_id"),)).fetchone() if claim.get("evidence_id") else None
        status = None
        if claim.get("legacy_inbox_id") and self.inbox:
            r = ib.get_row(self.inbox, claim["legacy_inbox_id"])
            status = r["validation_status"] if r else None
            conflict_flag = bool(r and r["conflict"])
        else:
            conflict_flag = False
        open_conflict = conflict_flag or bool(conn.execute(
            "SELECT 1 FROM conflict WHERE status='open' AND ((a_table=? AND a_id=?) OR (b_table=? AND b_id=?)) LIMIT 1", (table, cid, table, cid)).fetchone())
        overlap = 0
        if src is not None and src["is_external"] and src["kind"] in ("paper", "clip") and claim.get("legacy_inbox_id") and self.inbox:
            r = ib.get_row(self.inbox, claim["legacy_inbox_id"])
            mine = claim.get("summary") or claim.get("value_text") or ""
            if r and mine:
                overlap = rules.check_overlap(mine, [r["critical_thesis"] or "", r["counter_argument"] or ""])
        source_changed = bool(src and conn.execute("SELECT 1 FROM change_event WHERE source_id=? AND kind='content_changed' AND state='open' LIMIT 1", (src["source_id"],)).fetchone())
        verdict, reasons = rules.evaluate(table, claim, dict(src) if src else None, dict(ev) if ev else None, status, open_conflict, overlap, source_changed)
        old = conn.execute("SELECT verdict, reasons_json FROM verification_result WHERE claim_table=? AND claim_id=?", (table, cid)).fetchone()
        rj = json.dumps(reasons, ensure_ascii=False)
        if old and old["verdict"] == verdict and old["reasons_json"] == rj:
            conn.execute("UPDATE verification_result SET checked_at=? WHERE claim_table=? AND claim_id=?", (now_iso(), table, cid))
        else:
            conn.execute("""INSERT INTO verification_result(claim_table,claim_id,verdict,reasons_json,checked_at,applied) VALUES(?,?,?,?,?,0)
                            ON CONFLICT(claim_table,claim_id) DO UPDATE SET verdict=excluded.verdict, reasons_json=excluded.reasons_json, checked_at=excluded.checked_at, applied=0""",
                         (table, cid, verdict, rj, now_iso()))
