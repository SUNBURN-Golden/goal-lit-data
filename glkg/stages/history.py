"""7단계 상태·이력 갱신: 검증 판정을 상태에 적용하고, 내용이 바뀐 수집함 행의 옛 주장 버전을 닫는다(삭제·덮어쓰기 없음).

- confirm → confirmed_fact (DB 트리거가 외부 출처+근거 행을 다시 강제)
- keep_claim 인데 현재 confirmed → source_claim 으로 강등(충돌·출처 변경·편집 검증 상실)
- violation → unverified + 검수 대기(권리 위반 의심)
- 같은 수집함 행의 새 해시 버전이 생기면 옛 버전은 recorded_to 를 채워 닫고, 같은 표에 새 행이 하나면 superseded_by 로 연결
"""
from typing import List

from ..db import enqueue_review, log_change
from ..pipeline import Context, Item, Stage
from ..timeutil import now_iso
from .common import CLAIM_TABLES, TABLE_PK
import json


class HistoryStage(Stage):
    name = "history"
    batch_size = 100
    budget_metric = "rows"

    def next_batch(self, ctx: Context, cursor: str) -> List[Item]:
        rows = ctx.conn.execute(
            "SELECT * FROM verification_result WHERE applied=0 AND printf('%s|%s',claim_table,claim_id) > ? ORDER BY claim_table, claim_id LIMIT ?",
            (cursor, self.batch_size)).fetchall()
        return [Item(key=f"{r['claim_table']}|{r['claim_id']}", ref_id=f"{r['claim_table']}|{r['claim_id']}", payload=r) for r in rows]

    def load_ref(self, ctx, ref_id):
        t, _, i = ref_id.partition("|")
        r = ctx.conn.execute("SELECT * FROM verification_result WHERE claim_table=? AND claim_id=? AND applied=0", (t, i)).fetchone()
        return Item(key=ref_id, ref_id=ref_id, payload=r) if r else None

    def handle(self, ctx: Context, item: Item) -> None:
        conn = ctx.conn
        v = item.payload
        t, cid, verdict = v["claim_table"], v["claim_id"], v["verdict"]
        pk = TABLE_PK[t]
        row = conn.execute(f"SELECT epistemic_status FROM {t} WHERE {pk}=?", (cid,)).fetchone()
        if row is None:
            conn.execute("UPDATE verification_result SET applied=1 WHERE claim_table=? AND claim_id=?", (t, cid))
            return
        cur = row["epistemic_status"]
        new = cur
        if verdict == "confirm":
            new = "confirmed_fact"
        elif verdict == "keep_claim" and cur == "confirmed_fact":
            new = "source_claim"
        elif verdict == "violation":
            new = "unverified"
            enqueue_review(conn, "rights_violation", t, cid, f"검증 위반: {v['reasons_json']}", 95)
        if new != cur:
            conn.execute(f"UPDATE {t} SET epistemic_status=? WHERE {pk}=?", (new, cid))  # 트리거가 change_log 에 자동 기록
            log_change(conn, t, cid, "status_change", {"status": cur}, {"status": new}, f"verify:{verdict} {v['reasons_json']}", ctx.run_id)
        if verdict == "confirm" and "legacy_pointer_locator" in v["reasons_json"]:
            enqueue_review(conn, "locator_upgrade", t, cid, "확정이지만 근거 위치가 포인터형 — 문단 단위로 보강", 20)
        conn.execute("UPDATE verification_result SET applied=1 WHERE claim_table=? AND claim_id=?", (t, cid))

    def on_cycle_complete(self, ctx: Context) -> None:
        self.close_stale_versions(ctx)

    def close_stale_versions(self, ctx: Context) -> int:
        conn = ctx.conn
        now = now_iso()
        closed = 0
        inbox_ids = [r[0] for r in conn.execute("SELECT inbox_id FROM legacy_map WHERE kg_table='_row' GROUP BY inbox_id HAVING COUNT(DISTINCT content_hash) > 1")]
        for iid in inbox_ids:
            cur = conn.execute("SELECT content_hash FROM legacy_map WHERE inbox_id=? AND kg_table='_row' ORDER BY mapped_at DESC, rowid DESC LIMIT 1", (iid,)).fetchone()["content_hash"]
            for t in CLAIM_TABLES:
                pk = TABLE_PK[t]
                if t == "edition":
                    continue  # 판본 정체성 행은 닫지 않는다(속성은 bib_fact 가 닫힘)
                olds = conn.execute(
                    f"""SELECT x.{pk} AS id FROM {t} x JOIN legacy_map m ON m.kg_table='{t}' AND m.kg_id=x.{pk} AND m.inbox_id=?
                        WHERE m.content_hash<>? AND x.recorded_to IS NULL
                          AND NOT EXISTS (SELECT 1 FROM legacy_map m2 WHERE m2.inbox_id=m.inbox_id AND m2.kg_table=m.kg_table AND m2.kg_id=m.kg_id AND m2.content_hash=?)""",
                    (iid, cur, cur)).fetchall()
                news = [r[0] for r in conn.execute("SELECT kg_id FROM legacy_map WHERE inbox_id=? AND kg_table=? AND content_hash=?", (iid, t, cur))]
                for o in olds:
                    sup = news[0] if len(news) == 1 else None
                    conn.execute(f"UPDATE {t} SET recorded_to=?, superseded_by=? WHERE {pk}=?", (now, sup, o["id"]))
                    log_change(conn, t, o["id"], "supersede" if sup else "close", None, {"superseded_by": sup},
                               "수집함 원본 내용이 바뀌어 새 버전으로 대체(옛 버전은 보존)", ctx.run_id)
                    closed += 1
        ctx.detail["closed_versions"] = closed
        return closed
