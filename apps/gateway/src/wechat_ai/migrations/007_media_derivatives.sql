CREATE TABLE media_derivatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('ocr', 'transcript')),
    status TEXT NOT NULL CHECK(status IN ('ready', 'skipped', 'error')),
    text_content TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(asset_id, kind, content_hash)
);

CREATE INDEX idx_media_derivatives_asset_kind
ON media_derivatives(asset_id, kind, updated_at DESC);
