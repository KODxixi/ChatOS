import Database from 'better-sqlite3';
import path from 'node:path';
import fs from 'node:fs';

export interface Session {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
  metadata: string;
}

export interface Message {
  id: number;
  sessionId: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  createdAt: number;
  metadata: string;
}

export class SessionManager {
  private db: Database.Database;

  constructor(dbPath: string) {
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.migrate();
  }

  private migrate(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT 'New Session',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        message_count INTEGER NOT NULL DEFAULT 0,
        metadata TEXT NOT NULL DEFAULT '{}'
      );
      CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (session_id) REFERENCES sessions(id)
      );
      CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
      CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
    `);

    // 兼容旧库：如果有 thread_id 列，忽略（不删除，避免数据丢失）
  }

  createSession(title = 'New Session'): Session {
    const id = `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const now = Date.now();
    this.db.prepare(
      'INSERT INTO sessions (id, title, created_at, updated_at, message_count) VALUES (?, ?, ?, ?, 0)'
    ).run(id, title, now, now);
    return this.getSession(id)!;
  }

  getSession(id: string): Session | null {
    const row = this.db.prepare('SELECT * FROM sessions WHERE id = ?').get(id) as any;
    return row ? this.mapSession(row) : null;
  }

  listSessions(limit = 50): Session[] {
    const rows = this.db.prepare('SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?').all(limit) as any[];
    return rows.map((r) => this.mapSession(r));
  }

  renameSession(id: string, title: string): void {
    this.db.prepare('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?').run(title, Date.now(), id);
  }

  updateSession(id: string, updates: Partial<Pick<Session, 'title' | 'metadata'>>): void {
    const sets: string[] = [];
    const vals: unknown[] = [];
    if (updates.title !== undefined) { sets.push('title = ?'); vals.push(updates.title); }
    if (updates.metadata !== undefined) { sets.push('metadata = ?'); vals.push(updates.metadata); }
    sets.push('updated_at = ?'); vals.push(Date.now());
    vals.push(id);
    this.db.prepare(`UPDATE sessions SET ${sets.join(', ')} WHERE id = ?`).run(...vals);
  }

  deleteSession(id: string): void {
    this.db.prepare('DELETE FROM messages WHERE session_id = ?').run(id);
    this.db.prepare('DELETE FROM sessions WHERE id = ?').run(id);
  }

  addMessage(sessionId: string, role: Message['role'], content: string, metadata = '{}'): void {
    const now = Date.now();
    this.db.prepare(
      'INSERT INTO messages (session_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?)'
    ).run(sessionId, role, content, now, metadata);
    this.db.prepare('UPDATE sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?').run(now, sessionId);
  }

  /** 获取对话历史（升序，最早在前） */
  getMessages(sessionId: string, limit = 100): Message[] {
    const rows = this.db.prepare(
      'SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?'
    ).all(sessionId, limit) as Message[];
    return rows;
  }

  private mapSession(row: any): Session {
    return {
      id: row.id,
      title: row.title,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      messageCount: row.message_count,
      metadata: row.metadata,
    };
  }

  close(): void {
    this.db.close();
  }
}
