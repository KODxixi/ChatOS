CREATE TABLE background_jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN (
        'import',
        'retrieval',
        'deterministic_graph',
        'semantic_graph',
        'media',
        'gchat_insights',
        'backup'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'queued',
        'running',
        'retrying',
        'completed',
        'degraded',
        'failed',
        'cancelled',
        'interrupted'
    )),
    stage TEXT NOT NULL,
    progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
    progress_total INTEGER CHECK(progress_total IS NULL OR progress_total >= 0),
    trigger_source TEXT NOT NULL,
    error_code TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    cancel_requested_at TEXT
);

CREATE INDEX idx_background_jobs_status_updated
ON background_jobs(status, updated_at DESC, id DESC);

CREATE UNIQUE INDEX background_jobs_one_active_type_payload
ON background_jobs(type, payload_json)
WHERE status IN ('queued', 'running', 'retrying');

CREATE TABLE wechat_source_key_refs (
    key_ref TEXT PRIMARY KEY,
    database_id TEXT NOT NULL UNIQUE,
    salt_hash TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    credential_target TEXT NOT NULL UNIQUE
);

CREATE TABLE project_assignment_candidates (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    suggested_project_id TEXT,
    suggested_project_name TEXT,
    source_turn_id TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    model_profile_id TEXT,
    source_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK(status IN ('suggested', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    CHECK(suggested_project_id IS NOT NULL OR suggested_project_name IS NOT NULL)
);

CREATE INDEX idx_project_assignment_candidates_review
ON project_assignment_candidates(status, created_at DESC, id DESC);

CREATE TABLE project_assignment_candidate_evidence (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL
        REFERENCES project_assignment_candidates(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    part_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, message_id, part_id)
);

CREATE INDEX idx_project_assignment_candidate_evidence_candidate
ON project_assignment_candidate_evidence(candidate_id, created_at ASC, id ASC);

ALTER TABLE agent_turns
ADD COLUMN model_options_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE agent_turns
ADD COLUMN grounding_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK(grounding_status IN (
        'unknown', 'grounded', 'insufficient', 'conflicted', 'invalid'
    ));

ALTER TABLE agent_turns
ADD COLUMN retrieval_summary_json TEXT NOT NULL DEFAULT '{}';
