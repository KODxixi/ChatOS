-- 023: trigram FTS for Chinese substring BM25 recall.
-- The existing message_fts uses unicode61, which tokenizes Chinese into
-- individual characters and gives poor substring / phrase recall.  trigram
-- tokenizer builds 3-char windows, so "咏滨名邸" is matched as a real substring.
-- We mirror the message_fts trigger discipline (insert/update/delete) so the
-- derived table never drifts from messages.  Only text content is indexed.

CREATE VIRTUAL TABLE IF NOT EXISTS message_fts_trigram USING fts5(
    content,
    tokenize = 'trigram'
);

-- Backfill existing messages.  Non-text rows index as empty string (the
-- message_fts convention), so the trigger has nothing special to do.
INSERT INTO message_fts_trigram(rowid, content)
SELECT
    m.id,
    CASE WHEN m.type = 'text' THEN m.content ELSE '' END
FROM messages m
WHERE NOT EXISTS (
    SELECT 1 FROM message_fts_trigram t WHERE t.rowid = m.id
);

CREATE TRIGGER IF NOT EXISTS message_fts_trigram_after_insert
AFTER INSERT ON messages
BEGIN
    INSERT INTO message_fts_trigram(rowid, content)
    VALUES (
        NEW.id,
        CASE WHEN NEW.type = 'text' THEN NEW.content ELSE '' END
    );
END;

CREATE TRIGGER IF NOT EXISTS message_fts_trigram_after_update
AFTER UPDATE OF conversation_id, content, sender_name, type ON messages
BEGIN
    DELETE FROM message_fts_trigram WHERE rowid = OLD.id;
    INSERT INTO message_fts_trigram(rowid, content)
    VALUES (
        NEW.id,
        CASE WHEN NEW.type = 'text' THEN NEW.content ELSE '' END
    );
END;

CREATE TRIGGER IF NOT EXISTS message_fts_trigram_after_delete
AFTER DELETE ON messages
BEGIN
    DELETE FROM message_fts_trigram WHERE rowid = OLD.id;
END;
