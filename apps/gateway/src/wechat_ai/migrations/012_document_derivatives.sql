CREATE TABLE media_derivatives_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('ocr', 'transcript', 'document')),
    status TEXT NOT NULL CHECK(status IN ('ready', 'skipped', 'error')),
    text_content TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(asset_id, kind, content_hash)
);

INSERT INTO media_derivatives_v2(
    id, asset_id, kind, status, text_content, engine,
    content_hash, error_code, created_at, updated_at
)
SELECT
    id, asset_id, kind, status, text_content, engine,
    content_hash, error_code, created_at, updated_at
FROM media_derivatives;

DROP TABLE media_derivatives;
ALTER TABLE media_derivatives_v2 RENAME TO media_derivatives;

CREATE INDEX idx_media_derivatives_asset_kind
ON media_derivatives(asset_id, kind, updated_at DESC);
