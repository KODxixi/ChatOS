-- User-managed conversation groups live in derived state; imported WeChat
-- conversations remain read-only.
CREATE TABLE IF NOT EXISTS conversation_groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL COLLATE NOCASE UNIQUE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

INSERT OR IGNORE INTO conversation_groups (id, name, sort_order, created_at, updated_at)
VALUES
    ('personal', '个人', 10, CAST(strftime('%s', 'now') AS INTEGER) * 1000, CAST(strftime('%s', 'now') AS INTEGER) * 1000),
    ('work', '工作', 20, CAST(strftime('%s', 'now') AS INTEGER) * 1000, CAST(strftime('%s', 'now') AS INTEGER) * 1000),
    ('study', '学习', 30, CAST(strftime('%s', 'now') AS INTEGER) * 1000, CAST(strftime('%s', 'now') AS INTEGER) * 1000);

ALTER TABLE conversation_overrides
ADD COLUMN group_id TEXT REFERENCES conversation_groups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_overrides_group
ON conversation_overrides(group_id);
