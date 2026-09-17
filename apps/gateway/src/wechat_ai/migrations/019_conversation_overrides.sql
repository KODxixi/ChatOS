-- ChatOS v0.4.1: derived conversation override table.
-- Activity evaluation and classification results are written here,
-- NOT to the raw conversations table.  Queries COALESCE with the base columns.

CREATE TABLE IF NOT EXISTS conversation_overrides (
    conversation_id      TEXT PRIMARY KEY,
    is_indexing_enabled INTEGER,
    domain               TEXT,
    project_id           TEXT,
    project_role         TEXT,
    classification_status TEXT,
    classified_at        INTEGER,
    updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_overrides_domain
ON conversation_overrides(domain);

CREATE INDEX IF NOT EXISTS idx_overrides_project_id
ON conversation_overrides(project_id);
