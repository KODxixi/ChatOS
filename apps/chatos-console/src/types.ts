export type WorkspaceType = 'conversations' | 'todos' | 'projects' | 'library' | 'mindmap';

export type MindMapNodeType = 'conversation' | 'project' | 'todo' | 'knowledge' | 'person' | 'topic' | 'date';

export interface MindMapBiLink {
  id: string;
  source: string;
  target: string;
  label?: string;
  relationType: 'references' | 'produces' | 'belongs_to' | 'assignee' | 'timeline' | 'custom' | 'evolution' | 'milestone';
  createdAt: string;
  strength?: number;
  snippet?: string;
  direction?: 'forward' | 'backward' | 'bidirectional';
  evolutionFlow?: string;
}

export interface MindMapNode {
  id: string;
  label: string;
  type: MindMapNodeType;
  subType?: string;
  timestamp: string;
  dateKey: string;
  color: string;
  radius: number;
  entityId?: string;
  details?: string;
  badge?: string;
  tags?: string[];
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
  active?: boolean;
  clusterId?: string;
  affinityScore?: number;
  connectedCount?: number;
  isDateAnchor?: boolean;
}

export interface User {
  id: string;
  name: string;
  avatar: string;
  role: string;
  isSelf?: boolean;
}

export interface ChatMessage {
  id: string;
  conversationId: string;
  senderId: string;
  senderName: string;
  senderAvatar: string;
  content: string;
  timestamp: string;
  hasAttachment?: boolean;
  attachmentName?: string;
  attachmentType?: 'image' | 'pdf' | 'doc' | 'archive';
  attachmentSize?: string;
  attachmentUrl?: string;
  isActionTrigger?: boolean;
  associatedProjectId?: string;
}

export type TodoStatus = 'draft' | 'pending' | 'in_progress' | 'completed';
export type TodoRole = 'i_am_responsible' | 'i_promised' | 'i_need_follow_up' | 'other';

/**
 * Round-trip status mapping between the gateway `tasks.status` column and the
 * frontend kanban `TodoStatus`.  These tiny pure functions are imported by BOTH
 * the initial real-todo load and the post-organize reload (so they can never
 * drift apart), and by the console verify script — the production mapping is
 * exercised directly, never re-copied.
 *
 * Accepted gateway raw statuses:
 *   todo | pending | in_progress | draft | completed | done | ignored
 * - done / completed            -> frontend completed
 * - draft / in_progress          -> preserved 1:1 (a user-chosen state survives reload)
 * - todo / pending (and unknown) -> frontend pending
 * - ignored                     -> surfaced via the `isIgnored` flag (ignored is NOT a
 *                                  kanban column; the row's in-flight status defaults to
 *                                  pending but the flag keeps it out of the normal view).
 */
export function mapGatewayStatus(gatewayStatus: string): { status: TodoStatus; isIgnored: boolean } {
  const raw = gatewayStatus || 'todo';
  const isIgnored = raw === 'ignored';
  let status: TodoStatus;
  switch (raw) {
    case 'completed':
    case 'done':
      status = 'completed';
      break;
    case 'draft':
      status = 'draft';
      break;
    case 'in_progress':
      status = 'in_progress';
      break;
    case 'pending':
    case 'todo':
    default:
      status = 'pending';
      break;
  }
  return { status, isIgnored };
}

/**
 * Reverse mapping used when persisting a kanban change.  Each frontend state
 * maps to a gateway status that maps back to the SAME frontend state, so
 * draft / pending / in_progress / completed survive save + reload round-trips.
 * (The old code collapsed every non-completed state to 'todo', which on reload
 * became 'pending' and silently lost draft/in_progress.)
 */
export function mapTodoStatusToGateway(status: TodoStatus): string {
  switch (status) {
    case 'completed':
      return 'completed';
    case 'draft':
      return 'draft';
    case 'in_progress':
      return 'in_progress';
    case 'pending':
      return 'pending';
  }
}

/**
 * Terminal outcomes of an incremental-organize job.  Once the job reaches one
 * of these, polling MUST stop immediately.  queued / running / retrying (and any
 * non-terminal value) keep polling — they are NOT done yet.
 */
export const ORGANIZE_TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'degraded',
  'failed',
  'cancelled',
  'interrupted',
]);

export function isOrganizeTerminalStatus(status: string): boolean {
  return ORGANIZE_TERMINAL_STATUSES.has(status);
}

/**
 * Honest post-poll outcome: engine status + toast message.
 *
 * A job that exhausts all polls WITHOUT reaching a terminal status is a TIMEOUT.
 * It must NOT fall back to idle (that would fake success).  failed / cancelled /
 * interrupted / timeout all map to `error_fallback` with a message that names
 * the actual outcome instead of pretending it finished.
 */
export function summarizeOrganizeOutcome(
  finalStatus: string,
  addedCount: number,
): { engineStatus: 'idle' | 'error_fallback'; message: string; timedOut: boolean } {
  if (!isOrganizeTerminalStatus(finalStatus)) {
    return {
      engineStatus: 'error_fallback',
      timedOut: true,
      message: `增量整理超时：轮询已耗尽仍未完成（当前状态：${finalStatus || 'unknown'}），未伪装成功`,
    };
  }
  switch (finalStatus) {
    case 'completed':
      return { engineStatus: 'idle', timedOut: false, message: `增量整理完成：处理 ${addedCount} 项` };
    case 'degraded':
      return { engineStatus: 'idle', timedOut: false, message: `增量整理完成（部分降级）：${addedCount} 项，详情见日志` };
    case 'failed':
      return { engineStatus: 'error_fallback', timedOut: false, message: `增量整理失败：${finalStatus}` };
    case 'cancelled':
      return { engineStatus: 'error_fallback', timedOut: false, message: `增量整理已被取消：${finalStatus}` };
    case 'interrupted':
    default:
      return { engineStatus: 'error_fallback', timedOut: false, message: `增量整理被中断：${finalStatus}` };
  }
}

export interface TodoItem {
  id: string;
  title: string;
  description?: string;
  status: TodoStatus;
  assigneeRole: TodoRole;
  dueDate: string;
  isOverdue?: boolean;
  isUrgent?: boolean;
  sourceMessageId: string;
  sourceConversationId: string;
  sourceConversationName: string;
  sourceSnippet: string;
  evidenceText: string;
  isIgnored: boolean;
  manualModified: boolean;
  createdAt: string;
  projectId?: string;
  confidence: 'clear' | 'draft_review';
}

export type IntakeStatus = 'pending_confirmation' | 'pending_fetch' | 'processing' | 'stored' | 'failed';

export interface KnowledgeItem {
  id: string;
  title: string;
  fileName: string;
  fileType: 'image' | 'pdf' | 'doc' | 'code' | 'archive';
  fileSize: string;
  thumbnailUrl?: string;
  contentHash: string;
  intakeStatus: IntakeStatus;
  sourceConversationId: string;
  sourceConversationName: string;
  sourceMessageId: string;
  linkedProjectId?: string;
  createdAt: string;
  retryCount?: number;
  failureReason?: string;
  isDuplicateSample?: boolean;
}

export interface Conversation {
  id: string;
  name: string;
  isGroup: boolean;
  avatar: string;
  groupParticipants?: { id: string; name: string; avatar: string }[];
  memberCount?: number;
  roleTitle?: string;
  groupTopic?: string;
  isFollowed: boolean;
  interactionDays90d: number;
  joyBoyParticipationScore: number; // 0-100%
  lastActiveTime: string;
  twoLineSummary: string;
  unreadCount: number; // ChatOS 新消息本地水位
  topActionTodo?: TodoItem;
  relatedFilesPreview?: KnowledgeItem[];
  priorityScore: number;
  priorityReason: string;
  categoryTag: 'active_collab' | 'family_core' | 'low_freq_important' | 'noisy_group' | 'general';
  linkedProjectIds: string[];
}

export interface Project {
  id: string;
  name: string;
  code: string;
  oneLineProgress: string;
  currentAction: string;
  linkedConversationIds: string[];
  completionPercentage: number;
  status: 'active' | 'archived';
  isAutoGenerated: boolean;
  evidenceCount: number;
  lastUpdated: string;
  color: string;
  commentsCount?: number;
}

export interface ProjectComment {
  id: string;
  projectId: string;
  author: User;
  content: string;
  timestamp: string;
  reactions?: { emoji: string; count: number; users: string[] }[];
  isPinned?: boolean;
}

export interface SharedTask {
  id: string;
  listId: string;
  title: string;
  description?: string;
  isCompleted: boolean;
  completedAt?: string;
  completedBy?: User;
  assignees: User[];
  dueDate: string;
  priority: 'high' | 'medium' | 'low';
  createdAt: string;
  createdBy: User;
}

export interface SharedTodoList {
  id: string;
  title: string;
  description: string;
  createdAt: string;
  createdBy: User;
  collaborators: User[];
  associatedProjectId?: string;
  tasks: SharedTask[];
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  action: string;
  type: 'auto_extract' | 'manual_override' | 'dedup' | 'sync' | 'rollback';
  details: string;
  actor: 'JoyBoy' | 'ChatOS Engine (Local)' | 'LM Studio Local' | string;
}

export interface SystemEngineState {
  status: 'idle' | 'syncing' | 'analyzing' | 'error_fallback';
  lastRunTimestamp: string;
  processedDays: number;
  stats: {
    totalConversations: number;
    activeTodos: number;
    storedFiles: number;
    dedupedCount: number;
  };
}
