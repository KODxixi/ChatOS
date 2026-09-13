CREATE TABLE agent_call_spans (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    parent_span_id TEXT REFERENCES agent_call_spans(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK(kind IN (
        'retrieval', 'model', 'tool', 'embedding'
    )),
    operation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN (
        'running', 'completed', 'failed', 'cancelled'
    )),
    model_profile_id TEXT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
    input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
    total_tokens INTEGER CHECK(total_tokens IS NULL OR total_tokens >= 0),
    candidate_count INTEGER CHECK(candidate_count IS NULL OR candidate_count >= 0),
    citation_count INTEGER CHECK(citation_count IS NULL OR citation_count >= 0),
    error_code TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    expires_at INTEGER NOT NULL
);

CREATE INDEX idx_agent_call_spans_run_started
ON agent_call_spans(run_id, started_at ASC, id ASC);

CREATE INDEX idx_agent_call_spans_kind_status_started
ON agent_call_spans(kind, status, started_at DESC, id DESC);

CREATE INDEX idx_agent_call_spans_model_started
ON agent_call_spans(model_profile_id, started_at DESC, id DESC)
WHERE model_profile_id IS NOT NULL;

CREATE INDEX idx_agent_call_spans_expires
ON agent_call_spans(expires_at, status);

CREATE TABLE agent_call_evidence (
    span_id TEXT NOT NULL
        REFERENCES agent_call_spans(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    part_id INTEGER,
    role TEXT NOT NULL CHECK(role IN ('candidate', 'citation')),
    rank INTEGER NOT NULL CHECK(rank > 0),
    score REAL,
    PRIMARY KEY(span_id, role, rank)
);

CREATE INDEX idx_agent_call_evidence_message
ON agent_call_evidence(message_id, part_id, span_id);
