-- 서비스용 결과(재계산 대상). 전부 파생 테이블이며 원본은 주장 테이블이다. 언제든 지우고 다시 계산 가능.
CREATE TABLE out_meta (
    output_name TEXT PRIMARY KEY,
    computed_at TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0
);

-- Q1 판본 비교: 작품·속성별로 판본마다 값과 확인 수준, 값이 갈리면 conflicting=1
CREATE TABLE out_work_edition_diff (
    work_id TEXT NOT NULL,
    attribute TEXT NOT NULL,           -- publisher / translator / language / pub_year / pages / isbn
    edition_id TEXT NOT NULL,
    value TEXT,
    epistemic_status TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    source_url TEXT,
    distinct_values INTEGER NOT NULL,
    conflicting INTEGER NOT NULL,
    comparable INTEGER NOT NULL        -- 비교 가능한 판본이 2개 이상일 때만 1
);

-- Q2 주제별 해석(작성자·근거 위치·확인 수준) + 출처가 명시한 반대 관계
CREATE TABLE out_theme_critique_view (
    work_id TEXT NOT NULL,
    theme_label TEXT,                  -- NULL = 주제 미지정
    interp_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    attributed_to_name TEXT NOT NULL,
    attribution_verified INTEGER NOT NULL,
    epistemic_status TEXT NOT NULL,
    source_url TEXT,
    evidence_locator TEXT,
    collected_at TEXT NOT NULL,
    opposes_interp_id TEXT,            -- 출처 명시/편집자 확정 관계만
    has_opposing INTEGER NOT NULL
);

-- Q3 작가 발언 색인
CREATE TABLE out_author_statement_index (
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    work_id TEXT,
    statement_id TEXT NOT NULL,
    statement_date TEXT,
    statement_medium TEXT NOT NULL,
    source_tier TEXT NOT NULL,
    epistemic_status TEXT NOT NULL,
    source_url TEXT,
    evidence_locator TEXT,
    collected_at TEXT NOT NULL
);

-- Q4 서지 사실 상태: 작품별 사실과 확인 수준 (미확인 항목 포함)
CREATE TABLE out_work_fact_status (
    work_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_text TEXT NOT NULL,
    epistemic_status TEXT NOT NULL,
    source_url TEXT,
    evidence_locator TEXT,
    locator_quality TEXT,
    collected_at TEXT NOT NULL,
    has_open_conflict INTEGER NOT NULL
);

-- Q6 미확인 + AI 제안 (사실 칸과 분리)
CREATE TABLE out_unverified_and_ai (
    work_id TEXT,
    item_kind TEXT NOT NULL,           -- unverified_fact / unverified_statement / unverified_interpretation / ai_inference
    ref_id TEXT NOT NULL,
    text TEXT NOT NULL,
    category TEXT,
    review_state TEXT
);

-- Q7 작가-작품-주제: 두 연결 각각에 출처가 있는 경우만
CREATE TABLE out_author_theme_view (
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    theme_label TEXT NOT NULL,
    work_id TEXT NOT NULL,
    work_author_status TEXT NOT NULL,
    work_theme_status TEXT NOT NULL,
    author_source_url TEXT,
    theme_source_url TEXT
);
