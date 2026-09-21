import express from 'express';
import fs from 'node:fs';
import path from 'path';
import { DatabaseSync } from 'node:sqlite';
import { createServer as createViteServer } from 'vite';
import dotenv from 'dotenv';
import { captureUiRuntime, resolveLibraryDb, UI_URL, GATEWAY_URL } from '../../scripts/entry-contract.mjs';
import { proxyGateway } from './src/server/gateway-proxy.ts';
import {
  INITIAL_SHARED_LISTS,
  INITIAL_PROJECT_COMMENTS,
  AVAILABLE_TEAM_MEMBERS,
  CURRENT_USER,
} from './src/mockData';
import { SharedTodoList, SharedTask, ProjectComment, User, mapGatewayStatus } from './src/types';

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
const LOCAL_LLM_BASE_URL = (process.env.LOCAL_LLM_BASE_URL || 'http://127.0.0.1:8080/v1').replace(/\/+$/, '');
const LOCAL_LLM_MODEL = process.env.LOCAL_LLM_MODEL || 'muse-glimmer-30b';
const localLlmHostname = new URL(LOCAL_LLM_BASE_URL).hostname;
const localLlmIsLoopback = ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(localLlmHostname);

const openLibraryDb = () => new DatabaseSync(libraryDbPath, { readOnly: true });

type WechatAvatarSource = {
  databasePath: string;
  revision: string;
  urls: Map<string, string>;
};

const decryptedSourceDir = path.join(repositoryRoot, 'apps', 'gateway', 'decrypted');
const wechatAvatarHostSuffixes = ['.wechat.com', '.qlogo.cn', '.qpic.cn', '.weixin.qq.com'];
let cachedWechatAvatarSource: WechatAvatarSource | null = null;

const normalizeWechatAvatarUrl = (value: unknown) => {
  if (typeof value !== 'string' || value.length === 0) return null;
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || !wechatAvatarHostSuffixes.some(suffix => hostname.endsWith(suffix))) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
};

const findWechatContactDatabase = () => {
  if (!fs.existsSync(decryptedSourceDir)) return null;
  for (const file of fs.readdirSync(decryptedSourceDir).filter(name => name.endsWith('.db')).sort()) {
    const databasePath = path.join(decryptedSourceDir, file);
    let database: DatabaseSync | null = null;
    try {
      database = new DatabaseSync(databasePath, { readOnly: true });
      const columns = new Set(
        (database.prepare('PRAGMA table_info(contact)').all() as Array<{ name: string }>).map(column => column.name)
      );
      if (columns.has('username') && columns.has('small_head_url') && columns.has('big_head_url')) {
        return databasePath;
      }
    } catch {
      // A decrypted shard can be replaced while sources refresh; try the next one.
    } finally {
      database?.close();
    }
  }
  return null;
};

const readWechatAvatarSource = (): WechatAvatarSource => {
  try {
    const databasePath = cachedWechatAvatarSource?.databasePath && fs.existsSync(cachedWechatAvatarSource.databasePath)
      ? cachedWechatAvatarSource.databasePath
      : findWechatContactDatabase();
    if (!databasePath) throw new Error('wechat contact database unavailable');
    const revision = String(fs.statSync(databasePath).mtimeMs);
    if (cachedWechatAvatarSource?.databasePath === databasePath && cachedWechatAvatarSource.revision === revision) {
      return cachedWechatAvatarSource;
    }

    const database = new DatabaseSync(databasePath, { readOnly: true });
    try {
      const urls = new Map<string, string>();
      const rows = database.prepare(`
        SELECT username,
               COALESCE(NULLIF(small_head_url, ''), NULLIF(big_head_url, '')) AS avatar_url
        FROM contact
        WHERE COALESCE(small_head_url, big_head_url, '') != ''
      `).all() as Array<{ username: string; avatar_url: string }>;
      for (const row of rows) {
        const url = normalizeWechatAvatarUrl(row.avatar_url);
        if (row.username && url) urls.set(String(row.username), url);
      }
      cachedWechatAvatarSource = { databasePath, revision, urls };
      return cachedWechatAvatarSource;
    } finally {
      database.close();
    }
  } catch {
    return cachedWechatAvatarSource || { databasePath: '', revision: 'unavailable', urls: new Map() };
  }
};

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

const domainLabel = (domain: string | null, classificationStatus: string) => classificationStatus === 'needs_review'
  ? '关系待判断'
  : ({
  project: '项目协作',
  work: '工作协作',
  personal: '亲友关系',
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

const categoryFromDatabase = (domain: string | null, isGroup: boolean, indexingEnabled: boolean, classificationStatus: string) => {
  if (!indexingEnabled && isGroup) return 'noisy_group';
  if (classificationStatus === 'needs_review') return 'general';
  if (domain === 'project' || domain === 'work') return 'active_collab';
  if (domain === 'personal') return 'family_core';
  if (domain === 'service') return 'low_freq_important';
  if (domain === 'subscription') return 'noisy_group';
  return 'general';
};

const readConversationSnapshot = () => {
  const database = openLibraryDb();
  try {
    const avatarSource = readWechatAvatarSource();
    // Deliberately do NOT identify "the owner" as the global most active
    // sender — that heuristic is wrong in shared group chats. Identity is
    // shown only from real sender_name/sender_id columns.
    const cutoff = Date.now() - 90 * 86_400_000;

    // Effective per-conversation state = conversation_overrides COALESCEd over
    // the raw conversations columns.  Human PATCH (favorite/indexing/domain)
    // and automatic classification both live in the derived table; this read
    // path must honor them (matches the gateway's unified predicate).
    const rows = database.prepare(`
      SELECT c.id, c.title, c.kind, c.message_count, c.last_message_at,
             COALESCE(o.is_favorite, c.is_favorite, 0) AS is_favorite,
             COALESCE(o.is_archived, c.is_archived, 0) AS is_archived,
             COALESCE(o.is_indexing_enabled, c.is_indexing_enabled, 1) AS is_indexing_enabled,
             COALESCE(o.domain, c.domain) AS domain,
             COALESCE(o.project_id, c.project_id) AS project_id,
             COALESCE(o.project_role, c.project_role) AS project_role,
             COALESCE(o.classification_status, c.classification_status, 'needs_review') AS classification_status,
             o.group_id AS group_id,
             COALESCE(o.updated_at, 0) AS override_updated_at
      FROM conversations c
      LEFT JOIN conversation_overrides o ON o.conversation_id = c.id
      WHERE c.kind IN ('direct', 'group') AND c.message_count > 0
      ORDER BY c.last_message_at DESC
    `).all() as Record<string, unknown>[];

    const recentStats = new Map(
      (database.prepare(`
        SELECT conversation_id,
               COUNT(DISTINCT CAST(sent_at / 86400000 AS INTEGER)) AS interaction_days,
               COUNT(*) AS recent_messages
        FROM messages
        WHERE sent_at >= ?
        GROUP BY conversation_id
      `).all(cutoff) as Record<string, unknown>[])
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
          SELECT task_id, conversation_id, content, sender_name, sent_at, due_date, priority, status,
                 source_message_id,
                 ROW_NUMBER() OVER (
                   PARTITION BY conversation_id
                   ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, sent_at DESC
                 ) AS rank
          FROM tasks
          WHERE status IN ('todo', 'pending', 'in_progress')
        )
        SELECT task_id, conversation_id, content, sender_name, sent_at, due_date, priority, status, source_message_id
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
      const senderId = String(row.sender_id || name);
      const current = participants.get(conversationId) || [];
      current.push({ id: senderId, name, avatar: avatarSource.urls.get(senderId) || avatarDataUri(name) });
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
      const classificationStatus = String(row.classification_status || 'needs_review');
      const stats = recentStats.get(id);
      const interactionDays90d = Number(stats?.interaction_days || 0);
      const recentMessages = Number(stats?.recent_messages || 0);
      // No reliable "self" identity is derived from message volume; report 0
      // rather than falsely labeling the most active sender as the user.
      const participation = 0;
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
        avatar: avatarSource.urls.get(id) || avatarDataUri(name),
        groupParticipants: isGroup ? participants.get(id) || [] : undefined,
        memberCount: isGroup ? memberCounts.get(id) || 2 : undefined,
        roleTitle: isGroup ? undefined : String(row.project_role || domainLabel(domain, classificationStatus)),
        groupTopic: isGroup ? groupTopicLabel(domain) : undefined,
        isFollowed: favorite,
        isArchived: Boolean(row.is_archived),
        groupId: row.group_id ? String(row.group_id) : null,
        interactionDays90d,
        joyBoyParticipationScore: participation,
        lastActiveTime: lastMessageAt > 0 ? formatRelativeTime(lastMessageAt) : '暂无消息时间',
        twoLineSummary: latestContent || `${messageCount} 条历史消息`,
        unreadCount: 0,
        topActionTodo: todo ? {
          id: String(todo.task_id),
          title: String(todo.content),
          status: mapGatewayStatus(String(todo.status)).status,
          assigneeRole: 'other',
          dueDate: String(todo.due_date || '待确认'),
          isUrgent: todo.priority === 'high',
          sourceMessageId: todo.source_message_id != null ? String(todo.source_message_id) : '',
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
        categoryTag: categoryFromDatabase(domain, isGroup, indexingEnabled, classificationStatus),
        linkedProjectIds: row.project_id ? [String(row.project_id)] : [],
      };
    });

    const taskRevision = database.prepare('SELECT COALESCE(MAX(updated_at), 0) AS updated_at FROM tasks').get() as { updated_at?: number };
    // Override timestamp is part of the revision so that enabling/disabling a
    // conversation, favoriting it, or classifying it invalidates the cached
    // snapshot on the next poll (derived-state changes must reach the UI).
    // conversation_overrides.updated_at is an ISO8601 TEXT, not an epoch int.
    const overrideRevision = database.prepare('SELECT MAX(updated_at) AS updated_at FROM conversation_overrides').get() as { updated_at?: string | null };
    const newestMessage = Math.max(0, ...items.map(item => Number(rows.find(row => String(row.id) === item.id)?.last_message_at || 0)));
    return {
      source: 'sqlite-readonly',
      total: items.length,
      revision: `${newestMessage}:${Number(taskRevision?.updated_at || 0)}:${overrideRevision?.updated_at ?? 'none'}:${avatarSource.revision}:${items.length}`,
      updatedAt: new Date().toISOString(),
      items,
    };
  } finally {
    database.close();
  }
};

// ==========================================
// Security hardening (task 5) — loopback-only, no loose CORS.
// ==========================================
const UI_HOST_PORT = new URL(UI_URL).port;
const ALLOWED_HOSTS = new Set([
  `127.0.0.1:${UI_HOST_PORT}`,
  `localhost:${UI_HOST_PORT}`,
  `[::1]:${UI_HOST_PORT}`,
  '127.0.0.1:3109',
  'localhost:3109',
]);

// 1) Host allowlist: reject anything that is not the loopback UI origin.
app.use((req, res, next) => {
  const host = String(req.headers.host || '').toLowerCase();
  if (!host || !ALLOWED_HOSTS.has(host)) {
    res.status(403).json({ error: 'invalid_host', message: 'Host 不被允许。' });
    return;
  }
  next();
});

// 2) Baseline security response headers.
app.use((_req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  next();
});

// 3) Same-origin enforcement for mutating requests.  No Access-Control-Allow-*
//    headers are ever set, so the browser blocks cross-origin writes by default;
//    we additionally reject a non-loopback Origin on POST/PATCH/PUT/DELETE.
const ALLOWED_ORIGINS = new Set([
  `http://127.0.0.1:${UI_HOST_PORT}`,
  `http://localhost:${UI_HOST_PORT}`,
  'http://127.0.0.1:3109',
  'http://localhost:3109',
]);
app.use((req, res, next) => {
  if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(req.method || '')) {
    const origin = String(req.headers.origin || '').toLowerCase();
    // Exact-match allowed loopback origins only.  A generic startsWith would
    // accept any localhost port (e.g. a random local dev server), which is
    // not the same-origin UI and must be rejected.
    if (origin && !ALLOWED_ORIGINS.has(origin)) {
      res.status(403).json({ error: 'cross_origin_blocked', message: '跨域修改请求已拒绝。' });
      return;
    }
  }
  next();
});

app.use(express.json());

// Sensitive local-data endpoints must never be cached.
app.use('/api', (_req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  next();
});

// ==========================================
// Thin reverse proxy to the canonical gateway (8912).  The Express adapter
// never re-implements business rules: it forwards /api/gateway/<path> to
// <GATEWAY_URL>/api/<path> and relays the upstream status/body verbatim.
// Used for organize-incremental, jobs polling, tasks PATCH, search,
// projects and graph.  No CORS headers are set; this is same-origin.
// ==========================================
const GATEWAY_BASE = GATEWAY_URL.replace(/\/+$/, '');
app.use('/api/gateway', (req, res) => proxyGateway(req, res, GATEWAY_BASE));
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
  const targetMessageId = req.query.targetMessageId !== undefined
    ? Number(req.query.targetMessageId)
    : NaN;
  let database: DatabaseSync | null = null;
  try {
    database = openLibraryDb();
    let rows: Record<string, unknown>[];
    let anchored = false;

    if (Number.isFinite(targetMessageId) && targetMessageId > 0) {
      // Locate the anchor source message anywhere in the conversation history,
      // not just within the most-recent window.  Return a window ending at the
      // anchor so the UI can jump to a todo's originating message even when it
      // is older than the last 100 loaded rows.
      const anchor = database.prepare(`
        SELECT id, sent_at FROM messages
        WHERE conversation_id = ? AND id = ?
      `).get(req.params.conversationId, targetMessageId) as { id: number; sent_at: number } | undefined;
      if (!anchor) {
        return res.status(404).json({
          error: 'message_not_found',
          message: '未找到指定来源消息，可能已超出当前加载范围。',
        });
      }
      rows = (database.prepare(`
        SELECT id, sender_id, sender_name, sent_at, type, content
        FROM messages
        WHERE conversation_id = ? AND content IS NOT NULL AND length(trim(content)) > 0
          AND (sent_at < ? OR (sent_at = ? AND id <= ?))
        ORDER BY sent_at DESC, id DESC
        LIMIT ?
      `).all(req.params.conversationId, anchor.sent_at, anchor.sent_at, targetMessageId, limit) as Record<string, unknown>[])
        .reverse();
      anchored = true;
    } else {
      rows = (database.prepare(`
        SELECT id, sender_id, sender_name, sent_at, type, content
        FROM messages
        WHERE conversation_id = ? AND content IS NOT NULL AND length(trim(content)) > 0
        ORDER BY sent_at DESC, id DESC
        LIMIT ?
      `).all(req.params.conversationId, limit) as Record<string, unknown>[]).reverse();
    }

    const avatarSource = readWechatAvatarSource();
    const items = rows.map(row => {
      const senderName = String(row.sender_name || '未知联系人');
      const senderId = row.sender_id != null && String(row.sender_id) !== ''
        ? String(row.sender_id)
        : senderName;
      return {
        id: String(row.id),
        conversationId: req.params.conversationId,
        senderId,
        senderName,
        senderAvatar: avatarSource.urls.get(senderId) || avatarDataUri(senderName),
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
    res.json({
      source: 'sqlite-readonly',
      total: items.length,
      items,
      anchored,
      anchorMessageId: anchored ? String(targetMessageId) : undefined,
    });
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
      signal: AbortSignal.timeout(45_000),   // 2026-09-21: 30B 是推理模型，先烧 reasoning；
                                            // 原 10s 配 600 token 会在长上下文下被 abort 或返回空 content
      body: JSON.stringify({
        model: LOCAL_LLM_MODEL,
        stream: false,
        temperature: 0.2,
        max_tokens: 2048,
        reasoning_effort: 'low',
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
// DEMO / IN-MEMORY STATE: these endpoints operate on process-local state
// (sharedListsState) that resets on server restart.  They are NOT persisted
// to SQLite and must not be presented as durable collaboration.  The
// X-ChatOS-Mode header makes the demo status explicit to the client and
// to any network/inspection tooling.
app.use('/api/collaborative', (_req, res, next) => {
  res.setHeader('X-ChatOS-Mode', 'in-memory-demo');
  next();
});

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
