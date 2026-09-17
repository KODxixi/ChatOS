-- ChatOS v0.4.2: derived-override extensions + incremental-organize pending queue.
--
-- Hard rule: human PATCH state (favorite / archived / note) and automatic
-- activity/classification results are written ONLY to conversation_overrides.
-- The raw conversations table stays read-only.  All read paths COALESCE the
-- override over the base column.
--
-- organize_pending persists conversations whose task extraction succeeded but
-- whose project projection did not.  A later run with NO new messages must
-- still retry project projection for these conversations (watermark already
-- advanced, so affected would otherwise be empty and recovery would skip).

ALTER TABLE conversation_overrides ADD COLUMN is_favorite INTEGER;
ALTER TABLE conversation_overrides ADD COLUMN is_archived INTEGER;
ALTER TABLE conversation_overrides ADD COLUMN note TEXT;

CREATE TABLE IF NOT EXISTS organize_pending (
    conversation_id TEXT PRIMARY KEY,
    pending_since INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_organize_pending_since
ON organize_pending(pending_since);
