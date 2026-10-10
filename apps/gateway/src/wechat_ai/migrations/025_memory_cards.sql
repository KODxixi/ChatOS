-- 025: 记忆卡片派生表（memory_cards）。
-- 由 services/memory_cards.py 写入，是结构化查询索引；人读主存储在
-- D:\ChatOS-Vault\knowledge\memory-cards\（Markdown + index.json）。
-- 与微信原始表严格分离，只增不改。

CREATE TABLE IF NOT EXISTS memory_cards (
    id TEXT PRIMARY KEY,
    card_type TEXT NOT NULL
        CHECK(card_type IN (
            'preference', 'relationship', 'project',
            'fact', 'decision', 'constraint', 'event'
        )),
    subject TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5
        CHECK(confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'superseded')),
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    conversation_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_cards_type
ON memory_cards(card_type, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_cards_subject
ON memory_cards(subject);
