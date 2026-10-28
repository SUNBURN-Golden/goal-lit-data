"""문학 지식그래프(주제·근거·대립 해석·통합 뷰) — 비식별 합성 픽스처."""
import json
import sqlite3
from pathlib import Path

import pytest

from glkg import config, db
from glkg.pipeline import run_stage
from glkg.registry import Registry, sync_to_db
from glkg.stages.themes import ThemeStage, propose_ai_theme, match_themes, load_lexicon
from glkg.stages.quality import QualityStage
from tests.helpers import ROBOTS

FX = json.loads((Path(__file__).parent / "fixtures" / "literary_kg.json").read_text(encoding="utf-8"))
NOW = "2026-10-28T10:00:00+09:00"


@pytest.fixture()
def kg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_DIR", tmp_path / "locks")
    c = db.open_kg(tmp_path / "kg.db")
    sync_to_db(c, Registry(robots_path=ROBOTS))
    c.execute("INSERT INTO work(work_id,slug,canonical_title,created_at) VALUES('w_fx','work-fx','가상 작품',?)", (NOW,))
    for s in (FX["source"], FX["source2"]):
        c.execute("""INSERT INTO source(source_id,registry_key,url,canonical_url,domain,outlet,access_level,first_seen_at,last_seen_at)
                     VALUES(?,'press_robots_ok',?,?,?,?,'press_article',?,?)""", (s["source_id"], s["url"], s["url"], s["domain"], s["outlet"], NOW, NOW))
    for i in FX["interpretations"]:
        ev = "ev_" + i["interp_id"]
        c.execute("INSERT INTO evidence_passage(evidence_id,source_id,locator,locator_type,quote_snippet,collected_at) VALUES(?,?,?,'paragraph',?,?)",
                  (ev, i["src"], i["loc"], i["quote"], NOW))
        c.execute("""INSERT INTO interpretation(interp_id,work_id,kind,attributed_to_name,summary,source_id,evidence_id,collected_at,valid_from,recorded_at,
                     epistemic_status,legacy_inbox_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (i["interp_id"], "w_fx", i["kind"], i["attributed_to_name"], i["summary"], i["src"], ev, NOW, "2026", NOW, i["status"], "fx_row1" if i["src"] == "src_fx1" else "fx_row2"))
    c.commit()
    return c


def test_lexicon_matches_aliases():
    lex = load_lexicon()
    keys = {t["key"] for t in match_themes("국가폭력과 애도", lex)}
    assert {"trauma_history", "grief_mourning"} <= keys


def test_theme_stage_links_with_evidence_and_status(kg):
    r = run_stage(ThemeStage(), kg)
    assert r["status"] in ("ok", "done", "completed") or r.get("processed", 0) >= 0
    rows = kg.execute("SELECT w.epistemic_status, w.evidence_id, t.label FROM work_theme w JOIN theme t ON t.theme_id=w.theme_id").fetchall()
    assert rows and all(r["evidence_id"] for r in rows)
    labels = {r["label"]: r["epistemic_status"] for r in rows}
    assert labels["애도"] == "source_claim"
    assert labels["번역"] == "unverified"  # 근거 해석이 미확인이면 연결도 미확인
    assert kg.execute("SELECT COUNT(*) FROM entity_alias WHERE entity_type='theme'").fetchone()[0] > 0


def test_idempotent_rerun(kg):
    run_stage(ThemeStage(), kg)
    n1 = kg.execute("SELECT COUNT(*) FROM work_theme").fetchone()[0]
    run_stage(ThemeStage(), kg)
    assert kg.execute("SELECT COUNT(*) FROM work_theme").fetchone()[0] == n1


def test_opposing_pair_keeps_both_sides(kg):
    run_stage(ThemeStage(), kg)
    rel = kg.execute("SELECT * FROM interpretation_relation WHERE relation='opposes'").fetchall()
    assert len(rel) == 1 and rel[0]["epistemic_status"] == "source_claim"
    assert {rel[0]["a_interp_id"], rel[0]["b_interp_id"]} == {"in_fx_a", "in_fx_b"}
    assert kg.execute("SELECT COUNT(*) FROM interpretation WHERE interp_id IN ('in_fx_a','in_fx_b') AND recorded_to IS NULL").fetchone()[0] == 2


def test_opposes_rejected_without_author(kg):
    with pytest.raises(sqlite3.IntegrityError):
        kg.execute("INSERT INTO interpretation_relation(rel_id,a_interp_id,b_interp_id,relation,collected_at,recorded_at) VALUES('x','in_fx_a','in_fx_c','opposes',?,?)", (NOW, NOW))


def test_work_theme_claim_requires_evidence(kg):
    kg.execute("INSERT INTO theme(theme_id,label,created_at) VALUES('th_x','가상 주제',?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        kg.execute("INSERT INTO work_theme(wt_id,work_id,theme_id,collected_at,recorded_at,epistemic_status) VALUES('wt_x','w_fx','th_x',?,?,'source_claim')", (NOW, NOW))


def test_ai_theme_stays_inference_and_never_promoted(kg):
    iid = propose_ai_theme(kg, "w_fx", "고독", "모델 제안", "rule-test")
    assert kg.execute("SELECT epistemic_status FROM ai_inference WHERE inference_id=?", (iid,)).fetchone()[0] == "ai_inference"
    with pytest.raises(sqlite3.IntegrityError):
        kg.execute("UPDATE ai_inference SET review_state='promoted', promoted_to_table='work_theme', promoted_to_id='z', reviewed_by='ed' WHERE inference_id=?", (iid,))
    kg.execute("INSERT INTO theme(theme_id,label,created_at) VALUES('th_y','AI주제',?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        kg.execute("INSERT INTO work_theme(wt_id,work_id,theme_id,collected_at,recorded_at,epistemic_status) VALUES('wt_y','w_fx','th_y',?,?,'ai_inference')", (NOW, NOW))


def test_correction_history_recorded(kg):
    run_stage(ThemeStage(), kg)
    wt = kg.execute("SELECT wt_id FROM work_theme WHERE epistemic_status='source_claim' LIMIT 1").fetchone()[0]
    kg.execute("UPDATE work_theme SET epistemic_status='unverified', note='정정' WHERE wt_id=?", (wt,))
    assert kg.execute("SELECT COUNT(*) FROM change_log WHERE row_id=? AND op='correct'", (wt,)).fetchone()[0] == 1


def test_unified_item_view_fields(kg):
    run_stage(ThemeStage(), kg)
    r = kg.execute("SELECT * FROM v_kg_item WHERE item_type='work_theme' AND epistemic_status='source_claim' LIMIT 1").fetchone()
    for col in ("item_id", "source_url", "location", "collected_at", "valid_from", "status_ko", "conflict_count", "correction_count", "rights"):
        assert r[col] is not None, col
    assert r["status_ko"] == "출처 주장"
    assert kg.execute("SELECT aliases FROM v_kg_item WHERE item_type='theme' LIMIT 1").fetchone()[0]


def test_registry_rights_and_instagram_scope(kg):
    rows = {r["registry_key"]: r for r in kg.execute("SELECT * FROM source_registry")}
    assert all(r["rights_note"] for r in rows.values())
    assert rows["instagram_public_pages"]["support_level"] == "unsupported"
    assert "프록시" in rows["instagram_public_pages"]["unsupported_scope"]
    assert rows["manual_clip_extension"]["support_level"] == "limited"
    assert rows["nlk_lod_sparql"]["support_level"] == "unsupported"  # robots.txt Disallow: / (2026-10-28)


def test_quality_checks_pass_on_clean_graph(kg, tmp_path):
    run_stage(ThemeStage(), kg)
    run_stage(QualityStage(tmp_path / "exp", tmp_path / "none.db"), kg)
    d = json.loads(kg.execute("SELECT detail_json FROM job_run WHERE stage='quality' ORDER BY started_at DESC LIMIT 1").fetchone()[0])
    for k in ("work_theme_claim_without_evidence", "ai_theme_promoted", "opposes_missing_side"):
        assert k not in d["failed_errors"], k
