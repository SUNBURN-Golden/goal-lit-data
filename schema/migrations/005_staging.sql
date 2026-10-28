-- 추출 → 동일 대상 식별 → 근거 검증 → 상태 갱신 단계 사이의 중간 산출물
-- mention: 추출 단계가 찾은 '언급'(작가 발언, 판본 정보). 동일 대상 식별 단계가 개체와 주장 행으로 확정한다.
CREATE TABLE mention (
    mention_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('author_statement','edition')),
    work_id TEXT NOT NULL REFERENCES work(work_id),
    inbox_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source(source_id),
    evidence_id TEXT REFERENCES evidence_passage(evidence_id),
    is_external INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','resolved','needs_review')),
    resolved_ref TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_mention_state ON mention(state, is_external DESC, created_at);

-- verification_result: 근거 검증 단계의 판정. 상태·이력 단계가 이 판정을 적용한다(판정과 변경을 분리).
CREATE TABLE verification_result (
    claim_table TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('confirm','keep_claim','keep_unverified','violation')),
    reasons_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (claim_table, claim_id)
);
