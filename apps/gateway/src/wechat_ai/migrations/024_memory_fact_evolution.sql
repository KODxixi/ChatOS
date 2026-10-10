-- 024: 记忆事实演化（fact evolution）
--
-- 设计意图：
--   新事实进来时，同一主体（scope_kind+scope_id）+ 同一属性键（subject_key）
--   + 同一 memory_type 的旧 active 记忆（suggested/confirmed）不物理删除，
--   而是标记 status='superseded'，并通过 superseded_by 指向新记忆 id。
--
-- 既有 schema（006_gchat_memory_projects.sql）已预留：
--   - status CHECK 已包含 'superseded'
--   - superseded_by TEXT REFERENCES gchat_memories(id)
--   本迁移只补一个用于「同一主体+同一键」匹配的 subject_key 列 + 索引。
--
-- 幂等：ALTER TABLE ADD COLUMN 在 SQLite 中若列已存在会报错；
-- 迁移框架按 schema_version 只跑一次，正常情况下不会重复执行。

ALTER TABLE gchat_memories
ADD COLUMN subject_key TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_gchat_memories_subject_key
ON gchat_memories(scope_kind, scope_id, memory_type, subject_key, status);
