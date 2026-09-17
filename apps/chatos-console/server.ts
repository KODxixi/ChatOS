import express from 'express';
import fs from 'node:fs';
import path from 'path';
import { DatabaseSync } from 'node:sqlite';
import { createServer as createViteServer } from 'vite';
import dotenv from 'dotenv';
import { captureUiRuntime, resolveLibraryDb, UI_URL, GATEWAY_URL } from '../../scripts/entry-contract.mjs';
import {
  INITIAL_SHARED_LISTS,
  INITIAL_PROJECT_COMMENTS,
  AVAILABLE_TEAM_MEMBERS,
  CURRENT_USER,
} from './src/mockData';
import { SharedTodoList, SharedTask, ProjectComment, User } from './src/types';

dotenv.config();

const app = express();
const isProduction = process.env.NODE_ENV === 'production' || process.argv.includes('--production');
const PORT = isProduction ? Number(new URL(UI_URL).port) : Number(process.env.PORT || 3109);
declare const __CHATOS_BUILD__: Record<string, unknown>;
// Production embeds identity; a running process never rereads a replaced build.json.
const consoleRoot = isProduction ? path.resolve(__dirname, '..') : process.cwd();
const repositoryRoot = path.resolve(consoleRoot, '../..');
const libraryDbPath = resolveLibraryDb(repositoryRoot);
const runtimeIdentity = isProduction ? Object.freeze({
  ...captureUiRuntime(repositoryRoot, __CHATOS_BUILD__, __filename),
  pid: process.pid, cwd: process.cwd(), startedAt: new Date().toISOString(),
}) : { service: 'chatos-console-dev', sourceRoot: consoleRoot };
const LOCAL_LLM_BASE_URL = (process.env.LOCAL_LLM_BASE_URL || 'http://127.0.0.1:1234/v1').replace(/\/+$/, '');
const LOCAL_LLM_MODEL = process.env.LOCAL_LLM_MODEL || 'qwen/qwen3-vl-8b';
const localLlmHostname = new URL(LOCAL_LLM_BASE_URL).hostname;
const localLlmIsLoopback = ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(localLlmHostname);

const openLibraryDb = () => new DatabaseSync(libraryDbPath, { readOnly: true });

const databaseHealth = () => {
  try {
    const database = openLibraryDb();
    database.prepare('SELECT 1').get();
    database.close();
    return { status: 'ready', mode: 'sqlite-readonly' };
  } catch (error) {
    return {
      status: 'unavailable',
      mode: 'sqlite-readonly',
      message: error instanceof Error ? error.message : 'unknown database error',
    };
  }
};

const escapeXml = (value: string) =>
  value.replace(/[<>&'\"]/g, character => ({
    '<': '&lt;',
    '>': '&gt;',
    '&': '&amp;',
    "'": '&apos;',
    '"': '&quot;',
  })[character] || character);

const avatarDataUri = (name: string) => {
  const initial = Array.from(name.trim())[0] || '?';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><rect width="96" height="96" rx="48" fill="#242426"/><text x="48" y="58" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="34" font-weight="650" fill="#f5f5f7">${escapeXml(initial)}</text></svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
};

const formatRelativeTime = (timestamp: number) => {
  const difference = Math.max(0, Date.now() - timestamp);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (difference < minute) return '刚刚';
  if (difference < hour) return `${Math.floor(difference / minute)}分钟前`;
  if (difference < day) return `${Math.floor(difference / hour)}小时前`;
  if (difference < 7 * day) return `${Math.floor(difference / day)}天前`;
  return new Date(timestamp).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

const domainLabel = (domain: string | null) => ({
  project: '项目协作',
  work: '工作联系',
  personal: '个人关系',
  service: '服务联系',
  subscription: '订阅会话',
}[domain || ''] || '未分类会话');

const groupTopicLabel = (domain: string | null) => ({
  project: '项目群聊',
  work: '工作群聊',
  personal: '生活群聊',
  service: '服务群聊',
  subscription: '订阅群聊',
}[domain || ''] || '群聊讨论');

const categoryFromDatabase = (domain: string | null, isGroup: boolean, indexingEnabled: boolean) => {
  if (!indexingEnabled && isGroup) return 'noisy_group';
  if (domain === 'project' || domain === 'work') return 'active_collab';
  if (domain === 'personal') return 'family_core';
  if (domain === 'service') return 'low_freq_important';
  if (domain === 'subscription') return 'noisy_group';
  return 'general';
};

const readConversationSnapshot = () => {
  const database = openLibraryDb();
  try {
    const ownerRow = database.prepare(`
      SELECT sender_id
      FROM messages
      WHERE sender_id IS NOT NULL AND sender_id != ''
      GROUP BY sender_id
      ORDER BY COUNT(*) DESC
      LIMIT 1
    `).get() as { sender_id?: string } | undefined;
    const ownerId = ownerRow?.sender_id || '';
    const cutoff = Date.now() - 90 * 86_400_000;

    const rows = database.prepare(`
      SELECT id, title, kind, message_count, last_message_at, is_favorite,
             is_indexing_enabled, domain, project_id, project_role
      FROM conversations
      WHERE kind IN ('direct', 'group') AND message_count > 0
      ORDER BY last_message_at DESC
    `).all() as Record<string, unknown>[];

    const recentStats = new Map(
      (database.prepare(`
        SELECT conversation_id,
               COUNT(DISTINCT CAST(sent_at / 86400000 AS INTEGER)) AS interaction_days,
               COUNT(*) AS recent_messages,
               SUM(CASE WHEN sender_id = ? THEN 1 ELSE 0 END) AS owner_messages
        FROM messages
        WHERE sent_at >= ?
        GROUP BY conversation_id
      `).all(ownerId, cutoff) as Record<string, unknown>[])
        .map(row => [String(row.conversation_id), row])
    );

    const latestMessages = new Map(
      (database.prepare(`
        WITH ranked AS (
          SELECT conversation_id, sender_name, content, sent_at,
                 ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY sent_at DESC) AS rank
          FROM messages
          WHERE type = 'text' AND content IS NOT NULL AND length(trim(content)) > 1
        )
        SELECT conversation_id, sender_name, content, sent_at
        FROM ranked
        WHERE rank = 1
      `).all() as Record<string, unknown>[])
        .map(row => [String(row.conversation_id), row])
    );

    const topTodos = new Map(
      (database.prepare(`
        WITH ranked AS (
          SELECT task_id, conversation_id, content, sender_name, sent_at, due_date, priority,
                 ROW_NUMBER() OVER (
                   PARTITION BY conversation_id
                   ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, sent_at DESC
                 ) AS rank
          FROM tasks
          WHERE status = 'todo'
        )
        SELECT task_id, conversation_id, content, sender_name, sent_at, due_date, priority
        FROM ranked
        WHERE rank = 1
      `).all() as Record<string, unknown>[])
        .map(row => [String(row.conversation_id), row])
    );

    const participants = new Map<string, Array<{ id: string; name: string; avatar: string }>>();
    const memberCounts = new Map<string, number>();
    (database.prepare(`
      WITH speaker_counts AS (
        SELECT m.conversation_id, m.sender_id, m.sender_name, COUNT(*) AS message_count
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.kind = 'group' AND m.sender_name IS NOT NULL AND m.sender_name != ''
        GROUP BY m.conversation_id, m.sender_id, m.sender_name
      ), ranked AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY message_count DESC) AS rank,
               COUNT(*) OVER (PARTITION BY conversation_id) AS member_count
        FROM speaker_counts
      )
      SELECT conversation_id, sender_id, sender_name, member_count
      FROM ranked
      WHERE rank <= 2
      ORDER BY conversation_id, rank
    `).all() as Record<string, unknown>[]).forEach(row => {
      const conversationId = String(row.conversation_id);
      const name = String(row.sender_name || '成员');
      const current = participants.get(conversationId) || [];
      current.push({ id: String(row.sender_id || name), name, avatar: avatarDataUri(name) });
      participants.set(conversationId, current);
      memberCounts.set(conversationId, Number(row.member_count || current.length));
    });

    const items = rows.map(row => {
      const id = String(row.id);
      const name = String(row.title || id);
      const isGroup = row.kind === 'group';
      const messageCount = Number(row.message_count || 0);
      const lastMessageAt = Number(row.last_message_at || 0);
      const indexingEnabled = Boolean(row.is_indexing_enabled);
      const domain = row.domain ? String(row.domain) : null;
      const stats = recentStats.get(id);
      const interactionDays90d = Number(stats?.interaction_days || 0);
      const recentMessages = Number(stats?.recent_messages || 0);
      const ownerMessages = Number(stats?.owner_messages || 0);
      const participation = recentMessages > 0 ? Math.round(ownerMessages / recentMessages * 100) : 0;
      const latest = latestMessages.get(id);
      const todo = topTodos.get(id);
      const favorite = Boolean(row.is_favorite);
      const daysSinceActive = lastMessageAt > 0 ? Math.max(0, (Date.now() - lastMessageAt) / 86_400_000) : 90;
      const priorityScore = Math.min(100, Math.round(
        Math.max(0, 30 - daysSinceActive)
        + Math.min(25, Math.log10(messageCount + 1) * 8)
        + (todo ? 25 : 0)
        + (favorite ? 20 : 0)
      ));
      const priorityReason = favorite
        ? '数据库星标关注'
        : todo
          ? '会话含未完成行动待办'
          : interactionDays90d > 0
            ? `近90天活跃 ${interactionDays90d} 天`
            : '按最近消息时间排序';
      const latestContent = String(latest?.content || '').replace(/\s+/g, ' ').trim();

      return {
        id,
        name,
        isGroup,
        avatar: avatarDataUri(name),
        groupParticipants: isGroup ? participants.get(id) || [] : undefined,
        memberCount: isGroup ? memberCounts.get(id) || 2 : undefined,
        roleTitle: isGroup ? undefined : String(row.project_role || domainLabel(domain)),
        groupTopic: isGroup ? groupTopicLabel(domain) : undefined,
        isFollowed: favorite,
        interactionDays90d,
        joyBoyParticipationScore: participation,
        lastActiveTime: lastMessageAt > 0 ? formatRelativeTime(lastMessageAt) : '暂无消息时间',
        twoLineSummary: latestContent || `${messageCount} 条历史消息`,
        unreadCount: 0,
        topActionTodo: todo ? {
          id: String(todo.task_id),
          title: String(todo.content),
          status: 'pending',
          assigneeRole: 'other',
          dueDate: String(todo.due_date || '待确认'),
          isUrgent: todo.priority === 'high',
          sourceMessageId: `db-task-${String(todo.task_id)}`,
          sourceConversationId: id,
          sourceConversationName: name,
          sourceSnippet: String(todo.content),
          evidenceText: todo.sender_name ? `来自 ${String(todo.sender_name)} 的会话任务` : '来自本地会话任务库',
          isIgnored: false,
          manualModified: false,
          createdAt: new Date(Number(todo.sent_at || Date.now())).toISOString(),
          projectId: row.project_id ? String(row.project_id) : undefined,
          confidence: 'clear',
        } : undefined,
        priorityScore,
        priorityReason,
        categoryTag: categoryFromDatabase(domain, isGroup, indexingEnabled),
        linkedProjectIds: row.project_id ? [String(row.project_id)] : [],
      };
    });

    const taskRevision = database.prepare('SELECT COALESCE(MAX(updated_at), 0) AS updated_at FROM tasks').get() as { updated_at?: number };
    const newestMessage = Math.max(0, ...items.map(item => Number(rows.find(row => String(row.id) === item.id)?.last_message_at || 0)));
    return {
      source: 'sqlite-readonly',
      total: items.length,
      revision: `${newestMessage}:${Number(taskRevision?.updated_at || 0)}:${items.length}`,
      updatedAt: new Date().toISOString(),
      items,
    };
  } finally {
    database.close();
  }
};

app.use(express.json());
app.get('/api/runtime', (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.json(runtimeIdentity);
});

// In-memory collaborative state (Authoritative Server State)
let sharedListsState: SharedTodoList[] = JSON.parse(JSON.stringify(INITIAL_SHARED_LISTS));
let projectCommentsState: Record<string, ProjectComment[]> = JSON.parse(
  JSON.stringify(INITIAL_PROJECT_COMMENTS)
);

// Prototype health endpoint. Only process memory is measured; no model or benchmark claims.
app.get('/api/health', (req, res) => {
  const memoryUsage = process.memoryUsage();
  const conversations = databaseHealth();
  res.json({
    status: 'ok',
    version: '2.0.4',
    mode: isProduction ? 'canonical-mixed-data' : 'development-mixed-data',
    scope: { conversations: 'sqlite-readonly', modules: 'demo', collaboration: 'process-memory' },
    gateway: { url: GATEWAY_URL, status: 'not_checked' },
    persistence: 'sqlite-readonly+memory-collaboration',
    conversations,
    model: {
      status: localLlmIsLoopback ? 'configured' : 'blocked',
      provider: 'lm-studio-local',
      name: LOCAL_LLM_MODEL,
      endpoint: LOCAL_LLM_BASE_URL,
    },
    uptimeSeconds: Math.round(process.uptime()),
    telemetry: {
      heapUsedMb: (memoryUsage.heapUsed / 1024 / 1024).toFixed(1),
      heapTotalMb: (memoryUsage.heapTotal / 1024 / 1024).toFixed(1),
      rssMb: (memoryUsage.rss / 1024 / 1024).toFixed(1),
      source: 'process.memoryUsage',
      cacheHitRate: null,
      throughput: null,
      p99LatencyMs: null,
    },
  });
});

app.get('/api/kernel/metrics', (req, res) => {
  const memoryUsage = process.memoryUsage();
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    heapUsedMb: Number((memoryUsage.heapUsed / 1024 / 1024).toFixed(1)),
    eventLoopLagMs: null,
    activeConnections: null,
    auditVerification: { status: 'not_measured' },
  });
});

app.get('/api/conversations/live', (req, res) => {
  try {
    res.json(readConversationSnapshot());
  } catch (error) {
    res.status(503).json({
      error: 'local_conversation_database_unavailable',
      message: error instanceof Error ? error.message : '无法读取本地会话数据库',
    });
  }
});

app.get('/api/conversations/:conversationId/messages', (req, res) => {
  const requestedLimit = Number(req.query.limit || 100);
  const limit = Number.isFinite(requestedLimit) ? Math.min(200, Math.max(1, requestedLimit)) : 100;
  let database: DatabaseSync | null = null;
  try {
    database = openLibraryDb();
    const ownerRow = database.prepare(`
      SELECT sender_id
      FROM messages
      WHERE sender_id IS NOT NULL AND sender_id != ''
      GROUP BY sender_id
      ORDER BY COUNT(*) DESC
      LIMIT 1
    `).get() as { sender_id?: string } | undefined;
    const ownerId = ownerRow?.sender_id || '';
    const rows = database.prepare(`
      SELECT id, sender_id, sender_name, sent_at, type, content
      FROM messages
      WHERE conversation_id = ? AND content IS NOT NULL AND length(trim(content)) > 0
      ORDER BY sent_at DESC
      LIMIT ?
    `).all(req.params.conversationId, limit) as Record<string, unknown>[];
    const items = rows.reverse().map(row => {
      const senderName = String(row.sender_name || '未知联系人');
      return {
        id: String(row.id),
        conversationId: req.params.conversationId,
        senderId: row.sender_id === ownerId ? 'user-joyboy' : String(row.sender_id || senderName),
        senderName: row.sender_id === ownerId ? CURRENT_USER.name : senderName,
        senderAvatar: row.sender_id === ownerId ? CURRENT_USER.avatar : avatarDataUri(senderName),
        content: String(row.content),
        timestamp: new Date(Number(row.sent_at || 0)).toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }),
        hasAttachment: row.type !== 'text',
      };
    });
    res.json({ source: 'sqlite-readonly', total: items.length, items });
  } catch (error) {
    res.status(503).json({
      error: 'local_conversation_messages_unavailable',
      message: error instanceof Error ? error.message : '无法读取会话消息',
    });
  } finally {
    database?.close();
  }
});

app.post('/api/local-model/thinking-analysis', async (req, res) => {
  const query = typeof req.body?.query === 'string' ? req.body.query.trim() : '';
  const contextJson = JSON.stringify(req.body?.contextData ?? {});
  if (!query || query.length > 4_000) {
    return res.status(400).json({ error: 'invalid_query', message: '请输入 1–4000 字的推演问题。' });
  }
  if (contextJson.length > 32_000) {
    return res.status(413).json({ error: 'context_too_large', message: '推演上下文超过 32,000 字符限制。' });
  }
  if (!localLlmIsLoopback) {
    return res.status(503).json({
      error: 'local_model_endpoint_blocked',
      message: 'Thinking Mode 仅允许调用本机 LM Studio，已阻止非本机模型端点。',
    });
  }

  try {
    const modelsResponse = await fetch(`${LOCAL_LLM_BASE_URL}/models`, {
      signal: AbortSignal.timeout(3_000),
    });
    if (!modelsResponse.ok) throw new Error(`模型列表请求失败 (${modelsResponse.status})`);
    const models = await modelsResponse.json() as { data?: Array<{ id?: string }> };
    if (!models.data?.some(model => model.id === LOCAL_LLM_MODEL)) {
      return res.status(503).json({
        error: 'local_model_not_loaded',
        message: `LM Studio 未加载指定对话模型：${LOCAL_LLM_MODEL}`,
      });
    }

    const completionResponse = await fetch(`${LOCAL_LLM_BASE_URL}/chat/completions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      signal: AbortSignal.timeout(10_000),
      body: JSON.stringify({
        model: LOCAL_LLM_MODEL,
        stream: false,
        temperature: 0.2,
        max_tokens: 600,
        messages: [
          {
            role: 'system',
            content: '你是 ChatOS 的本地决策分析助手。只能依据提供的结构化上下文作答，不得虚构人物、期限或事件；证据不足时明确标注。用简洁中文输出“结论、依据、行动建议”三部分，不展示隐藏思维链。',
          },
          {
            role: 'user',
            content: `推演请求：${query}\n\n结构化上下文：${contextJson}`,
          },
        ],
      }),
    });
    if (!completionResponse.ok) {
      const detail = (await completionResponse.text()).slice(0, 200);
      throw new Error(`本地推演请求失败 (${completionResponse.status})${detail ? `：${detail}` : ''}`);
    }
    const completion = await completionResponse.json() as {
      model?: string;
      choices?: Array<{ message?: { content?: string } }>;
    };
    const analysis = completion.choices?.[0]?.message?.content?.trim();
    if (!analysis) throw new Error('本地模型未返回可显示的推演结论');

    return res.json({
      provider: 'lm-studio-local',
      modelUsed: completion.model || LOCAL_LLM_MODEL,
      thinkingMode: 'LOCAL',
      thoughtSummary: '仅使用当前界面提交的结构化会话、待办与项目摘要；未向外部模型发送数据。',
      analysis,
    });
  } catch (error) {
    const isTimeout = error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name);
    return res.status(isTimeout ? 504 : 503).json({
      error: isTimeout ? 'local_model_timeout' : 'local_model_unavailable',
      message: isTimeout
        ? '本地模型在 10 秒内未完成推演，请稍后重试。'
        : error instanceof Error ? error.message : '本地模型暂不可用。',
    });
  }
});

// ==========================================
// COLLABORATIVE APIS: Shared Lists & Tasks
// ==========================================

// 1. Get Team Members
app.get('/api/collaborative/team-members', (req, res) => {
  res.json(AVAILABLE_TEAM_MEMBERS);
});

// 2. Get All Shared To-Do Lists
app.get('/api/collaborative/shared-lists', (req, res) => {
  res.json(sharedListsState);
});

// 3. Create Shared To-Do List
app.post('/api/collaborative/shared-lists', (req, res) => {
  const { title, description, collaboratorIds, associatedProjectId, createdBy } = req.body;
  if (!title) {
    return res.status(400).json({ error: 'Title is required' });
  }

  const selectedCollaborators = AVAILABLE_TEAM_MEMBERS.filter(m =>
    collaboratorIds?.includes(m.id)
  );
  // Ensure creator is included
  const author = createdBy || CURRENT_USER;
  if (!selectedCollaborators.some(c => c.id === author.id)) {
    selectedCollaborators.unshift(author);
  }

  const newList: SharedTodoList = {
    id: `shared-list-${Date.now()}`,
    title,
    description: description || '',
    createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
    createdBy: author,
    associatedProjectId,
    collaborators: selectedCollaborators,
    tasks: [],
  };

  sharedListsState.unshift(newList);
  res.status(201).json(newList);
});

// 4. Delete Shared To-Do List
app.delete('/api/collaborative/shared-lists/:listId', (req, res) => {
  const { listId } = req.params;
  sharedListsState = sharedListsState.filter(l => l.id !== listId);
  res.json({ success: true, message: 'Shared list deleted' });
});

// 5. Add Task to Shared List (with Multiple Assignees)
app.post('/api/collaborative/shared-lists/:listId/tasks', (req, res) => {
  const { listId } = req.params;
  const { title, description, assigneeIds, dueDate, priority, createdBy } = req.body;

  const targetList = sharedListsState.find(l => l.id === listId);
  if (!targetList) {
    return res.status(404).json({ error: 'Shared list not found' });
  }

  const assignees = AVAILABLE_TEAM_MEMBERS.filter(m => assigneeIds?.includes(m.id));

  const newTask: SharedTask = {
    id: `stask-${Date.now()}`,
    listId,
    title,
    description: description || '',
    isCompleted: false,
    assignees: assignees.length > 0 ? assignees : [CURRENT_USER],
    dueDate: dueDate || '2026-09-18 18:00',
    priority: priority || 'medium',
    createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
    createdBy: createdBy || CURRENT_USER,
  };

  targetList.tasks.push(newTask);
  res.status(201).json(newTask);
});

// 6. Update Task (Toggle Completion, Reassign, etc.)
app.patch('/api/collaborative/shared-lists/:listId/tasks/:taskId', (req, res) => {
  const { listId, taskId } = req.params;
  const updates = req.body;

  const targetList = sharedListsState.find(l => l.id === listId);
  if (!targetList) {
    return res.status(404).json({ error: 'Shared list not found' });
  }

  const targetTask = targetList.tasks.find(t => t.id === taskId);
  if (!targetTask) {
    return res.status(404).json({ error: 'Task not found' });
  }

  // Handle completion state change
  if (typeof updates.isCompleted === 'boolean') {
    targetTask.isCompleted = updates.isCompleted;
    if (updates.isCompleted) {
      targetTask.completedAt = new Date().toISOString().replace('T', ' ').slice(0, 16);
      targetTask.completedBy = updates.completedBy || CURRENT_USER;
    } else {
      delete targetTask.completedAt;
      delete targetTask.completedBy;
    }
  }

  // Handle assignee updates
  if (updates.assigneeIds && Array.isArray(updates.assigneeIds)) {
    targetTask.assignees = AVAILABLE_TEAM_MEMBERS.filter(m =>
      updates.assigneeIds.includes(m.id)
    );
  }

  if (updates.title) targetTask.title = updates.title;
  if (updates.description !== undefined) targetTask.description = updates.description;
  if (updates.dueDate) targetTask.dueDate = updates.dueDate;
  if (updates.priority) targetTask.priority = updates.priority;

  res.json(targetTask);
});

// 7. Delete Task
app.delete('/api/collaborative/shared-lists/:listId/tasks/:taskId', (req, res) => {
  const { listId, taskId } = req.params;
  const targetList = sharedListsState.find(l => l.id === listId);
  if (!targetList) {
    return res.status(404).json({ error: 'Shared list not found' });
  }

  targetList.tasks = targetList.tasks.filter(t => t.id !== taskId);
  res.json({ success: true, message: 'Task deleted' });
});

// ==========================================
// COLLABORATIVE APIS: Project Comments
// ==========================================

// 8. Get Comments for a Project
app.get('/api/collaborative/projects/:projectId/comments', (req, res) => {
  const { projectId } = req.params;
  const comments = projectCommentsState[projectId] || [];
  res.json(comments);
});

// 9. Add Comment to a Project
app.post('/api/collaborative/projects/:projectId/comments', (req, res) => {
  const { projectId } = req.params;
  const { content, author, isPinned } = req.body;

  if (!content || !content.trim()) {
    return res.status(400).json({ error: 'Content cannot be empty' });
  }

  if (!projectCommentsState[projectId]) {
    projectCommentsState[projectId] = [];
  }

  const newComment: ProjectComment = {
    id: `pcomm-${Date.now()}`,
    projectId,
    author: author || CURRENT_USER,
    content: content.trim(),
    timestamp: '刚刚',
    reactions: [],
    isPinned: Boolean(isPinned),
  };

  projectCommentsState[projectId].unshift(newComment);
  res.status(201).json(newComment);
});

// 10. Delete Comment
app.delete('/api/collaborative/projects/:projectId/comments/:commentId', (req, res) => {
  const { projectId, commentId } = req.params;
  if (projectCommentsState[projectId]) {
    projectCommentsState[projectId] = projectCommentsState[projectId].filter(
      c => c.id !== commentId
    );
  }
  res.json({ success: true, message: 'Comment deleted' });
});

// 11. Toggle Emoji Reaction on Comment
app.post(
  '/api/collaborative/projects/:projectId/comments/:commentId/reactions',
  (req, res) => {
    const { projectId, commentId } = req.params;
    const { emoji, userId } = req.body;
    const uId = userId || CURRENT_USER.id;

    const comments = projectCommentsState[projectId] || [];
    const comment = comments.find(c => c.id === commentId);
    if (!comment) {
      return res.status(404).json({ error: 'Comment not found' });
    }

    if (!comment.reactions) {
      comment.reactions = [];
    }

    const reactionIndex = comment.reactions.findIndex(r => r.emoji === emoji);
    if (reactionIndex > -1) {
      const existing = comment.reactions[reactionIndex];
      if (existing.users.includes(uId)) {
        // Remove reaction
        existing.users = existing.users.filter(u => u !== uId);
        existing.count = existing.users.length;
        if (existing.count === 0) {
          comment.reactions.splice(reactionIndex, 1);
        }
      } else {
        // Add user to reaction
        existing.users.push(uId);
        existing.count = existing.users.length;
      }
    } else {
      comment.reactions.push({
        emoji,
        count: 1,
        users: [uId],
      });
    }

    res.json(comment);
  }
);

async function startServer() {
  if (!isProduction) {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(consoleRoot, 'dist');
    app.use('/api', (_req, res) => { res.status(404).json({ error: 'not_implemented_in_ui_adapter' }); });
    app.use(['/server.cjs', '/server.cjs.map'], (_req, res) => { res.sendStatus(404); });
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '127.0.0.1', () => {
    console.log(`ChatOS Server running on http://127.0.0.1:${PORT}`);
  });
}

startServer();
