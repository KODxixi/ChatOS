CREATE TABLE model_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL
        CHECK(provider IN ('lm-studio', 'openai-compatible')),
    base_url TEXT NOT NULL,
    model TEXT,
    auth_kind TEXT NOT NULL
        CHECK(auth_kind IN ('none', 'bearer')),
    is_default INTEGER NOT NULL DEFAULT 0
        CHECK(is_default IN (0, 1)),
    privacy_ack_version TEXT,
    privacy_ack_fingerprint TEXT,
    privacy_ack_at INTEGER,
    last_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(last_status IN ('unknown', 'ready', 'offline')),
    last_checked_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX model_profiles_one_default
ON model_profiles(is_default)
WHERE is_default = 1;

CREATE TABLE remote_model_send_consents (
    profile_id TEXT NOT NULL
        REFERENCES model_profiles(id) ON DELETE CASCADE,
    endpoint_fingerprint TEXT NOT NULL,
    confirmed_at INTEGER NOT NULL,
    PRIMARY KEY(profile_id, endpoint_fingerprint)
);

CREATE TABLE agent_threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    default_scope_json TEXT NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0
        CHECK(is_archived IN (0, 1)),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_agent_threads_updated
ON agent_threads(is_archived, updated_at DESC, id DESC);

CREATE TABLE agent_turns (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL
        REFERENCES agent_threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
        CHECK(status IN (
            'complete',
            'streaming',
            'cancelled',
            'error',
            'interrupted'
        )),
    model_profile_id TEXT REFERENCES model_profiles(id),
    scope_json TEXT NOT NULL,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    CHECK(
        (role = 'user' AND model_profile_id IS NULL AND status = 'complete')
        OR (role = 'assistant' AND model_profile_id IS NOT NULL)
    )
);

CREATE INDEX idx_agent_turns_thread_time
ON agent_turns(thread_id, created_at ASC, id ASC);

CREATE UNIQUE INDEX agent_turns_one_streaming_assistant
ON agent_turns(thread_id)
WHERE role = 'assistant' AND status = 'streaming';

CREATE TABLE agent_citations (
    assistant_turn_id TEXT NOT NULL
        REFERENCES agent_turns(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    message_part_id INTEGER REFERENCES message_parts(id),
    sender_name TEXT NOT NULL DEFAULT '',
    sent_at INTEGER NOT NULL,
    excerpt TEXT NOT NULL,
    PRIMARY KEY(assistant_turn_id, ordinal)
);

CREATE INDEX idx_agent_citations_message
ON agent_citations(message_id, assistant_turn_id);
