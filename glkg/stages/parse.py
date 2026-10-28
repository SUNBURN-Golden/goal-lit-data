"""parse 단계: 원문 사본이 있는데 현재 추출기 버전으로 아직 파싱되지 않은 문서를 (재)파싱한다.
수집 직후 파싱은 collect()가 하므로, 이 단계는 추출기 버전이 오르거나 중간에 끊긴 문서를 메운다. 네트워크 요청 없음."""
from pathlib import Path
from typing import List, Optional

from ..bot import EXTRACTOR_VERSION, parser
from ..bot.executor import extractor_kind
from ..pipeline import Context, Item, Stage
from ..registry import Registry


class ParseStage(Stage):
    name = "parse"
    batch_size = 25
    budget_metric = None

    def __init__(self, registry: Optional[Registry] = None):
        self.reg = registry or Registry()

    def next_batch(self, ctx: Context, cursor: str) -> List[Item]:
        rows = ctx.conn.execute(
            "SELECT rowid AS rid, doc_id FROM raw_document WHERE rowid > ? AND body_stored=1 AND (parsed_version IS NULL OR parsed_version != ?) ORDER BY rowid LIMIT ?",
            (int(cursor or 0), EXTRACTOR_VERSION, self.batch_size)).fetchall()
        return [Item(key=str(r["rid"]).zfill(12), ref_id=r["doc_id"]) for r in rows]

    def load_ref(self, ctx, ref_id):
        r = ctx.conn.execute("SELECT rowid AS rid FROM raw_document WHERE doc_id=?", (ref_id,)).fetchone()
        return Item(key=str(r["rid"]).zfill(12), ref_id=ref_id) if r else None

    def handle(self, ctx: Context, item: Item) -> None:
        d = ctx.conn.execute("SELECT * FROM raw_document WHERE doc_id=?", (item.ref_id,)).fetchone()
        content = Path(d["storage_path"]).read_bytes()
        kind = extractor_kind(d["url"], d["fetch_method"], d["content_type"])
        parser.parse_document(ctx.conn, d["doc_id"], content, kind, d["url"], d["content_type"], self.reg)
