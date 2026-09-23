-- Guard new writes. Existing vectors are rebuilt from source by the migration tool.
CREATE TRIGGER IF NOT EXISTS message_embeddings_space_insert
BEFORE INSERT ON message_embeddings
WHEN NEW.status = 'indexed' AND (
    length(NEW.embedding) != 1024 * 4 OR
    NEW.model_version != 'text-embedding-qwen3-embedding-0.6b'
)
BEGIN
    SELECT RAISE(ABORT, 'ChatOS requires 0.6B / 1024 embedding vectors');
END;

CREATE TRIGGER IF NOT EXISTS message_embeddings_space_update
BEFORE UPDATE ON message_embeddings
WHEN NEW.status = 'indexed' AND (
    length(NEW.embedding) != 1024 * 4 OR
    NEW.model_version != 'text-embedding-qwen3-embedding-0.6b'
)
BEGIN
    SELECT RAISE(ABORT, 'ChatOS requires 0.6B / 1024 embedding vectors');
END;
