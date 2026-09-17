-- LLM result cache for summary / todo filter / topic extraction.
CREATE TABLE IF NOT EXISTS llm_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT UNIQUE NOT NULL,
    task_type TEXT NOT NULL,
    reference_id TEXT,
    result TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_key ON llm_cache(cache_key);
CREATE INDEX IF NOT EXISTS idx_llm_cache_ref ON llm_cache(task_type, reference_id);
