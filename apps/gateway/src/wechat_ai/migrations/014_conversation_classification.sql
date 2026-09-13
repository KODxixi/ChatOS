-- Conversation classification fields for ChatOS taxonomy.
-- domain: project/work/personal/service/subscription
-- project_id: linked architecture project (for domain=project, syncs with ArchOS)
-- project_role: core/collab/client/vendor (role of this conversation in the project)
-- classification_status: auto/manual/needs_review
-- classified_at: timestamp of last classification
ALTER TABLE conversations ADD COLUMN domain TEXT;
ALTER TABLE conversations ADD COLUMN project_id TEXT;
ALTER TABLE conversations ADD COLUMN project_role TEXT;
ALTER TABLE conversations ADD COLUMN classification_status TEXT DEFAULT 'needs_review';
ALTER TABLE conversations ADD COLUMN classified_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_conversations_domain
ON conversations(domain);
CREATE INDEX IF NOT EXISTS idx_conversations_project_id
ON conversations(project_id);
