"""5단계 동일 대상 식별 (mention → 개체·주장).

원칙: ISBN이 같을 때만 같은 판본으로 합친다. 이름·제목 유사도로는 합치지 않는다(후보만 검수 대기).
출처 간 속성이 다르면 덮어쓰지 않고 두 주장을 모두 남기며 conflict + 검수 대기를 만든다.
"""
import json
import re
import unicodedata
from typing import List

from ..db import enqueue_review, log_change, sha, stable_id
from ..pipeline import Context, Item, Stage
from ..timeutil import now_iso
from . import common as C

ATTRS = ("publisher", "pub_year", "pages", "translator", "language")


def norm_isbn(s: str) -> str:
    d = re.sub(r"[^0-9Xx]", "", s or "")
    return d.upper() if len(d) in (10, 13) else ""


def norm_name(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or "")).strip()


def norm_val(v) -> str:
    return re.sub(r"\s+", "", str(v)).lower()


def edition_attrs(payload: dict) -> dict:
    out = {}
    if payload.get("publisher"):
        out["publisher"] = str(payload["publisher"]).strip()
    year = payload.get("pub_year_claim") or payload.get("pub_year")
    if not year and payload.get("pub_date"):
        m = re.search(r"(\d{4})", str(payload["pub_date"]))
        year = m.group(1) if m else None
    if year:
        out["pub_year"] = str(year)[:4]
    if payload.get("pages"):
        m = re.search(r"\d+", str(payload["pages"]))
        if m:
            out["pages"] = m.group(0)
    for k in ("translator", "language"):
        if payload.get(k):
            out[k] = str(payload[k]).strip()
    return out


class ResolveStage(Stage):
    name = "resolve"
    batch_size = 50
    budget_metric = "rows"

    def __init__(self, inbox_path=None):
        self.inbox_path = inbox_path

    def next_batch(self, ctx: Context, cursor: str) -> List[Item]:
        rows = ctx.conn.execute(
            """SELECT *, printf('%d|%s|%s', 1-is_external, created_at, mention_id) AS k FROM mention
               WHERE state='pending' AND printf('%d|%s|%s', 1-is_external, created_at, mention_id) > ?
               ORDER BY k LIMIT ?""", (cursor, self.batch_size)).fetchall()
        return [Item(key=r["k"], ref_id=r["mention_id"], payload=r) for r in rows]

    def load_ref(self, ctx, ref_id):
        r = ctx.conn.execute("SELECT * FROM mention WHERE mention_id=? AND state='pending'", (ref_id,)).fetchone()
        return Item(key="", ref_id=ref_id, payload=r) if r else None

    def on_cycle_complete(self, ctx: Context) -> None:
        self.sync_inbox_conflicts(ctx)

    # ------------------------------------------------------------------
    def handle(self, ctx: Context, item: Item) -> None:
        m = item.payload
        payload = json.loads(m["payload_json"])
        if m["kind"] == "edition":
            ref = self._edition(ctx, m, payload)
        else:
            ref = self._statement(ctx, m, payload)
        ctx.conn.execute("UPDATE mention SET state=?, resolved_ref=?, resolved_at=? WHERE mention_id=?",
                         ("resolved" if ref else "needs_review", ref, now_iso(), m["mention_id"]))

    def _common(self, ctx, m):
        src = ctx.conn.execute("SELECT * FROM source WHERE source_id=?", (m["source_id"],)).fetchone()
        return dict(source_id=m["source_id"], source_url=src["url"], evidence_id=m["evidence_id"], collected_at=src["first_seen_at"],
                    recorded_at=now_iso(), usage_basis=src["usage_basis"], retention_until=src["retention_until"], legacy_inbox_id=m["inbox_id"]), src

    # ---- 판본 ----
    def _edition(self, ctx, m, payload):
        conn = ctx.conn
        cv, src = self._common(ctx, m)
        isbn = norm_isbn(payload.get("isbn", ""))
        attrs = edition_attrs(payload)
        if not isbn and not attrs:
            return None
        status = "source_claim" if m["is_external"] else "unverified"
        if isbn:
            eid = stable_id("ed", m["work_id"], isbn)
            ident = "isbn_matched"
        else:
            eid = stable_id("ed", m["work_id"], "prov", m["source_id"])
            ident = "provisional"
        existed = conn.execute("SELECT 1 FROM edition WHERE edition_id=?", (eid,)).fetchone()
        if not existed:
            C.insert_claim(conn, "edition", dict(edition_id=eid, work_id=m["work_id"], isbn=isbn or None, label=attrs.get("publisher", ""),
                                                 identity_status=ident, epistemic_status=status, **cv), ctx.run_id)
        for pred, val in attrs.items():
            fid = stable_id("bf", eid, pred, m["source_id"], sha(str(val)))
            vf, vp = C.parse_date(val) if pred == "pub_year" else (None, "unknown")
            C.insert_claim(conn, "bib_fact", dict(fact_id=fid, subject_type="edition", subject_id=eid, predicate=pred, value_text=str(val),
                                                  valid_from=vf, valid_from_precision=vp, epistemic_status=status, **cv), ctx.run_id)
            C.map_legacy(conn, m["inbox_id"], "bib_fact", fid, m["content_hash"], now_iso())
            self._attr_conflicts(ctx, eid, pred)
        if not isbn:
            self._duplicate_candidates(ctx, m["work_id"], eid, attrs)
        return eid

    def _attr_conflicts(self, ctx, eid, pred):
        conn = ctx.conn
        rows = conn.execute(
            "SELECT fact_id, value_text FROM bib_fact WHERE subject_type='edition' AND subject_id=? AND predicate=? AND recorded_to IS NULL ORDER BY fact_id",
            (eid, pred)).fetchall()
        first = {}
        for r in rows:
            first.setdefault(norm_val(r["value_text"]), r["fact_id"])
        if len(first) < 2:
            return
        ids = sorted(first.values())
        a = ids[0]
        for b in ids[1:]:
            cid = stable_id("cf", "edition_attr", a, b)
            cur = conn.execute("INSERT OR IGNORE INTO conflict(conflict_id,kind,a_table,a_id,b_table,b_id,detail,detected_at) VALUES(?,?,?,?,?,?,?,?)",
                               (cid, "edition_attr", "bib_fact", a, "bib_fact", b, f"같은 판본({eid})의 '{pred}' 값이 출처마다 다름", now_iso()))
            if cur.rowcount:
                enqueue_review(conn, "conflict", "conflict", cid, f"판본 속성 충돌: {pred}", 70)

    def _duplicate_candidates(self, ctx, work_id, eid, attrs):
        """ISBN 없는 임시 판본이 다른 임시 판본과 출판사·연도가 같으면 '합칠지' 검수 대기(자동 병합 안 함)."""
        if "publisher" not in attrs or "pub_year" not in attrs:
            return
        conn = ctx.conn
        for r in conn.execute("SELECT edition_id FROM edition WHERE work_id=? AND edition_id<>?", (work_id, eid)).fetchall():
            o = {p["predicate"]: p["value_text"] for p in conn.execute(
                "SELECT predicate,value_text FROM bib_fact WHERE subject_type='edition' AND subject_id=? AND predicate IN ('publisher','pub_year')", (r["edition_id"],))}
            if o.get("publisher") and norm_val(o["publisher"]) == norm_val(attrs["publisher"]) and o.get("pub_year") == attrs["pub_year"]:
                a, b = sorted([eid, r["edition_id"]])
                cid = stable_id("cf", "duplicate_entity", a, b)
                cur = conn.execute("INSERT OR IGNORE INTO conflict(conflict_id,kind,a_table,a_id,b_table,b_id,detail,detected_at) VALUES(?,?,?,?,?,?,?,?)",
                                   (cid, "duplicate_entity", "edition", a, "edition", b, "출판사·연도가 같은 판본 후보(자동 병합 안 함)", now_iso()))
                if cur.rowcount:
                    enqueue_review(conn, "duplicate_entity", "conflict", cid, "같은 판본인지 확인(ISBN 없음)", 50)

    # ---- 작가 발언 ----
    def _statement(self, ctx, m, payload):
        conn = ctx.conn
        cv, src = self._common(ctx, m)
        text = (payload.get("summary") or "").strip()
        if not text:
            return None
        name = (payload.get("author_hint") or "").strip()
        if not name:
            mm = re.match(r"^([가-힣]{2,4})(?:은|는|이|가)\s", text)
            name = mm.group(1) if mm else ""
        aid, raw = None, name
        review = None
        if name:
            nn = norm_name(name)
            cands = {r["entity_id"] for r in conn.execute("SELECT entity_id FROM entity_alias WHERE entity_type='author' AND replace(alias,' ','')=?", (nn,))}
            cands |= {r["author_id"] for r in conn.execute("SELECT author_id FROM author WHERE replace(canonical_name,' ','')=?", (nn,))}
            if len(cands) == 1:
                aid = cands.pop()
            elif not cands:
                aid = stable_id("au", nn)
                conn.execute("INSERT OR IGNORE INTO author(author_id,canonical_name,created_at) VALUES(?,?,?)", (aid, name, now_iso()))
                conn.execute("INSERT OR IGNORE INTO entity_alias(entity_type,entity_id,alias,alias_kind,source_id,epistemic_status,collected_at) VALUES('author',?,?,'variant',?,'source_claim',?)",
                             (aid, name, m["source_id"], now_iso()))
            else:
                review = "author_ambiguous"
        status = "source_claim" if (m["is_external"] and aid) else "unverified"
        stid = stable_id("as", m["inbox_id"], sha(text))
        C.insert_claim(conn, "author_statement", dict(statement_id=stid, author_id=aid, author_name_raw="" if aid else raw, work_id=m["work_id"], summary=text,
                                                      statement_medium=payload.get("medium", ""), source_tier=payload.get("tier", "unknown"),
                                                      epistemic_status=status, **cv), ctx.run_id)
        C.map_legacy(conn, m["inbox_id"], "author_statement", stid, m["content_hash"], now_iso())
        if not aid:
            enqueue_review(conn, "statement_author_unresolved", "author_statement", stid,
                           "발언자를 문장에서 특정할 수 없음" if not review else "동명 후보가 여러 명", 55)
        elif not m["is_external"]:
            enqueue_review(conn, "statement_needs_external_source", "author_statement", stid, "자체 글 서술 — 발언의 외부 1차 출처 대조 필요", 55)
        return stid

    # ---- 수집함의 충돌 표시 → conflict ----
    def sync_inbox_conflicts(self, ctx: Context) -> None:
        from .. import inbox as ib
        conn = ctx.conn
        try:
            inbox = ib.open_inbox(getattr(self, "inbox_path", None))
        except Exception:
            return
        try:
            pairs = inbox.execute("SELECT id, conflict_with_id FROM items WHERE conflict=1 AND conflict_with_id<>''").fetchall()
        finally:
            inbox.close()
        for p in pairs:
            a = conn.execute("SELECT kg_table,kg_id FROM legacy_map WHERE inbox_id=? AND kg_table IN ('bib_fact','interpretation','author_statement') ORDER BY kg_id LIMIT 1", (p["id"],)).fetchone()
            b = conn.execute("SELECT kg_table,kg_id FROM legacy_map WHERE inbox_id=? AND kg_table IN ('bib_fact','interpretation','author_statement') ORDER BY kg_id LIMIT 1", (p["conflict_with_id"],)).fetchone()
            if not a or not b:
                continue
            x, y = sorted([(a["kg_table"], a["kg_id"]), (b["kg_table"], b["kg_id"])], key=lambda t: t[1])
            cid = stable_id("cf", "bib_value", x[1], y[1])
            cur = conn.execute("INSERT OR IGNORE INTO conflict(conflict_id,kind,a_table,a_id,b_table,b_id,detail,detected_at) VALUES(?,?,?,?,?,?,?,?)",
                               (cid, "bib_value", x[0], x[1], y[0], y[1], "수집함에서 충돌로 표시된 두 항목(덮어쓰지 않고 둘 다 보존)", now_iso()))
            if cur.rowcount:
                enqueue_review(conn, "conflict", "conflict", cid, "출처 충돌 — 편집자 판단 필요", 80)
        conn.commit()
