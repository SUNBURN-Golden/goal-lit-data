-- 자동 생성: tools/gen_schema.py (직접 수정하지 말고 생성기를 고칠 것)

CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);

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


CREATE TABLE edition (
    edition_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    isbn TEXT,
    publisher TEXT,
    translator TEXT,
    language TEXT,
    pub_year TEXT,
    pages INTEGER,
    label TEXT NOT NULL DEFAULT '',
    identity_status TEXT NOT NULL DEFAULT 'provisional' CHECK (identity_status IN ('isbn_matched','provisional','confirmed')),
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('confirmed_fact','source_claim','ai_inference','unverified')),
    CHECK (epistemic_status <> 'confirmed_fact' OR (source_id IS NOT NULL AND evidence_id IS NOT NULL))
);
CREATE INDEX idx_edition_status ON edition(epistemic_status);

CREATE TABLE bib_fact (
    fact_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('work','edition')),
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_text TEXT NOT NULL DEFAULT '',
    legacy_tier INTEGER,
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('confirmed_fact','source_claim','ai_inference','unverified')),
    CHECK (epistemic_status <> 'confirmed_fact' OR (source_id IS NOT NULL AND evidence_id IS NOT NULL))
);
CREATE INDEX idx_bib_fact_status ON bib_fact(epistemic_status);

CREATE TABLE author_statement (
    statement_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL REFERENCES author(author_id),
    work_id TEXT REFERENCES work(work_id),
    summary TEXT NOT NULL DEFAULT '',
    summary_withheld_reason TEXT NOT NULL DEFAULT '',
    statement_date TEXT,
    statement_medium TEXT NOT NULL DEFAULT '',
    source_tier TEXT NOT NULL DEFAULT 'unknown' CHECK (source_tier IN ('primary','secondary','unknown')),
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('confirmed_fact','source_claim','ai_inference','unverified')),
    CHECK (epistemic_status <> 'confirmed_fact' OR (source_id IS NOT NULL AND evidence_id IS NOT NULL))
);
CREATE INDEX idx_author_statement_status ON author_statement(epistemic_status);

CREATE TABLE interpretation (
    interp_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    theme_id TEXT REFERENCES theme(theme_id),
    kind TEXT NOT NULL CHECK (kind IN ('critique','interpretation','counter','scholarly_ref')),
    attributed_to_name TEXT NOT NULL DEFAULT '',
    attributed_author_id TEXT REFERENCES author(author_id),
    attribution_verified INTEGER NOT NULL DEFAULT 0,
    stance TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summary_withheld_reason TEXT NOT NULL DEFAULT '',
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('source_claim','ai_inference','unverified'))
);
CREATE INDEX idx_interpretation_status ON interpretation(epistemic_status);

CREATE TABLE work_author (
    wa_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    author_id TEXT NOT NULL REFERENCES author(author_id),
    role TEXT NOT NULL DEFAULT 'author',
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('confirmed_fact','source_claim','ai_inference','unverified')),
    CHECK (epistemic_status <> 'confirmed_fact' OR (source_id IS NOT NULL AND evidence_id IS NOT NULL))
);
CREATE INDEX idx_work_author_status ON work_author(epistemic_status);

CREATE TABLE work_theme (
    wt_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    theme_id TEXT NOT NULL REFERENCES theme(theme_id),
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('source_claim','ai_inference','unverified'))
);
CREATE INDEX idx_work_theme_status ON work_theme(epistemic_status);

CREATE TABLE interpretation_relation (
    rel_id TEXT PRIMARY KEY,
    a_interp_id TEXT NOT NULL REFERENCES interpretation(interp_id),
    b_interp_id TEXT NOT NULL REFERENCES interpretation(interp_id),
    relation TEXT NOT NULL CHECK (relation IN ('opposes','supports','responds_to')),
    source_id TEXT REFERENCES source(source_id),
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
    usage_basis TEXT NOT NULL DEFAULT 'metadata_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    retention_until TEXT,
    legacy_inbox_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (epistemic_status IN ('source_claim','unverified')),
    CHECK (a_interp_id <> b_interp_id)
);
CREATE INDEX idx_interpretation_relation_status ON interpretation_relation(epistemic_status);

CREATE TABLE ai_inference (
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
    PRIMARY KEY (inbox_id, kg_table, kg_id)
);
