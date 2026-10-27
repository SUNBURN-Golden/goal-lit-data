#!/usr/bin/env python3
"""schema/migrations/*.sql 생성기. 공통 근거 필드(PROV)를 모든 주장 테이블에 같은 모양으로 넣기 위해 사용한다.
실행: python tools/gen_schema.py  → schema/migrations/001_ontology.sql, 002_guards.sql 재생성."""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "schema" / "migrations"

STATUSES = "'confirmed_fact','source_claim','ai_inference','unverified'"
USAGE = "'metadata_only','link_only','quote_le12','fulltext_forbidden','own_work'"

def prov(allow=STATUSES, require_evidence_for_confirmed=True):
    chk = f"CHECK (epistemic_status IN ({allow}))"
    conf = ""
    if "confirmed_fact" in allow and require_evidence_for_confirmed:
        conf = ",\n    CHECK (epistemic_status <> 'confirmed_fact' OR (source_id IS NOT NULL AND evidence_id IS NOT NULL))"
    return f"""    source_id TEXT REFERENCES source(source_id),
    source_url TEXT,
    evidence_id TEXT REFERENCES evidence_passage(evidence_id),
    collected_at TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    valid_from_precision TEXT NOT NULL DEFAULT 'unknown' CHECK (valid_from_precision IN ('year','month','day','unknown')),
    recorded_at TEXT NOT NULL,
    recorded_to TEXT,
    superseded_by TEXT,
    epistemic_status TEXT NOT NULL DEFAULT 'unverified',
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ({USAGE})),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    {chk[0:]}{conf}"""

TABLES = []  # (name, immutable_cols, status_check_allow)

def t(name, body, immutable, allow=STATUSES, extra=""):
    TABLES.append((name, immutable, allow))
    return f"CREATE TABLE {name} (\n{body},\n{prov(allow)}{extra}\n);\nCREATE INDEX idx_{name}_status ON {name}(epistemic_status);\n"

PROV_IMMUTABLE = ["source_id", "source_url", "evidence_id", "collected_at", "valid_from", "valid_from_precision", "recorded_at", "usage_basis", "legacy_inbox_id"]

sql = ["-- 자동 생성: tools/gen_schema.py (직접 수정하지 말고 생성기를 고칠 것)\n"]
sql.append("""CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);

CREATE TABLE source_registry (
    registry_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    support_level TEXT NOT NULL CHECK (support_level IN ('supported','limited','unsupported')),
    domains_json TEXT NOT NULL DEFAULT '[]',
    fields_json TEXT NOT NULL DEFAULT '[]',
    usage_basis TEXT NOT NULL CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    refresh_hours INTEGER,
    retention_days INTEGER,
    unsupported_scope TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    loaded_at TEXT NOT NULL
);

CREATE TABLE source (
    source_id TEXT PRIMARY KEY,
    registry_key TEXT REFERENCES source_registry(registry_key),
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    outlet TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'article' CHECK (kind IN ('article','paper','catalog','official_entry','internal_record','clip')),
    access_level TEXT NOT NULL CHECK (access_level IN ('book_intro','paper_abstract','full_text_read','press_article','author_interview')),
    is_external INTEGER NOT NULL DEFAULT 1,
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    published_at TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    legacy_inbox_id TEXT
);
CREATE INDEX idx_source_domain ON source(domain);

CREATE TABLE source_snapshot (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES source(source_id),
    content_hash TEXT NOT NULL,
    http_status INTEGER,
    checked_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_snapshot_source ON source_snapshot(source_id, snapshot_id);

CREATE TABLE change_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES source(source_id),
    kind TEXT NOT NULL CHECK (kind IN ('content_changed','became_unreachable','recovered')),
    old_hash TEXT,
    new_hash TEXT,
    detected_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','handled')),
    impacted_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE work (
    work_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    canonical_title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE author (
    author_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE theme (
    theme_id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE entity_alias (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('work','author','theme','edition')),
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_kind TEXT NOT NULL DEFAULT 'variant' CHECK (alias_kind IN ('variant','translation','romanization','pen_name','legacy_slug')),
    source_id TEXT REFERENCES source(source_id),
    epistemic_status TEXT NOT NULL DEFAULT 'source_claim' CHECK (epistemic_status IN ('confirmed_fact','source_claim','unverified')),
    collected_at TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, alias)
);

CREATE TABLE evidence_passage (
    evidence_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source(source_id),
    locator TEXT NOT NULL,
    locator_type TEXT NOT NULL CHECK (locator_type IN ('paragraph','page','section','dom_path','api_field','legacy_pointer','timestamp')),
    locator_quality TEXT NOT NULL DEFAULT 'precise' CHECK (locator_quality IN ('precise','legacy_pointer')),
    quote_snippet TEXT NOT NULL DEFAULT '' CHECK (length(quote_snippet) <= 12),
    passage_hash TEXT,
    collected_at TEXT NOT NULL,
    legacy_inbox_id TEXT,
    UNIQUE (source_id, locator)
);
CREATE INDEX idx_evidence_source ON evidence_passage(source_id);

""")

sql.append(t("edition", """    edition_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    isbn TEXT,
    label TEXT NOT NULL DEFAULT '',
    identity_status TEXT NOT NULL DEFAULT 'provisional' CHECK (identity_status IN ('isbn_matched','provisional','confirmed'))""",
    ["work_id", "isbn", "label"] + PROV_IMMUTABLE))
# 판본의 속성(출판사·연도·쪽수·번역자·언어)은 bib_fact(subject_type='edition') 행으로, 출처마다 따로 쌓는다.

sql.append(t("bib_fact", """    fact_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('work','edition')),
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_text TEXT NOT NULL DEFAULT '',
    legacy_tier INTEGER""",
    ["subject_type", "subject_id", "predicate", "value_text"] + PROV_IMMUTABLE))

sql.append(t("author_statement", """    statement_id TEXT PRIMARY KEY,
    author_id TEXT REFERENCES author(author_id),
    author_name_raw TEXT NOT NULL DEFAULT '',
    work_id TEXT REFERENCES work(work_id),
    summary TEXT NOT NULL DEFAULT '',
    summary_withheld_reason TEXT NOT NULL DEFAULT '',
    statement_date TEXT,
    statement_medium TEXT NOT NULL DEFAULT '',
    source_tier TEXT NOT NULL DEFAULT 'unknown' CHECK (source_tier IN ('primary','secondary','unknown'))""",
    ["author_id", "author_name_raw", "work_id", "summary", "statement_date", "statement_medium", "source_tier"] + PROV_IMMUTABLE,
    extra=",\n    CHECK (author_id IS NOT NULL OR epistemic_status = 'unverified')"))

NOCONF = "'source_claim','ai_inference','unverified'"
sql.append(t("interpretation", """    interp_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    theme_id TEXT REFERENCES theme(theme_id),
    kind TEXT NOT NULL CHECK (kind IN ('critique','interpretation','counter','scholarly_ref')),
    attributed_to_name TEXT NOT NULL DEFAULT '',
    attributed_author_id TEXT REFERENCES author(author_id),
    attribution_verified INTEGER NOT NULL DEFAULT 0,
    stance TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summary_withheld_reason TEXT NOT NULL DEFAULT ''""",
    ["work_id", "theme_id", "kind", "attributed_to_name", "stance", "summary"] + PROV_IMMUTABLE, NOCONF))

sql.append(t("work_author", """    wa_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    author_id TEXT NOT NULL REFERENCES author(author_id),
    role TEXT NOT NULL DEFAULT 'author'""",
    ["work_id", "author_id", "role"] + PROV_IMMUTABLE))

sql.append(t("work_theme", """    wt_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    theme_id TEXT NOT NULL REFERENCES theme(theme_id)""",
    ["work_id", "theme_id"] + PROV_IMMUTABLE, NOCONF))

sql.append(t("interpretation_relation", """    rel_id TEXT PRIMARY KEY,
    a_interp_id TEXT NOT NULL REFERENCES interpretation(interp_id),
    b_interp_id TEXT NOT NULL REFERENCES interpretation(interp_id),
    relation TEXT NOT NULL CHECK (relation IN ('opposes','supports','responds_to'))""",
    ["a_interp_id", "b_interp_id", "relation"] + PROV_IMMUTABLE, "'source_claim','unverified'",
    extra=",\n    CHECK (a_interp_id <> b_interp_id)"))


sql.append("""CREATE TABLE ai_inference (
    inference_id TEXT PRIMARY KEY,
    subject_table TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    proposed_relation TEXT NOT NULL,
    rationale_summary TEXT NOT NULL DEFAULT '',
    model_tag TEXT NOT NULL DEFAULT '',
    epistemic_status TEXT NOT NULL DEFAULT 'ai_inference' CHECK (epistemic_status = 'ai_inference'),
    collected_at TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending','rejected','promoted')),
    promoted_to_table TEXT,
    promoted_to_id TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    CHECK (review_state <> 'promoted' OR (promoted_to_table IS NOT NULL AND promoted_to_id IS NOT NULL AND reviewed_by IS NOT NULL))
);

CREATE TABLE conflict (
    conflict_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('bib_value','edition_attr','statement_source','duplicate_entity','other')),
    a_table TEXT NOT NULL, a_id TEXT NOT NULL,
    b_table TEXT NOT NULL, b_id TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    detected_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
    resolved_at TEXT,
    resolution_note TEXT NOT NULL DEFAULT '',
    resolved_by TEXT
);
CREATE INDEX idx_conflict_open ON conflict(status);

CREATE TABLE review_queue (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref_table TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','in_progress','done','dismissed')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT,
    UNIQUE (kind, ref_table, ref_id, reason)
);
CREATE INDEX idx_review_state ON review_queue(state, priority DESC);

CREATE TABLE change_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    row_id TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('insert','supersede','close','status_change','correct','source_changed')),
    old_json TEXT,
    new_json TEXT,
    reason TEXT NOT NULL DEFAULT '',
    run_id TEXT,
    at TEXT NOT NULL
);
CREATE INDEX idx_changelog_row ON change_log(table_name, row_id);

CREATE TABLE legacy_map (
    inbox_id TEXT NOT NULL,
    kg_table TEXT NOT NULL,
    kg_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    mapped_at TEXT NOT NULL,
    PRIMARY KEY (inbox_id, kg_table, kg_id, content_hash)
);
""")
(OUT / "001_ontology.sql").write_text("\n".join(sql), encoding="utf-8")

# ---- 002 guards (불변 조건 트리거) ----
g = ["-- 자동 생성: tools/gen_schema.py\n-- 불변 조건: 주장 행 값 UPDATE 금지, DELETE 금지, change_log append-only, confirmed_fact 승격 조건 재확인\n"]
for name, imm, allow in TABLES:
    pk = {"edition": "edition_id", "bib_fact": "fact_id", "author_statement": "statement_id", "interpretation": "interp_id",
          "work_author": "wa_id", "work_theme": "wt_id", "interpretation_relation": "rel_id"}[name]
    cond = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in imm)
    g.append(f"""CREATE TRIGGER trg_{name}_immutable BEFORE UPDATE ON {name}
WHEN {cond}
BEGIN SELECT RAISE(ABORT, '{name}: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_{name}_nodelete BEFORE DELETE ON {name}
BEGIN SELECT RAISE(ABORT, '{name}: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;
""")
    if "confirmed_fact" in allow:
        g.append(f"""CREATE TRIGGER trg_{name}_confirm_ins BEFORE INSERT ON {name}
WHEN NEW.epistemic_status = 'confirmed_fact' AND NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1)
BEGIN SELECT RAISE(ABORT, '{name}: confirmed_fact는 외부 출처가 필요하다'); END;
CREATE TRIGGER trg_{name}_confirm_upd BEFORE UPDATE OF epistemic_status ON {name}
WHEN NEW.epistemic_status = 'confirmed_fact' AND (NEW.evidence_id IS NULL OR NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1))
BEGIN SELECT RAISE(ABORT, '{name}: confirmed_fact는 외부 출처+근거 위치가 필요하다'); END;
""")
    g.append(f"""CREATE TRIGGER trg_{name}_status_log AFTER UPDATE OF epistemic_status ON {name}
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('{name}', NEW.{pk}, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;
""")
g.append("""CREATE TRIGGER trg_evidence_immutable BEFORE UPDATE ON evidence_passage
BEGIN SELECT RAISE(ABORT, 'evidence_passage: 수정 금지 — 새 근거 행을 추가할 것'); END;
CREATE TRIGGER trg_evidence_nodelete BEFORE DELETE ON evidence_passage
BEGIN SELECT RAISE(ABORT, 'evidence_passage: 삭제 금지'); END;
CREATE TRIGGER trg_changelog_noupdate BEFORE UPDATE ON change_log
BEGIN SELECT RAISE(ABORT, 'change_log는 append-only'); END;
CREATE TRIGGER trg_changelog_nodelete BEFORE DELETE ON change_log
BEGIN SELECT RAISE(ABORT, 'change_log는 append-only'); END;
CREATE TRIGGER trg_snapshot_noupdate BEFORE UPDATE ON source_snapshot
BEGIN SELECT RAISE(ABORT, 'source_snapshot은 append-only'); END;
CREATE TRIGGER trg_snapshot_nodelete BEFORE DELETE ON source_snapshot
BEGIN SELECT RAISE(ABORT, 'source_snapshot은 append-only'); END;
CREATE TRIGGER trg_conflict_nodelete BEFORE DELETE ON conflict
BEGIN SELECT RAISE(ABORT, 'conflict 삭제 금지 (status로 닫을 것)'); END;
CREATE TRIGGER trg_aiinf_nodelete BEFORE DELETE ON ai_inference
BEGIN SELECT RAISE(ABORT, 'ai_inference 삭제 금지'); END;
""")
(OUT / "002_guards.sql").write_text("\n".join(g), encoding="utf-8")
print("ok")
