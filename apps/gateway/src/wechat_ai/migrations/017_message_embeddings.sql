-- Message embeddings for semantic search / RAG.
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    embedding BLOB NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_embeddings_conversation ON message_embeddings(conversation_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_status ON message_embeddings(status);
