CREATE TABLE source_apps (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    connector_kind TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE TABLE source_accounts (
    id TEXT PRIMARY KEY,
    source_app_id TEXT NOT NULL REFERENCES source_apps(id),
    external_account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK(status IN ('ready', 'missing', 'unsupported', 'disabled')),
    last_imported_at INTEGER,
    UNIQUE(source_app_id, external_account_id)
);

INSERT INTO source_apps(id, display_name, connector_kind, created_at)
VALUES ('wechat', CAST(X'E5BEAEE4BFA1' AS TEXT), 'wechat', 0);

INSERT INTO source_accounts(
    id,
    source_app_id,
    external_account_id,
    display_name,
    status
)
VALUES (
    'wechat:default',
    'wechat',
    'default',
    CAST(X'E5BEAEE4BFA1' AS TEXT),
    'ready'
);

CREATE TABLE conversation_origins (
    conversation_id TEXT PRIMARY KEY
        REFERENCES conversations(id) ON DELETE CASCADE,
    source_account_id TEXT NOT NULL REFERENCES source_accounts(id),
    external_conversation_id TEXT NOT NULL,
    UNIQUE(source_account_id, external_conversation_id)
);

INSERT INTO conversation_origins(
    conversation_id,
    source_account_id,
    external_conversation_id
)
SELECT id, 'wechat:default', source_conversation_id
FROM conversations;

CREATE INDEX idx_conversation_origins_account
ON conversation_origins(source_account_id, conversation_id);

CREATE TABLE message_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL
        CHECK(kind IN (
            'text',
            'link',
            'image',
            'file',
            'audio',
            'video',
            'sticker',
            'attachment',
            'unsupported'
        )),
    status TEXT NOT NULL
        CHECK(status IN ('resolved', 'unresolved', 'missing')),
    text_content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(message_id, ordinal)
);

INSERT INTO message_parts(
    message_id,
    ordinal,
    kind,
    status,
    text_content
)
SELECT
    id,
    0,
    CASE type
        WHEN 'text' THEN 'text'
        WHEN 'link' THEN 'link'
        WHEN 'image' THEN 'image'
        WHEN 'file' THEN 'file'
        WHEN 'voice' THEN 'audio'
        WHEN 'video' THEN 'video'
        WHEN 'sticker' THEN 'sticker'
        WHEN 'attachment' THEN 'attachment'
        ELSE 'unsupported'
    END,
    CASE
        WHEN type IN ('text', 'link') THEN 'resolved'
        WHEN type = 'file' AND content <> '' THEN 'resolved'
        ELSE 'unresolved'
    END,
    CASE WHEN type IN ('text', 'link', 'file') THEN content ELSE '' END
FROM messages;

CREATE INDEX idx_message_parts_message
ON message_parts(message_id, ordinal);

CREATE TABLE assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL REFERENCES message_parts(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK(status IN ('resolved', 'unresolved', 'missing')),
    file_name TEXT,
    mime_type TEXT,
    byte_size INTEGER CHECK(byte_size IS NULL OR byte_size >= 0),
    external_url TEXT,
    local_path TEXT,
    sha256 TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(part_id, ordinal)
);

INSERT INTO assets(part_id, ordinal, kind, status)
SELECT id, 0, kind, 'unresolved'
FROM message_parts
WHERE kind NOT IN ('text', 'link');

CREATE INDEX idx_assets_part
ON assets(part_id, ordinal);

CREATE INDEX idx_assets_kind_status
ON assets(kind, status);

CREATE TABLE retrieval_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_part_id INTEGER NOT NULL UNIQUE
        REFERENCES message_parts(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    text_content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    index_version INTEGER NOT NULL CHECK(index_version > 0),
    sender_name TEXT NOT NULL DEFAULT '',
    sent_at INTEGER NOT NULL
);

CREATE INDEX idx_retrieval_chunks_conversation_time
ON retrieval_chunks(conversation_id, sent_at DESC, id DESC);

CREATE VIRTUAL TABLE retrieval_chunk_fts USING fts5(
    text_content,
    sender_name,
    conversation_title,
    tokenize = 'unicode61'
);

CREATE TRIGGER retrieval_chunks_after_insert
AFTER INSERT ON retrieval_chunks
BEGIN
    INSERT INTO retrieval_chunk_fts(
        rowid,
        text_content,
        sender_name,
        conversation_title
    )
    VALUES (
        NEW.id,
        NEW.text_content,
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

CREATE TRIGGER retrieval_chunks_after_update
AFTER UPDATE OF conversation_id, text_content, sender_name ON retrieval_chunks
BEGIN
    DELETE FROM retrieval_chunk_fts WHERE rowid = OLD.id;
    INSERT INTO retrieval_chunk_fts(
        rowid,
        text_content,
        sender_name,
        conversation_title
    )
    VALUES (
        NEW.id,
        NEW.text_content,
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

CREATE TRIGGER retrieval_chunks_after_delete
AFTER DELETE ON retrieval_chunks
BEGIN
    DELETE FROM retrieval_chunk_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER conversations_after_retrieval_title_update
AFTER UPDATE OF title ON conversations
BEGIN
    UPDATE retrieval_chunk_fts
    SET conversation_title = NEW.title
    WHERE rowid IN (
        SELECT id
        FROM retrieval_chunks
        WHERE conversation_id = NEW.id
    );
END;

INSERT INTO retrieval_chunks(
    message_part_id,
    message_id,
    conversation_id,
    kind,
    text_content,
    content_hash,
    index_version,
    sender_name,
    sent_at
)
SELECT
    message_parts.id,
    messages.id,
    messages.conversation_id,
    message_parts.kind,
    message_parts.text_content,
    '',
    1,
    messages.sender_name,
    messages.sent_at
FROM message_parts
JOIN messages ON messages.id = message_parts.message_id
WHERE message_parts.status = 'resolved'
  AND message_parts.text_content <> '';

CREATE TABLE graph_nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL
        CHECK(kind IN (
            'source_app',
            'source_account',
            'conversation',
            'participant',
            'content_kind',
            'asset',
            'group',
            'contact',
            'topic',
            'entity',
            'link_domain'
        )),
    label TEXT NOT NULL,
    trace_kind TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(kind, trace_kind, trace_id)
);

CREATE INDEX idx_graph_nodes_trace
ON graph_nodes(trace_kind, trace_id);

CREATE TABLE graph_edges (
    id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    target_node_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK(kind IN (
            'contains',
            'participates_in',
            'has_content',
            'has_asset',
            'member_of',
            'mentions',
            'related_to',
            'links_to'
        )),
    weight INTEGER NOT NULL DEFAULT 1 CHECK(weight > 0),
    UNIQUE(source_node_id, target_node_id, kind)
);

CREATE INDEX idx_graph_edges_source
ON graph_edges(source_node_id, kind);

CREATE INDEX idx_graph_edges_target
ON graph_edges(target_node_id, kind);

CREATE TABLE graph_edge_evidence (
    edge_id TEXT NOT NULL REFERENCES graph_edges(id) ON DELETE CASCADE,
    evidence_kind TEXT NOT NULL
        CHECK(evidence_kind IN (
            'source_app',
            'source_account',
            'conversation',
            'message',
            'message_part',
            'asset'
        )),
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(edge_id, evidence_kind, evidence_id)
);
