"""문학 지식그래프 단계: 주제 개체·작품-주제 연결·대립 해석 관계.

규칙
  - 주제 연결은 근거 위치(evidence_passage)가 있는 해석/비평 문장에 주제어(또는 별칭)가 실제로 나올 때만 만든다.
  - 상태는 근거 해석의 상태를 넘지 않는다: 근거 해석이 source_claim 이면 source_claim, 아니면 unverified.
  - AI 제안 주제는 propose_ai_theme() 로 ai_inference 표에만 남기고 승격하지 않는다(트리거가 막음).
  - 대립(opposes)은 같은 작품에서 비평(critique)과 반론(counter)이 짝을 이룰 때, 양쪽 모두 작성자·근거가 있을 때만 만든다.
    양쪽 다 source_claim 이면 source_claim, 아니면 unverified. 어느 쪽도 지우지 않는다.
배치형 단계라 커서(interp_id)는 배치 커밋 뒤에만 전진하고, 실패 항목은 공통 실패 큐로 재시도된다.
"""
import json
from pathlib import Path
from typing import List

from .. import config
from ..db import log_change, stable_id
from ..pipeline import Context, Item, Stage
from ..timeutil import now_iso
from . import common as C

LEXICON_PATH = Path(__file__).resolve().parent.parent.parent / "sources" / "themes.json"


def load_lexicon(path=None):
    return json.loads(Path(path or LEXICON_PATH).read_text(encoding="utf-8"))["themes"]


def match_themes(text: str, lexicon) -> List[dict]:
    low = (text or "").lower()
    return [t for t in lexicon if any(a.lower() in low for a in [t["label"]] + t["aliases"])]


def ensure_theme(conn, t, now):
    tid = stable_id("th", t["key"])
    cur = conn.execute("INSERT OR IGNORE INTO theme(theme_id,label,created_at,epistemic_status,lexicon_key,usage_basis) VALUES(?,?,?,?,?,?)",
                       (tid, t["label"], now, "unverified", t["key"], "own_work"))
    if cur.rowcount:
        log_change(conn, "theme", tid, "insert", None, {"label": t["label"]}, "themes: 편집 어휘")
        for a in t["aliases"]:
            conn.execute("INSERT OR IGNORE INTO entity_alias(entity_type,entity_id,alias,alias_kind,epistemic_status,collected_at) VALUES('theme',?,?,?, 'unverified', ?)",
                         (tid, a, "translation" if a.isascii() else "variant", now))
    return tid


def propose_ai_theme(conn, work_id: str, theme_label: str, rationale: str, model_tag: str) -> str:
    """AI 제안 주제: ai_inference 로만 저장. work_theme 로는 절대 가지 않는다."""
    iid = stable_id("ai", work_id, theme_label, model_tag)
    conn.execute("INSERT OR IGNORE INTO ai_inference(inference_id,subject_table,subject_id,proposed_relation,rationale_summary,model_tag,collected_at) VALUES(?,?,?,?,?,?,?)",
                 (iid, "work", work_id, f"work_theme:{theme_label}", rationale[:300], model_tag, now_iso()))
    return iid


class ThemeStage(Stage):
    name = "themes"
    batch_size = 100
    budget_metric = None

    def __init__(self, lexicon_path=None):
        self.lexicon = load_lexicon(lexicon_path)

    def next_batch(self, ctx: Context, cursor: str):
        rows = ctx.conn.execute(
            "SELECT interp_id FROM interpretation WHERE interp_id>? AND recorded_to IS NULL ORDER BY interp_id LIMIT ?",
            (cursor or "", self.batch_size)).fetchall()
        return [Item(key=r["interp_id"], ref_id=r["interp_id"]) for r in rows]

    def load_ref(self, ctx, ref_id):
        return Item(key=ref_id, ref_id=ref_id)

    def handle(self, ctx: Context, item: Item):
        conn, now = ctx.conn, now_iso()
        i = conn.execute("SELECT * FROM interpretation WHERE interp_id=?", (item.ref_id,)).fetchone()
        if i is None or i["evidence_id"] is None:
            return
        text = " ".join([i["summary"] or "", i["note"] or "", i["stance"] or ""])
        status = "source_claim" if i["epistemic_status"] == "source_claim" and i["source_id"] else "unverified"
        for t in match_themes(text, self.lexicon):
            tid = ensure_theme(conn, t, now)
            wt = stable_id("wt", i["work_id"], tid, i["interp_id"])
            ok = C.insert_claim(conn, "work_theme", dict(
                wt_id=wt, work_id=i["work_id"], theme_id=tid, source_id=i["source_id"], source_url=i["source_url"], evidence_id=i["evidence_id"],
                collected_at=now, valid_from=i["valid_from"], valid_from_precision=i["valid_from_precision"], recorded_at=now,
                epistemic_status=status, usage_basis=i["usage_basis"], retention_until=i["retention_until"],
                note=f"근거 해석 {i['interp_id']} 에 주제어 출현"), ctx.run_id)
            if ok:
                ctx.detail["work_theme_new"] = ctx.detail.get("work_theme_new", 0) + 1
        if i["kind"] == "counter":
            self._oppose(ctx, i, now)

    def _oppose(self, ctx, counter, now):
        conn = ctx.conn
        if not counter["attributed_to_name"]:
            return
        peers = conn.execute(
            """SELECT * FROM interpretation WHERE work_id=? AND kind='critique' AND recorded_to IS NULL AND evidence_id IS NOT NULL
               AND attributed_to_name<>'' AND (legacy_inbox_id IS ? OR source_id IS ?)""",
            (counter["work_id"], counter["legacy_inbox_id"], counter["source_id"])).fetchall()
        for p in peers:
            both = counter["epistemic_status"] == "source_claim" and p["epistemic_status"] == "source_claim"
            rid = stable_id("ir", p["interp_id"], counter["interp_id"], "opposes")
            cur = conn.execute(
                """INSERT OR IGNORE INTO interpretation_relation(rel_id,a_interp_id,b_interp_id,relation,source_id,source_url,evidence_id,collected_at,
                   valid_from,valid_from_precision,recorded_at,epistemic_status,usage_basis,retention_until,note)
                   VALUES(?,?,?,'opposes',?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, p["interp_id"], counter["interp_id"], counter["source_id"], counter["source_url"], counter["evidence_id"], now,
                 counter["valid_from"], counter["valid_from_precision"], now, "source_claim" if both else "unverified",
                 counter["usage_basis"], counter["retention_until"],
                 f"양쪽 보존: {p['attributed_to_name']} ↔ {counter['attributed_to_name']}"[:300]))
            if cur.rowcount:
                log_change(conn, "interpretation_relation", rid, "insert", None, {"status": "source_claim" if both else "unverified"}, "themes: 대립 해석", ctx.run_id)
                ctx.detail["opposes_new"] = ctx.detail.get("opposes_new", 0) + 1
