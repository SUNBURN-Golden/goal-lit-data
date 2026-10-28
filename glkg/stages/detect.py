"""3단계 변경 감지: 출처별 '현재 내용 해시'(수집함 행들의 내용 해시 묶음)를 직전 스냅샷과 비교한다.
다르면 source_snapshot + change_event(content_changed)를 남기고, 해당 출처의 주장은 편집자가 확인할 때까지 확정 불가(verify 규칙).
접근 불가 감지는 fetch가 http_status를 기록할 때만 가능(현재 미지원 → docs/sources.md '지원하지 않는 범위')."""
import json
from typing import List

from .. import inbox as ib
from ..db import log_change, sha
from ..pipeline import Context, Item, Stage
from ..timeutil import now_iso


class DetectStage(Stage):
    name = "detect"
    custom = True

    def __init__(self, inbox_path=None):
        self.inbox_path = inbox_path

    def run_custom(self, ctx: Context):
        conn = ctx.conn
        inbox = ib.open_inbox(self.inbox_path)
        try:
            by_src = {}
            for r in inbox.execute("SELECT * FROM items ORDER BY rowid"):
                by_src.setdefault(r["canonical_url"] or r["url"], []).append((r["id"], ib.content_hash(r)))
        finally:
            inbox.close()
        stats = {"checked": 0, "baseline": 0, "changed": 0}
        now = now_iso()
        for canon, items in by_src.items():
            s = conn.execute("SELECT source_id FROM source WHERE canonical_url=?", (canon,)).fetchone()
            if not s:
                continue  # 아직 추출 전
            sid = s["source_id"]
            h = sha("|".join(sorted(f"{i}:{c}" for i, c in items)))
            last = conn.execute("SELECT content_hash FROM source_snapshot WHERE source_id=? ORDER BY snapshot_id DESC LIMIT 1", (sid,)).fetchone()
            stats["checked"] += 1
            if last is None:
                conn.execute("INSERT INTO source_snapshot(source_id,content_hash,checked_at,note) VALUES(?,?,?,?)", (sid, h, now, "baseline"))
                stats["baseline"] += 1
            elif last["content_hash"] != h:
                conn.execute("INSERT INTO source_snapshot(source_id,content_hash,checked_at,note) VALUES(?,?,?,?)", (sid, h, now, "changed"))
                impacted = [r[0] for r in conn.execute(
                    "SELECT DISTINCT kg_table||':'||kg_id FROM legacy_map WHERE inbox_id IN (%s) AND kg_table NOT IN ('source','_row')" % ",".join("?" * len(items)),
                    [i for i, _ in items])]
                conn.execute("INSERT INTO change_event(source_id,kind,old_hash,new_hash,detected_at,impacted_json) VALUES(?,?,?,?,?,?)",
                             (sid, "content_changed", last["content_hash"], h, now, json.dumps(impacted, ensure_ascii=False)))
                log_change(conn, "source", sid, "source_changed", {"hash": last["content_hash"]}, {"hash": h}, "detect", ctx.run_id)
                stats["changed"] += 1
            ctx.processed += 1
        return stats
