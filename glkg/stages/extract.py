"""4단계 추출: 수집함 행 → 출처·근거 위치·주장 행·언급(mention).

규칙 기반(AI 호출 0). 원문 문장은 지식그래프로 복사하지 않는다.
  - 논문 초록: 위치·해시만. 요지 문장은 비공개 수집함에만 있다.
  - 수동 클립: 출처·근거 위치만(요약은 편집자가 쓰도록 검수 대기).
  - 중복 클러스터의 비대표 행: 출처만 기록.
"""
import json
import re
from typing import List, Optional

from .. import config, inbox as ib
from ..db import enqueue_review, sha, stable_id
from ..pipeline import Context, Item, Stage
from ..registry import Registry
from ..timeutil import now_iso
from . import common as C

INTERPRETIVE_LABELS = {
    "평가", "위상", "장르 논의", "장르 해석", "연구 소개", "연구사", "해외 연구", "연출 연구", "작자 논의", "영역본 논쟁",
    "국내외 반응 보도", "완결 반응", "플랫폼과 위상", "모티프", "배경", "내용", "구조", "작품 구조", "설정", "시대", "인기 지표",
}
SKIP_FACT_LABELS = {"서지 검증", "서지 보강(승인 대기)", "비평 테제", "대조 반론", "수동 클립 발췌", "학술 메타데이터 검증", "본문 핵심 요지"}
_works_cache = {}


def work_title(slug: str, info: dict) -> str:
    if info.get("title"):
        return info["title"]
    if not _works_cache:
        try:
            _works_cache.update(json.loads(config.WORKS_JSON.read_text(encoding="utf-8")))
        except Exception:
            _works_cache["_"] = {}
    return (_works_cache.get(slug) or {}).get("title") or slug


def ensure_work(conn, slug: str, info: dict, now: str) -> str:
    wid = stable_id("wk", slug)
    conn.execute("INSERT OR IGNORE INTO work(work_id,slug,canonical_title,kind,created_at) VALUES(?,?,?,?,?)",
                 (wid, slug, work_title(slug, info), (_works_cache.get(slug) or {}).get("kind", ""), now))
    conn.execute("INSERT OR IGNORE INTO entity_alias(entity_type,entity_id,alias,alias_kind,epistemic_status,collected_at) VALUES('work',?,?,'legacy_slug','source_claim',?)",
                 (wid, slug, now))
    return wid


class ExtractStage(Stage):
    name = "extract"
    batch_size = 50
    budget_metric = None  # 실제로 처리한 행만 handle() 안에서 센다(변경 없는 행 재확인은 무료)

    def __init__(self, inbox_path=None, registry: Optional[Registry] = None):
        self.inbox_path = inbox_path
        self.reg = registry or Registry()
        self._inbox = None

    @property
    def inbox(self):
        if self._inbox is None:
            self._inbox = ib.open_inbox(self.inbox_path)
        return self._inbox

    def next_batch(self, ctx: Context, cursor: str) -> List[Item]:
        from ..registry import sync_to_db
        sync_to_db(ctx.conn, self.reg)  # source.registry_key 외래키 보장(멱등)
        rows = ib.rows_after(self.inbox, int(cursor or 0), self.batch_size)
        return [Item(key=str(r["_rowid"]), ref_id=r["id"], payload=r) for r in rows]

    def load_ref(self, ctx, ref_id):
        r = ib.get_row(self.inbox, ref_id)
        return Item(key=str(r["_rowid"]), ref_id=ref_id, payload=r) if r else None

    # ------------------------------------------------------------------
    def handle(self, ctx: Context, item: Item) -> None:
        conn, row = ctx.conn, item.payload
        chash = ib.content_hash(row)
        if conn.execute("SELECT 1 FROM legacy_map WHERE inbox_id=? AND kg_table='_row' AND content_hash=?", (row["id"], chash)).fetchone():
            return  # 이미 같은 내용으로 처리함
        ctx.require("rows")
        now = now_iso()
        policy = self.reg.policy_for_url(row["url"])
        sid = C.upsert_source(conn, row, policy, self.reg, now)
        eid = C.upsert_evidence(conn, row, sid, row["evidence_locator"], now,
                                passage_text=(row["critical_thesis"] if row["source_access_level"] == "paper_abstract" else ""))
        C.map_legacy(conn, row["id"], "source", sid, chash, now)
        if not policy["may_collect"]:
            enqueue_review(conn, "blocked_source_in_inbox", "source", sid, f"정책상 수집 불가 출처({policy['registry_key']})가 수집함에 있음 — 주장 추출 안 함", 90)
            return self._done(conn, row, chash, now)
        if not row["is_cluster_primary"]:
            return self._done(conn, row, chash, now)
        slug = (row["target_work_slug"] or "").strip()
        if not slug or slug == "general":
            return self._done(conn, row, chash, now)

        info = ib.json_or(row["work_info_json"], {})
        wid = ensure_work(conn, slug, info, now)
        ctx_vals = dict(
            source_id=sid, source_url=row["url"], evidence_id=eid, collected_at=row["collected_at"] or now, recorded_at=now,
            usage_basis=policy["usage_basis"], retention_until=self.reg.retention_until(policy["registry_key"], row["collected_at"] or now),
            legacy_inbox_id=row["id"],
        )
        internal = not policy["is_external"]
        base_status = "unverified" if internal else "source_claim"
        facts = ib.json_or(row["extracted_facts_json"], [])
        path = row["collection_path"]
        pub_from, pub_prec = C.parse_date(row["published_at"])

        def add(table, vals, key):
            if C.insert_claim(conn, table, vals, ctx.run_id):
                ctx.detail["claims"] = ctx.detail.get("claims", 0) + 1
            C.map_legacy(conn, row["id"], table, key, chash, now)

        if path == "migrated_unverified":
            self._unverified(conn, row, wid, facts, ctx_vals, add)
        elif path == "manual_clip":
            enqueue_review(conn, "clip_needs_summary", "source", sid, "수동 클립: 편집자가 요지를 직접 쓰고 근거 위치를 보강해야 함", 40)
        elif row["source_access_level"] == "paper_abstract":
            self._paper(conn, row, wid, info, facts, ctx_vals, pub_prec, add)
        else:
            for i, f in enumerate(facts):
                label, claim = (f.get("label") or "").strip(), (f.get("claim") or "").strip()
                if not claim or label in SKIP_FACT_LABELS:
                    continue
                if path == "auto":
                    continue  # 자동 발견 페이지의 요약문은 검수 전까지 주장으로 올리지 않는다(후보 상태)
                if label in INTERPRETIVE_LABELS:
                    iid = stable_id("in", row["id"], i, sha(claim))
                    add("interpretation", dict(interp_id=iid, work_id=wid, kind="interpretation", attributed_to_name=row["outlet"] or "",
                                               attribution_verified=0, summary=claim, valid_from=pub_from, valid_from_precision=pub_prec,
                                               epistemic_status=base_status, **ctx_vals), iid)
                else:
                    fid = stable_id("bf", row["id"], i, sha(claim))
                    add("bib_fact", dict(fact_id=fid, subject_type="work", subject_id=wid, predicate=label or "fact", value_text=claim,
                                         epistemic_status=base_status, **ctx_vals), fid)
            if path == "column_evidence" or internal:
                self._essay(conn, row, wid, ctx_vals, pub_from, pub_prec, add)
            self._mentions(conn, row, wid, sid, eid, chash, internal, policy, now)
        self._done(conn, row, chash, now)

    # ------------------------------------------------------------------
    def _done(self, conn, row, chash, now):
        C.map_legacy(conn, row["id"], "_row", "-", chash, now)

    def _unverified(self, conn, row, wid, facts, cv, add):
        cat = row["unverified_category"]
        for i, f in enumerate(facts):
            claim = (f.get("claim") or "").strip()
            label = (f.get("label") or "").strip()
            if not claim:
                continue
            if cat == "작품 해석":
                iid = stable_id("in", row["id"], i, sha(claim))
                add("interpretation", dict(interp_id=iid, work_id=wid, kind="interpretation", summary=claim, epistemic_status="unverified",
                                           note="미확인 단서(근거 출처를 아직 못 읽음)", **cv), iid)
            elif cat == "발언":
                sid2 = stable_id("as", row["id"], i, sha(claim))
                add("author_statement", dict(statement_id=sid2, author_id=None, author_name_raw="", work_id=wid, summary=claim,
                                             epistemic_status="unverified", note="발언 1차 출처 미확인", **cv), sid2)
                enqueue_review(conn, "unverified_statement", "author_statement", sid2, "작가 발언의 1차 출처를 찾아야 함", 60)
            else:
                fid = stable_id("bf", row["id"], i, sha(claim))
                add("bib_fact", dict(fact_id=fid, subject_type="work", subject_id=wid, predicate=f"미확인:{label or '서지'}", value_text=claim,
                                     epistemic_status="unverified", note="확인 못 한 서지 항목", **cv), fid)

    def _paper(self, conn, row, wid, info, facts, cv, prec, add):
        authors = info.get("paper_authors") or []
        year = re.search(r"\((\d{4})\)", row["outlet"] or "")
        vf, vp = (year.group(1), "year") if year else (None, "unknown")
        if not facts:
            return
        for i, f in enumerate(facts):
            claim = (f.get("claim") or "").strip()
            iid = stable_id("in", row["id"], i, sha(claim))
            if f.get("label") == "학술 메타데이터 검증":
                add("interpretation", dict(interp_id=iid, work_id=wid, kind="scholarly_ref", attributed_to_name=", ".join(authors),
                                           attribution_verified=1 if authors else 0, summary="", summary_withheld_reason="abstract_private_store_only",
                                           note=f"논문: {row['title']}"[:300], valid_from=vf, valid_from_precision=vp,
                                           epistemic_status="source_claim", **cv), iid)
            elif claim:  # 이관 사실(연구 소개 등): 우리가 쓴 요지 문장
                add("interpretation", dict(interp_id=iid, work_id=wid, kind="scholarly_ref", attributed_to_name=row["outlet"] or ", ".join(authors),
                                           attribution_verified=1 if authors else 0, summary=claim, valid_from=vf, valid_from_precision=vp,
                                           epistemic_status="source_claim", **cv), iid)

    def _essay(self, conn, row, wid, cv, pub_from, pub_prec, add):
        for kind, col in (("critique", "critical_thesis"), ("counter", "counter_argument")):
            txt = (row[col] or "").strip()
            if txt:
                iid = stable_id("in", row["id"], kind, sha(txt))
                add("interpretation", dict(interp_id=iid, work_id=wid, kind=kind, attributed_to_name=row["outlet"] or "골릿 자체 글",
                                           attribution_verified=0, summary=txt, valid_from=pub_from, valid_from_precision=pub_prec,
                                           epistemic_status="unverified", note="자체 글: 외부 1차 출처로 대조 전", **cv), iid)

    def _mentions(self, conn, row, wid, sid, eid, chash, internal, policy, now):
        ed = ib.json_or(row["edition_info_json"], {})
        if isinstance(ed, dict) and any(ed.values()):
            mid = stable_id("mn", row["id"], "edition", sha(json.dumps(ed, sort_keys=True, ensure_ascii=False)))
            conn.execute(
                "INSERT OR IGNORE INTO mention(mention_id,kind,work_id,inbox_id,source_id,evidence_id,is_external,payload_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (mid, "edition", wid, row["id"], sid, eid, 0 if internal else 1, json.dumps(ed, ensure_ascii=False), chash, now))
        st = (row["author_statement"] or "").strip()
        if st:
            payload = {"summary": st, "tier": "secondary" if row["source_access_level"] != "author_interview" else "primary",
                       "author_hint": ib.json_or(row["work_info_json"], {}).get("author", ""), "medium": row["source_access_level"]}
            mid = stable_id("mn", row["id"], "statement", sha(st))
            conn.execute(
                "INSERT OR IGNORE INTO mention(mention_id,kind,work_id,inbox_id,source_id,evidence_id,is_external,payload_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (mid, "author_statement", wid, row["id"], sid, eid, 0 if internal else 1, json.dumps(payload, ensure_ascii=False), chash, now))
