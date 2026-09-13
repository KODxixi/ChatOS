CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'archived')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_projects_status_updated
ON projects(status, updated_at DESC, id DESC);

CREATE TABLE project_conversations (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    assignment_source TEXT NOT NULL DEFAULT 'user'
        CHECK(assignment_source IN ('user', 'agent', 'rule')),
    confidence REAL NOT NULL DEFAULT 1.0
        CHECK(confidence >= 0 AND confidence <= 1),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(project_id, conversation_id)
);

CREATE INDEX idx_project_conversations_conversation
ON project_conversations(conversation_id, project_id);

CREATE TABLE project_pulses (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    open_loops_json TEXT NOT NULL DEFAULT '[]',
    latest_activity_at INTEGER,
    source_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK(status IN ('ready', 'indexing', 'degraded')),
    updated_at INTEGER NOT NULL
);

CREATE TABLE gchat_memories (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL
        CHECK(scope_kind IN ('global', 'project', 'conversation')),
    scope_id TEXT NOT NULL DEFAULT '',
    memory_type TEXT NOT NULL
        CHECK(memory_type IN ('fact', 'preference', 'goal', 'decision', 'constraint', 'risk')),
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK(status IN ('suggested', 'confirmed', 'rejected', 'superseded', 'archived')),
    confidence REAL NOT NULL DEFAULT 0.5
        CHECK(confidence >= 0 AND confidence <= 1),
    source_hash TEXT NOT NULL DEFAULT '',
    superseded_by TEXT REFERENCES gchat_memories(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    confirmed_at INTEGER
);

CREATE INDEX idx_gchat_memories_scope_status
ON gchat_memories(scope_kind, scope_id, status, updated_at DESC);

CREATE TABLE gchat_memory_evidence (
    memory_id TEXT NOT NULL REFERENCES gchat_memories(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    part_id INTEGER REFERENCES message_parts(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'supports'
        CHECK(relation IN ('supports', 'contradicts')),
    PRIMARY KEY(memory_id, message_id, part_id, relation)
);

CREATE INDEX idx_gchat_memory_evidence_message
ON gchat_memory_evidence(message_id, memory_id);

CREATE TABLE gchat_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed', 'accepted', 'rejected', 'superseded')),
    confidence REAL NOT NULL DEFAULT 0.5
        CHECK(confidence >= 0 AND confidence <= 1),
    due_at INTEGER,
    superseded_by TEXT REFERENCES gchat_decisions(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_gchat_decisions_project_status
ON gchat_decisions(project_id, status, updated_at DESC);

CREATE TABLE gchat_decision_evidence (
    decision_id TEXT NOT NULL REFERENCES gchat_decisions(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    part_id INTEGER REFERENCES message_parts(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'supports'
        CHECK(relation IN ('supports', 'contradicts')),
    PRIMARY KEY(decision_id, message_id, part_id, relation)
);

CREATE INDEX idx_gchat_decision_evidence_message
ON gchat_decision_evidence(message_id, decision_id);
