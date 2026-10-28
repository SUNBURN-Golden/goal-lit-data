-- 데몬 단계 운영 테이블: 체크포인트 · 실행 이력 · 실패 큐 · 일일 상한
CREATE TABLE stage_state (
    stage TEXT PRIMARY KEY,
    cursor TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT 'never_run',
    last_run_id TEXT,
    last_started_at TEXT,
    last_finished_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT,
    paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE job_run (
    run_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed','skipped_locked','skipped_budget','skipped_paused')),
    processed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    cursor_start TEXT,
    cursor_end TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_jobrun_stage ON job_run(stage, started_at);

CREATE TABLE failed_queue (
    fq_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    ref_kind TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    first_failed_at TEXT NOT NULL,
    last_failed_at TEXT NOT NULL,
    next_retry_at TEXT,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','dead','resolved')),
    UNIQUE (stage, ref_kind, ref_id)
);
CREATE INDEX idx_failed_state ON failed_queue(stage, state, next_retry_at);

CREATE TABLE daily_usage (
    day TEXT NOT NULL,
    stage TEXT NOT NULL,
    metric TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    cap INTEGER NOT NULL,
    PRIMARY KEY (day, stage, metric)
);
