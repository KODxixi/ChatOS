CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    source_conversation_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('direct', 'group', 'system', 'unknown')),
    last_message_at INTEGER,
    message_count INTEGER NOT NULL DEFAULT 0,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    source_message_id TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    sender_id TEXT,
    sender_name TEXT NOT NULL DEFAULT '',
    sent_at INTEGER NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    raw_payload_json TEXT
);

CREATE INDEX idx_messages_conversation_time
ON messages(conversation_id, sent_at DESC, id DESC);

CREATE VIRTUAL TABLE message_fts USING fts5(
    content,
    sender_name,
    conversation_title,
    tokenize = 'unicode61'
);

CREATE TABLE app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TRIGGER messages_after_insert
AFTER INSERT ON messages
BEGIN
    INSERT INTO message_fts(
        rowid,
        content,
        sender_name,
        conversation_title
    )
    VALUES (
        NEW.id,
        CASE WHEN NEW.type = 'text' THEN NEW.content ELSE '' END,
        NEW.sender_name,
        COALESCE(
            (
                SELECT title
                FROM conversations
                WHERE id = NEW.conversation_id
            ),
            ''
        )
    );
END;

CREATE TRIGGER messages_after_update
AFTER UPDATE OF conversation_id, content, sender_name, type ON messages
BEGIN
    DELETE FROM message_fts WHERE rowid = OLD.id;
    INSERT INTO message_fts(
        rowid,
        content,
        sender_name,
        conversation_title
    )
    VALUES (
        NEW.id,
        CASE WHEN NEW.type = 'text' THEN NEW.content ELSE '' END,
        NEW.sender_name,
        COALESCE(
            (
                SELECT title
                FROM conversations
                WHERE id = NEW.conversation_id
            ),
            ''
        )
    );
END;

CREATE TRIGGER messages_after_delete
AFTER DELETE ON messages
BEGIN
    DELETE FROM message_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER conversations_after_title_update
AFTER UPDATE OF title ON conversations
BEGIN
    UPDATE message_fts
    SET conversation_title = NEW.title
    WHERE rowid IN (
        SELECT id
        FROM messages
        WHERE conversation_id = NEW.id
    );
END;
