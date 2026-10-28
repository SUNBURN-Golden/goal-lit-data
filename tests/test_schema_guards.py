"""스키마·불변 조건 테스트 (비식별 픽스처, 임시 DB)."""
import sqlite3
import pytest

from glkg import db
from glkg.timeutil import now_iso


@pytest.fixture()
def conn(tmp_path):
    c = db.open_kg(tmp_path / "kg.db")
    yield c
    c.close()


def _seed(c):
    t = now_iso()
    c.execute("INSERT INTO source(source_id,url,canonical_url,domain,access_level,is_external,first_seen_at,last_seen_at) VALUES('src_ext','https://ex.test/a','https://ex.test/a','ex.test','press_article',1,?,?)", (t, t))
    c.execute("INSERT INTO source(source_id,url,canonical_url,domain,access_level,is_external,first_seen_at,last_seen_at) VALUES('src_int','https://own.test/a','https://own.test/a','own.test','book_intro',0,?,?)", (t, t))
    c.execute("INSERT INTO evidence_passage(evidence_id,source_id,locator,locator_type,collected_at) VALUES('ev1','src_ext','p.3','page',?)", (t,))
    c.execute("INSERT INTO evidence_passage(evidence_id,source_id,locator,locator_type,collected_at) VALUES('ev2','src_int','s1','section',?)", (t,))
    c.execute("INSERT INTO work(work_id,slug,canonical_title,created_at) VALUES('wk1','w1','작품',?)", (t,))
    c.commit()


def _fact(c, fid, status="source_claim", src="src_ext", ev="ev1", value="v"):
    t = now_iso()
    c.execute("INSERT INTO bib_fact(fact_id,subject_type,subject_id,predicate,value_text,source_id,evidence_id,collected_at,recorded_at,epistemic_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (fid, "work", "wk1", "출간", value, src, ev, t, t, status))


def test_migrations_apply_once(tmp_path):
    c = db.connect(tmp_path / "x.db")
    first = db.migrate(c)
    assert first == [1, 2, 3, 4]
    assert db.migrate(c) == []


def test_value_cannot_be_overwritten_or_deleted(conn):
    _seed(conn)
    _fact(conn, "f1")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE bib_fact SET value_text='바뀜' WHERE fact_id='f1'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM bib_fact WHERE fact_id='f1'")
    # 닫기(valid_to / recorded_to / superseded_by)는 허용
    conn.execute("UPDATE bib_fact SET valid_to='2026-01-01', recorded_to='2026-01-02', superseded_by='f2' WHERE fact_id='f1'")


def test_confirmed_requires_external_source_and_evidence(conn):
    _seed(conn)
    with pytest.raises(sqlite3.DatabaseError):
        _fact(conn, "f_noev", status="confirmed_fact", ev=None)
    with pytest.raises(sqlite3.DatabaseError):
        _fact(conn, "f_int", status="confirmed_fact", src="src_int", ev="ev2")
    _fact(conn, "f_ok", status="confirmed_fact")
    _fact(conn, "f_claim")
    with pytest.raises(sqlite3.DatabaseError):  # 승격도 같은 조건
        conn.execute("UPDATE bib_fact SET epistemic_status='confirmed_fact', evidence_id=NULL WHERE fact_id='f_claim'")


def test_status_change_is_logged(conn):
    _seed(conn)
    _fact(conn, "f1")
    conn.execute("UPDATE bib_fact SET epistemic_status='confirmed_fact' WHERE fact_id='f1'")
    row = conn.execute("SELECT * FROM change_log WHERE row_id='f1'").fetchone()
    assert row["op"] == "status_change"


def test_interpretation_can_never_be_confirmed_fact(conn):
    _seed(conn)
    t = now_iso()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO interpretation(interp_id,work_id,kind,source_id,evidence_id,collected_at,recorded_at,epistemic_status) VALUES('i1','wk1','critique','src_ext','ev1',?,?,'confirmed_fact')", (t, t))
    conn.execute("INSERT INTO interpretation(interp_id,work_id,kind,source_id,evidence_id,collected_at,recorded_at,epistemic_status) VALUES('i1','wk1','critique','src_ext','ev1',?,?,'source_claim')", (t, t))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE interpretation SET epistemic_status='confirmed_fact' WHERE interp_id='i1'")


def test_ai_inference_is_separate_and_not_auto_promotable(conn):
    _seed(conn)
    t = now_iso()
    conn.execute("INSERT INTO ai_inference(inference_id,subject_table,subject_id,proposed_relation,collected_at) VALUES('a1','interpretation','i1','opposes:i2',?)", (t,))
    with pytest.raises(sqlite3.DatabaseError):  # 승격엔 사람 검수자와 대상 행이 필요
        conn.execute("UPDATE ai_inference SET review_state='promoted' WHERE inference_id='a1'")
    # interpretation_relation 에는 ai_inference 상태를 넣을 수 없다
    conn.execute("INSERT INTO interpretation(interp_id,work_id,kind,source_id,evidence_id,collected_at,recorded_at,epistemic_status) VALUES('i1','wk1','critique','src_ext','ev1',?,?,'source_claim')", (t, t))
    conn.execute("INSERT INTO interpretation(interp_id,work_id,kind,source_id,evidence_id,collected_at,recorded_at,epistemic_status) VALUES('i2','wk1','counter','src_ext','ev1',?,?,'source_claim')", (t, t))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO interpretation_relation(rel_id,a_interp_id,b_interp_id,relation,collected_at,recorded_at,epistemic_status) VALUES('r1','i1','i2','opposes',?,?,'ai_inference')", (t, t))


def test_quote_snippet_max_12_chars(conn):
    _seed(conn)
    t = now_iso()
    conn.execute("INSERT INTO evidence_passage(evidence_id,source_id,locator,locator_type,quote_snippet,collected_at) VALUES('e12','src_ext','p.9','page',?,?)", ("가" * 12, t))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO evidence_passage(evidence_id,source_id,locator,locator_type,quote_snippet,collected_at) VALUES('e13','src_ext','p.10','page',?,?)", ("가" * 13, t))


def test_changelog_and_snapshot_append_only(conn):
    _seed(conn)
    db.log_change(conn, "bib_fact", "f1", "insert", new={"a": 1})
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE change_log SET reason='x'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM change_log")
    conn.execute("INSERT INTO source_snapshot(source_id,content_hash,checked_at) VALUES('src_ext','h',?)", (now_iso(),))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM source_snapshot")


def test_unknown_validity_is_null_not_forever(conn):
    _seed(conn)
    _fact(conn, "f1")
    r = conn.execute("SELECT valid_from, valid_to, valid_from_precision FROM bib_fact").fetchone()
    assert r["valid_from"] is None and r["valid_to"] is None and r["valid_from_precision"] == "unknown"
