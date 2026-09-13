CREATE TABLE channel_accounts (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL CHECK(channel IN ('weixin')),
    external_account_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    display_name TEXT,
    api_base_url TEXT NOT NULL,
    bot_token_ref TEXT NOT NULL UNIQUE,
    get_updates_buf TEXT NOT NULL DEFAULT '',
    active_thread_id TEXT REFERENCES agent_threads(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'online' CHECK(status IN (
        'online', 'reauth_required', 'disconnected', 'error'
    )),
    last_inbound_at INTEGER,
    last_outbound_at INTEGER,
    last_error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(channel, external_account_id)
);

CREATE INDEX idx_channel_accounts_status
ON channel_accounts(channel, status, updated_at DESC);

CREATE TABLE channel_contexts (
    account_id TEXT NOT NULL REFERENCES channel_accounts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    context_token_ref TEXT NOT NULL UNIQUE,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(account_id, user_id)
);

CREATE TABLE channel_inbox (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES channel_accounts(id) ON DELETE CASCADE,
    external_message_id TEXT NOT NULL,
    from_user_id TEXT NOT NULL,
    conversation_kind TEXT NOT NULL CHECK(conversation_kind IN ('direct', 'group')),
    message_type TEXT NOT NULL,
    text_content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'queued', 'ignored', 'completed', 'failed'
    )),
    ignored_reason TEXT,
    agent_run_id TEXT,
    received_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(account_id, external_message_id)
);

CREATE INDEX idx_channel_inbox_pending
ON channel_inbox(status, received_at ASC, id ASC);

CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT REFERENCES agent_threads(id) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK(source IN ('http', 'weixin')),
    source_ref TEXT,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'queued', 'running', 'waiting_approval', 'completed',
        'failed', 'cancelled', 'interrupted'
    )),
    current_seq INTEGER NOT NULL DEFAULT 0 CHECK(current_seq >= 0),
    answer_text TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    cancel_requested_at INTEGER,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    updated_at INTEGER NOT NULL,
    UNIQUE(source, source_ref)
);

CREATE INDEX idx_agent_runs_status_created
ON agent_runs(status, created_at ASC, id ASC);

CREATE TABLE agent_run_events (
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK(seq > 0),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id, seq)
);

CREATE TABLE channel_outbox (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES channel_accounts(id) ON DELETE CASCADE,
    inbox_id TEXT REFERENCES channel_inbox(id) ON DELETE SET NULL,
    agent_run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
    to_user_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    context_token_ref TEXT NOT NULL,
    text_content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
        'queued', 'sending', 'sent', 'failed'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_error_code TEXT,
    created_at INTEGER NOT NULL,
    sent_at INTEGER,
    updated_at INTEGER NOT NULL,
    UNIQUE(account_id, client_id),
    UNIQUE(agent_run_id)
);

CREATE INDEX idx_channel_outbox_delivery
ON channel_outbox(status, created_at ASC, id ASC);

CREATE TABLE agent_route_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
    thread_id TEXT REFERENCES agent_threads(id) ON DELETE SET NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    project_name TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    route_source TEXT NOT NULL CHECK(route_source IN ('explicit', 'model')),
    status TEXT NOT NULL DEFAULT 'suggested' CHECK(status IN (
        'suggested', 'confirmed', 'rejected', 'changed'
    )),
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER
);

CREATE INDEX idx_agent_route_candidates_review
ON agent_route_candidates(status, created_at DESC, id DESC);
