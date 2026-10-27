-- 능동 수집 봇: 4개 저장 계층 + 탐색·평가·일정 + 빈칸 관리
-- 계층: raw(원본 사본·해시, 리포 밖 비공개) → extraction(추출 필드) → label(라벨) → service(기존 주장 표 + out_*)

-- [raw 계층] 가져온 문서의 사본 정보. 본문은 data 디렉터리(리포 밖)에만, 권리상 불가면 해시만.
CREATE TABLE raw_document (
    doc_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    domain TEXT NOT NULL,
    fetch_method TEXT NOT NULL CHECK (fetch_method IN ('api','feed','html','browser','pdf','image')),
    http_status INTEGER,
    content_type TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    byte_len INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    body_stored INTEGER NOT NULL DEFAULT 0,
    storage_path TEXT,
    retention_until TEXT,
    origin TEXT NOT NULL DEFAULT '',
    budget_class TEXT NOT NULL DEFAULT 'discovery',
    parsed_version TEXT,
    parsed_at TEXT,
    UNIQUE (canonical_url, content_hash)
);
CREATE INDEX idx_raw_canon ON raw_document(canonical_url, fetched_at);
CREATE INDEX idx_raw_unparsed ON raw_document(parsed_version);

-- 모든 시도 기록(성공·실패). 로그인 페이지·빈 본문·차단은 실패로 남는다(우회하지 않음).
CREATE TABLE fetch_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    at TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    http_status INTEGER,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok','unchanged','failed_login','failed_empty','failed_http','failed_robots','failed_policy','failed_rate_limit','failed_network','failed_inaccessible','failed_unsupported_type')),
    detail TEXT NOT NULL DEFAULT '',
    budget_class TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_fetchlog_url ON fetch_log(url, at);

-- [extraction 계층] 추출 필드. 같은 문서를 추출기 버전별로 다시 추출해도 이전 결과는 남는다.
CREATE TABLE extraction (
    extraction_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES raw_document(doc_id),
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    locator TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'structured' CHECK (method IN ('structured','pattern','ocr')),
    extracted_at TEXT NOT NULL
);
CREATE INDEX idx_extraction_doc ON extraction(doc_id);
CREATE INDEX idx_extraction_field ON extraction(field, value);

-- [label 계층] 문서 라벨. 홍보물과 독립 비평을 분리해 둔다.
CREATE TABLE doc_label (
    doc_id TEXT NOT NULL REFERENCES raw_document(doc_id),
    label TEXT NOT NULL CHECK (label IN ('서지','인터뷰','서평','비평','해석','참고문헌')),
    origin_class TEXT NOT NULL DEFAULT 'unknown' CHECK (origin_class IN ('promo','independent','unknown')),
    basis TEXT NOT NULL DEFAULT '',
    work_id TEXT,
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, label)
);

-- [탐색] 질의·후보 출처·후보 작품·수집 대기열
CREATE TABLE discovery_query (
    query_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    api TEXT NOT NULL,
    query_text TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    budget_class TEXT NOT NULL CHECK (budget_class IN ('backfill','discovery','refresh')),
    priority INTEGER NOT NULL DEFAULT 50,
    origin TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','done','failed','skipped')),
    result_count INTEGER,
    created_at TEXT NOT NULL,
    last_run_at TEXT
);
CREATE INDEX idx_dq_state ON discovery_query(state, budget_class, priority DESC);

CREATE TABLE crawl_frontier (
    url TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    depth INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 50,
    budget_class TEXT NOT NULL CHECK (budget_class IN ('backfill','discovery','refresh')),
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','done','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try_at TEXT,
    last_outcome TEXT NOT NULL DEFAULT '',
    work_hint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_frontier_q ON crawl_frontier(state, budget_class, priority DESC);

CREATE TABLE candidate_source (
    domain TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new','trial_done','registered','deprioritized','blocked')),
    in_seed INTEGER NOT NULL DEFAULT 0,
    first_seen_via TEXT NOT NULL DEFAULT '',
    sample_url TEXT NOT NULL DEFAULT '',
    score_relevance REAL, score_novelty REAL, score_extractability REAL, score_original REAL, score_rights REAL, score_total REAL,
    usage_basis TEXT NOT NULL DEFAULT 'link_only' CHECK (usage_basis IN ('metadata_only','link_only','quote_le12','fulltext_forbidden','own_work')),
    decision_reason TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    evaluated_at TEXT
);

CREATE TABLE work_candidate (
    cand_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author_name TEXT NOT NULL DEFAULT '',
    first_year TEXT NOT NULL DEFAULT '',
    isbn TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT '',
    doc_id TEXT,
    known_work_id TEXT,
    state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new','promoted','duplicate')),
    created_at TEXT NOT NULL
);

-- [빈칸] 필수 필드별 빈칸 상태. 상태는 아래 4가지 + open/resolved. AI로 채우지 않는다.
CREATE TABLE gap (
    gap_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work(work_id),
    field TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open','not_extracted','not_in_source','inaccessible','conflict','resolved')),
    detail TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);
CREATE INDEX idx_gap_state ON gap(state, field);

CREATE TABLE gap_history (
    hist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);

CREATE TABLE out_coverage (
    work_id TEXT NOT NULL,
    field TEXT NOT NULL,
    filled INTEGER NOT NULL,
    gap_state TEXT,
    PRIMARY KEY (work_id, field)
);

-- 작품 속성은 bib_fact(subject_type='work')에 source_claim으로 쌓인다. 봇이 쓰는 필드: author / first_pub_year / publisher / isbn / original_title / translator
