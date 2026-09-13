-- Speed up /api/health media_health probes: the previous indexes covered
-- (asset_id, kind) lookups but not the status/error_code filters that
-- media_health runs on every health check.  With 6k+ rows a full table
-- scan per query made health probes take several seconds.
CREATE INDEX IF NOT EXISTS idx_media_derivatives_status_error
ON media_derivatives(status, error_code);
