"""추출→식별→검증→이력→재계산→점검 end-to-end (합성 수집함, 임시 KG DB)."""
import json

import pytest

from glkg import config, db, rules
from glkg.pipeline import run_stage
from glkg.registry import Registry
from glkg.stages.extract import ExtractStage
from glkg.stages.resolve import ResolveStage
from glkg.stages.verify import VerifyStage
from glkg.stages.history import HistoryStage
from glkg.stages.recompute import RecomputeStage
from glkg.stages.quality import QualityStage
from glkg.stages.detect import DetectStage
from tests.helpers import ROBOTS, make_inbox, row


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(config, "WORKS_JSON", tmp_path / "works.json")
    (tmp_path / "works.json").write_text(json.dumps({"work-a": {"title": "가상 작품", "kind": "장편"}}), encoding="utf-8")
    inbox = tmp_path / "inbox.db"
    kg = db.open_kg(tmp_path / "kg.db")
    return tmp_path, inbox, kg, Registry(robots_path=ROBOTS)


def cycle(kg, inbox, reg, export):
    out = {}
    for st in (ExtractStage(inbox, reg), ResolveStage(inbox), VerifyStage(inbox), HistoryStage(), RecomputeStage(export), QualityStage(export, inbox)):
        out[st.name] = run_stage(st, kg)
    return out


def rows_fixture():
    return [
        row(1, collection_path="migrated_fact", edition_info_json=json.dumps({"isbn": "978-89-0000-000-0", "publisher": "가상출판", "pub_date": "2016-05-16", "pages": "180"}),
            extracted_facts_json=json.dumps([{"label": "수상", "claim": "가상 문학상 수상으로 소개됨"}, {"label": "평가", "claim": "구조가 독특하다는 평가가 있음"}])),
        row(2, url="https://ok-news.test/b/2", canonical_url="https://ok-news.test/b/2", edition_info_json=json.dumps({"isbn": "9788900000000", "publisher": "다른출판", "pages": "180"}),
            validation_status="source_checked"),
        row(3, collection_path="migrated_unverified", url="https://goallit.soulbound.co.kr/u", canonical_url="https://goallit.soulbound.co.kr/u", domain="goallit.soulbound.co.kr",
            source_access_level="book_intro", unverified_category="서지", evidence_locator="works.json:work-a:unverified[0]",
            extracted_facts_json=json.dumps([{"label": "미확인(서지)", "claim": "초판 출간일(1차 출처 못 읽음)"}]), validation_status="discovered"),
        row(4, url="https://blocked-news.test/x", canonical_url="https://blocked-news.test/x", domain="blocked-news.test",
            extracted_facts_json=json.dumps([{"label": "수상", "claim": "가상 수상"}])),
    ]


def test_end_to_end_and_invariants(env):
    tmp, inbox, kg, reg = env
    make_inbox(inbox, rows_fixture())
    out = cycle(kg, inbox, reg, tmp / "exp")
    assert all(o["status"] in ("ok", "partial") for o in out.values()), out
    q = lambda s, *a: kg.execute(s, a).fetchall()
    # 같은 ISBN은 판본 하나로, 출판사 값이 다르면 충돌 + 검수 대기(덮어쓰지 않음)
    assert len(q("SELECT * FROM edition WHERE isbn='9788900000000'")) == 1
    pubs = q("SELECT value_text FROM bib_fact WHERE predicate='publisher' AND subject_type='edition'")
    assert {p[0] for p in pubs} == {"가상출판", "다른출판"}
    assert q("SELECT * FROM conflict WHERE kind='edition_attr' AND status='open'")
    # 확정은 편집 검증+외부출처+근거+무충돌일 때만. 충돌 판본 속성은 확정 불가
    assert not q("SELECT * FROM bib_fact WHERE predicate='publisher' AND epistemic_status='confirmed_fact'")
    confirmed = q("SELECT * FROM bib_fact WHERE epistemic_status='confirmed_fact'")
    assert any(r["predicate"] == "수상" for r in confirmed)
    # 해석은 확정 없음, 미확인은 별도 표시
    assert not q("SELECT * FROM interpretation WHERE epistemic_status NOT IN ('source_claim','unverified','ai_inference')")
    assert q("SELECT * FROM bib_fact WHERE epistemic_status='unverified' AND predicate LIKE '미확인:%'")
    # 정책상 수집 불가 출처: 주장 추출 안 함 + 검수 대기
    assert not q("SELECT * FROM bib_fact WHERE legacy_inbox_id='fx_4'")
    assert q("SELECT * FROM review_queue WHERE kind='blocked_source_in_inbox'")
    # 인용 조각은 비어 있고 주요 결과표가 채워짐
    assert not q("SELECT * FROM evidence_passage WHERE length(quote_snippet)>0")
    assert q("SELECT * FROM out_work_edition_diff WHERE conflicting=1")
    rep = json.loads((tmp / "exp" / "quality_report.json").read_text(encoding="utf-8"))
    assert rep["pass"] is True


def test_rerun_is_idempotent(env):
    tmp, inbox, kg, reg = env
    make_inbox(inbox, rows_fixture())
    cycle(kg, inbox, reg, tmp / "exp")
    counts = lambda: {t: kg.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("source", "evidence_passage", "bib_fact", "interpretation", "edition", "conflict", "review_queue", "legacy_map", "change_log")}
    c1 = counts()
    cycle(kg, inbox, reg, tmp / "exp")
    assert counts() == c1


def test_content_change_supersedes_old_version_without_deleting(env):
    tmp, inbox, kg, reg = env
    make_inbox(inbox, rows_fixture())
    cycle(kg, inbox, reg, tmp / "exp")
    import sqlite3
    c = sqlite3.connect(str(inbox))
    c.execute("UPDATE items SET extracted_facts_json=? WHERE id='fx_1'", (json.dumps([{"label": "수상", "claim": "정정된 수상 서술"}]),))
    c.commit(); c.close()
    cycle(kg, inbox, reg, tmp / "exp")
    old = kg.execute("SELECT * FROM bib_fact WHERE value_text LIKE '가상 문학상%'").fetchone()
    assert old["recorded_to"] is not None and old["superseded_by"]
    assert kg.execute("SELECT value_text FROM bib_fact WHERE fact_id=?", (old["superseded_by"],)).fetchone()[0] == "정정된 수상 서술"
    r = run_stage(DetectStage(inbox), kg)
    assert r["status"] == "ok"


def test_source_change_blocks_confirmation(env):
    tmp, inbox, kg, reg = env
    make_inbox(inbox, rows_fixture())
    cycle(kg, inbox, reg, tmp / "exp")
    run_stage(DetectStage(inbox), kg)  # baseline
    import sqlite3
    c = sqlite3.connect(str(inbox))
    c.execute("UPDATE items SET title='바뀐 제목' WHERE id='fx_1'"); c.commit(); c.close()
    ex = run_stage(ExtractStage(inbox, reg), kg)
    det = run_stage(DetectStage(inbox), kg)
    assert det["detail"]["changed"] >= 1
    run_stage(VerifyStage(inbox), kg); run_stage(HistoryStage(), kg)
    assert not kg.execute("SELECT * FROM bib_fact WHERE legacy_inbox_id='fx_1' AND recorded_to IS NULL AND epistemic_status='confirmed_fact'").fetchall()


def test_rules_overlap_and_quote():
    assert rules.longest_common_substring_len("가나다라마바사아자차", "xx가나다라마바사아자차yy") == 10
    assert rules.check_overlap("완전히 다른 문장입니다", ["전혀 관련 없는 원문"]) < 15
    v, r = rules.evaluate("bib_fact", {"epistemic_status": "source_claim", "collected_at": "x"}, {"is_external": 1}, {"quote_snippet": "열세글자열세글자열세글자열", "locator_quality": "precise"}, "verified_ready", False)
    assert v == "violation"
    v, r = rules.evaluate("interpretation", {"epistemic_status": "source_claim"}, {"is_external": 1}, {"quote_snippet": ""}, "verified_ready", False)
    assert v == "keep_claim"
