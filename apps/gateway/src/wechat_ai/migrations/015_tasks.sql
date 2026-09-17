-- ChatOS v0.3.0: tasks / todo extraction.
-- Derived data (extracted from messages). WeChat raw tables are untouched.
CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    conversation_title TEXT,
    content          TEXT NOT NULL,
    sender_name      TEXT,
    sent_at          INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'todo',
    priority         TEXT NOT NULL DEFAULT 'medium',
    due_date         TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_sent_at
    ON tasks(sent_at);
CREATE INDEX IF NOT EXISTS idx_tasks_conversation
    ON tasks(conversation_id);
