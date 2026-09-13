ALTER TABLE source_accounts
ADD COLUMN projection_status TEXT NOT NULL DEFAULT 'ready'
    CHECK(projection_status IN ('indexing', 'ready', 'degraded'));


ALTER TABLE conversations
ADD COLUMN is_indexing_enabled INTEGER NOT NULL DEFAULT 1
    CHECK(is_indexing_enabled IN (0, 1));

CREATE INDEX idx_conversations_indexing_enabled
ON conversations(is_indexing_enabled, id);
