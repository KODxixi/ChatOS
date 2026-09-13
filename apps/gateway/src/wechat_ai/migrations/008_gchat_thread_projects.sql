ALTER TABLE agent_threads
ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE SET NULL;

CREATE INDEX idx_agent_threads_project_updated
ON agent_threads(project_id, is_archived, updated_at DESC, id DESC);
