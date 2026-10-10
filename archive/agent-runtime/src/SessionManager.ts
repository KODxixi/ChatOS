import Database from 'better-sqlite3';
import path from 'node:path';
import fs from 'node:fs';
import type { ChatMessage, ToolCall } from './LocalModelClient.js';

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
    this.db.pragma('foreign_keys = ON');
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
    const deleteMessages = this.db.prepare('DELETE FROM messages WHERE session_id = ?');
    const deleteSession = this.db.prepare('DELETE FROM sessions WHERE id = ?');
    this.db.transaction(() => {
      deleteMessages.run(id);
      deleteSession.run(id);
    })();
  }

  addMessage(sessionId: string, role: Message['role'], content: string, metadata = '{}'): void {
    const now = Date.now();
    const insert = this.db.prepare(
      'INSERT INTO messages (session_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?)'
    );
    const update = this.db.prepare(
      'UPDATE sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?'
    );
    this.db.transaction(() => {
      insert.run(sessionId, role, content, now, metadata);
      update.run(now, sessionId);
    })();
  }

  addChatMessage(sessionId: string, message: ChatMessage): void {
    const metadata: Record<string, unknown> = {};
    if (message.tool_calls) metadata.tool_calls = message.tool_calls;
    if (message.tool_call_id) metadata.tool_call_id = message.tool_call_id;
    if (message.name) metadata.name = message.name;
    this.addMessage(sessionId, message.role, message.content, JSON.stringify(metadata));
  }

  /** 获取对话历史（升序，最早在前） */
  getMessages(sessionId: string, limit = 100): Message[] {
    const rows = this.db.prepare(
      'SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?'
    ).all(sessionId, limit) as any[];
    return rows.reverse().map((row) => this.mapMessage(row));
  }

  getChatMessages(sessionId: string, limit = 100, maxChars = Number.POSITIVE_INFINITY): ChatMessage[] {
    const decoded = this.getMessages(sessionId, limit).flatMap((message) => {
      const metadata = this.parseMetadata(message.metadata);
      const chatMessage: ChatMessage = { role: message.role, content: message.content };

      if (message.role === 'assistant' && Array.isArray(metadata.tool_calls)
        && metadata.tool_calls.every((toolCall) => this.isToolCall(toolCall))) {
        chatMessage.tool_calls = metadata.tool_calls;
      }
      if (message.role === 'tool') {
        if (typeof metadata.tool_call_id !== 'string' || typeof metadata.name !== 'string') return [];
        chatMessage.tool_call_id = metadata.tool_call_id;
        chatMessage.name = metadata.name;
      }
      return [chatMessage];
    });
    const messages: ChatMessage[] = [];
    for (let index = 0; index < decoded.length; index++) {
      const message = decoded[index];
      if (message.role === 'tool') continue;
      if (message.role !== 'assistant' || !message.tool_calls?.length) {
        messages.push(message);
        continue;
      }

      const expectedIds = new Set(message.tool_calls.map((toolCall) => toolCall.id));
      const group: ChatMessage[] = [message];
      let cursor = index + 1;
      while (cursor < decoded.length && decoded[cursor].role === 'tool') {
        const toolMessage = decoded[cursor];
        if (toolMessage.tool_call_id && expectedIds.delete(toolMessage.tool_call_id)) group.push(toolMessage);
        cursor += 1;
      }
      if (expectedIds.size === 0 && group.length === message.tool_calls.length + 1) messages.push(...group);
      index = cursor - 1;
    }
    const selected: ChatMessage[] = [];
    let usedChars = 0;
    for (let index = messages.length - 1; index >= 0; index--) {
      const size = JSON.stringify(messages[index]).length;
      if (selected.length > 0 && usedChars + size > maxChars) break;
      selected.push(messages[index]);
      usedChars += size;
    }
    selected.reverse();
    while (selected[0]?.role === 'tool') selected.shift();
    return selected;
  }

  private parseMetadata(metadata: string): Record<string, unknown> {
    try {
      const parsed: unknown = JSON.parse(metadata);
      return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
        ? parsed as Record<string, unknown>
        : {};
    } catch {
      return {};
    }
  }

  private isToolCall(value: unknown): value is ToolCall {
    if (value === null || typeof value !== 'object') return false;
    const toolCall = value as Partial<ToolCall>;
    return typeof toolCall.id === 'string'
      && toolCall.type === 'function'
      && toolCall.function !== null
      && typeof toolCall.function === 'object'
      && typeof toolCall.function.name === 'string'
      && typeof toolCall.function.arguments === 'string';
  }

  private mapMessage(row: any): Message {
    return {
      id: row.id,
      sessionId: row.session_id,
      role: row.role,
      content: row.content,
      createdAt: row.created_at,
      metadata: row.metadata,
    };
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
