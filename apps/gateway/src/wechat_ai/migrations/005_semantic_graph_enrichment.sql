ALTER TABLE retrieval_chunks
ADD COLUMN semantic_content_hash TEXT;

ALTER TABLE retrieval_chunks
ADD COLUMN semantic_version INTEGER
    CHECK(semantic_version IS NULL OR semantic_version > 0);

ALTER TABLE source_accounts
ADD COLUMN semantic_graph_status TEXT NOT NULL DEFAULT 'skipped'
    CHECK(
        semantic_graph_status IN (
            'indexing',
            'ready',
            'skipped',
            'degraded'
        )
    );

ALTER TABLE source_accounts
ADD COLUMN semantic_graph_error_code TEXT
    CHECK(
        semantic_graph_error_code IS NULL
        OR semantic_graph_error_code IN (
            'MODEL_UNAVAILABLE',
            'INVALID_MODEL_OUTPUT',
            'SEMANTIC_WRITE_FAILED'
        )
    );

ALTER TABLE source_accounts
ADD COLUMN semantic_graph_updated_at INTEGER;

CREATE INDEX idx_retrieval_chunks_semantic_pending
ON retrieval_chunks(semantic_version, semantic_content_hash, id);

CREATE INDEX idx_source_accounts_semantic_status
ON source_accounts(semantic_graph_status, id);
