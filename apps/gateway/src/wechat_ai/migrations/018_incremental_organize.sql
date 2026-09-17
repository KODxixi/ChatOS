-- ChatOS v0.4.0: incremental organize infrastructure.
-- 1) Extend background_jobs type CHECK with 'incremental_organize' + add result_json.
-- 2) conversation_watermarks: per-conversation organize high-water mark.
-- 3) tasks.source_message_id: deterministic idempotency key (conversation_id + source_message_id).

-- ---------------------------------------------------------------------------
-- 1. background_jobs: add incremental_organize + result_json (table recreate)
-- ---------------------------------------------------------------------------
CREATE TABLE background_jobs_new (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN (
        'import',
        'retrieval',
        'deterministic_graph',
        'semantic_graph',
        'media',
        'gchat_insights',
        'backup',
        'incremental_organize'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'queued', 'running', 'retrying', 'completed',
        'degraded', 'failed', 'cancelled', 'interrupted'
    )),
    stage TEXT NOT NULL,
    progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
    progress_total INTEGER CHECK(progress_total IS NULL OR progress_total >= 0),
    trigger_source TEXT NOT NULL,
    error_code TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    cancel_requested_at TEXT
);

INSERT INTO background_jobs_new (
    id, type, status, stage, progress_current, progress_total,
    trigger_source, error_code, retry_count, payload_json,
    result_json, created_at, started_at, finished_at, updated_at,
    cancel_requested_at
)
SELECT
    id, type, status, stage, progress_current, progress_total,
    trigger_source, error_code, retry_count, payload_json,
    '{}', created_at, started_at, finished_at, updated_at,
    cancel_requested_at
FROM background_jobs;

DROP TABLE background_jobs;
ALTER TABLE background_jobs_new RENAME TO background_jobs;

CREATE INDEX idx_background_jobs_status_updated
ON background_jobs(status, updated_at DESC, id DESC);

CREATE UNIQUE INDEX background_jobs_one_active_type_payload
ON background_jobs(type, payload_json)
WHERE status IN ('queued', 'running', 'retrying');

-- ---------------------------------------------------------------------------
-- 2. conversation_watermarks
-- ---------------------------------------------------------------------------
CREATE TABLE conversation_watermarks (
    conversation_id      TEXT PRIMARY KEY,
    last_message_sent_at INTEGER NOT NULL DEFAULT 0,
    last_message_id      INTEGER NOT NULL DEFAULT 0,
    updated_at           TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 3. tasks.source_message_id for deterministic idempotency
-- ---------------------------------------------------------------------------
ALTER TABLE tasks ADD COLUMN source_message_id INTEGER;

CREATE INDEX idx_tasks_source_message
ON tasks(source_message_id);

CREATE UNIQUE INDEX idx_tasks_conv_source
ON tasks(conversation_id, source_message_id)
WHERE source_message_id IS NOT NULL;
