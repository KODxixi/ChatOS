import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';

test('live reminders retain pending and in-progress tasks but hide draft/final states', () => {
  const source = readFileSync(new URL('../server.ts', import.meta.url), 'utf8');
  const sql = source.match(/const topTodos = new Map\([\s\S]*?database.prepare\(`([\s\S]*?)`\)/)?.[1];
  assert.ok(sql, 'execute the production reminder query');
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(`CREATE TABLE tasks(task_id TEXT, conversation_id TEXT, content TEXT,
      sender_name TEXT, sent_at INTEGER, due_date TEXT, priority TEXT,
      source_message_id INTEGER, status TEXT)`);
    const statuses = ['todo', 'pending', 'in_progress', 'draft', 'completed', 'done', 'ignored'];
    statuses.forEach((status, i) => db.prepare('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)')
      .run(status, status, 'synthetic task', 'Test', i + 1, null, 'medium', i + 100, status));
    const rows = db.prepare(sql).all();
    assert.deepEqual(rows.map(r => r.task_id).sort(), ['in_progress', 'pending', 'todo']);
    assert.equal(rows.find(r => r.task_id === 'in_progress').status, 'in_progress');
  } finally { db.close(); }
});
