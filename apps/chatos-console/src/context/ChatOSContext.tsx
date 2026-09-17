import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  WorkspaceType,
  Conversation,
  TodoItem,
  Project,
  KnowledgeItem,
  ChatMessage,
  AuditLogEntry,
  SystemEngineState,
  TodoStatus,
  User,
  SharedTodoList,
  SharedTask,
  ProjectComment,
  MindMapBiLink,
} from '../types';
import {
  CURRENT_USER,
  AVAILABLE_TEAM_MEMBERS,
  INITIAL_CONVERSATIONS,
  INITIAL_TODOS,
  INITIAL_PROJECTS,
  INITIAL_KNOWLEDGE_ITEMS,
  INITIAL_MESSAGES,
  INITIAL_AUDIT_LOGS,
  INITIAL_SHARED_LISTS,
  INITIAL_PROJECT_COMMENTS,
  generateScaleEnterpriseDirectory,
} from '../mockData';

interface ChatOSContextType {
  // Navigation & Workspace
  activeWorkspace: WorkspaceType;
  setActiveWorkspace: (w: WorkspaceType) => void;
  isSidebarCollapsed: boolean;
  setIsSidebarCollapsed: (v: boolean) => void;

  // Selected Entities & Inspector Panel
  selectedConversation: Conversation | null;
  setSelectedConversation: (c: Conversation | null) => void;
  selectedProject: Project | null;
  setSelectedProject: (p: Project | null) => void;
  selectedTodo: TodoItem | null;
  setSelectedTodo: (t: TodoItem | null) => void;
  selectedKnowledgeItem: KnowledgeItem | null;
  setSelectedKnowledgeItem: (k: KnowledgeItem | null) => void;
  highlightedMessageId: string | null;
  setHighlightedMessageId: (id: string | null) => void;
  isInspectorOpen: boolean;
  setIsInspectorOpen: (open: boolean) => void;

  // Entities state
  conversations: Conversation[];
  conversationDataSource: 'loading' | 'database' | 'prototype';
  conversationDataUpdatedAt: string | null;
  conversationTotal: number;
  todos: TodoItem[];
  projects: Project[];
  knowledgeItems: KnowledgeItem[];
  messages: Record<string, ChatMessage[]>;
  auditLogs: AuditLogEntry[];
  engineState: SystemEngineState;

  // Collaborative Shared To-Do Lists
  sharedLists: SharedTodoList[];
  activeSharedListId: string | null;
  setActiveSharedListId: (id: string | null) => void;
  teamMembers: User[];
  createSharedTodoList: (
    title: string,
    description: string,
    collaboratorIds: string[],
    associatedProjectId?: string
  ) => Promise<SharedTodoList | null>;
  deleteSharedTodoList: (listId: string) => Promise<void>;
  addSharedTask: (
    listId: string,
    taskData: {
      title: string;
      description?: string;
      assigneeIds: string[];
      dueDate: string;
      priority: 'high' | 'medium' | 'low';
    }
  ) => Promise<SharedTask | null>;
  toggleCompleteSharedTask: (
    listId: string,
    taskId: string,
    completedByUserId?: string
  ) => Promise<boolean>;
  deleteSharedTask: (listId: string, taskId: string) => Promise<void>;
  updateSharedTask: (
    listId: string,
    taskId: string,
    updates: Partial<SharedTask>
  ) => Promise<void>;

  // Collaborative Project Comments & Discussions
  projectComments: Record<string, ProjectComment[]>;
  activeDiscussingProjectId: string | null;
  setActiveDiscussingProjectId: (id: string | null) => void;
  addProjectComment: (
    projectId: string,
    content: string,
    asUserId?: string
  ) => Promise<ProjectComment | null>;
  deleteProjectComment: (projectId: string, commentId: string) => Promise<void>;
  toggleCommentReaction: (
    projectId: string,
    commentId: string,
    emoji: string,
    asUserId?: string
  ) => Promise<void>;

  // Mindmap & Bi-directional Links
  customBiLinks: MindMapBiLink[];
  addCustomBiLink: (
    sourceId: string,
    targetId: string,
    relationType: 'references' | 'produces' | 'belongs_to' | 'assignee' | 'timeline' | 'custom',
    label: string,
    snippet?: string
  ) => void;
  removeCustomBiLink: (linkId: string) => void;

  // Actions
  updateTodoStatus: (id: string, status: TodoStatus) => boolean;
  updateTodoDueDate: (id: string, newDate: string) => void;
  toggleIgnoreTodo: (id: string) => void;
  createTodo: (newTodo: Partial<TodoItem>) => void;
  deleteTodo: (id: string) => void;

  createProject: (name: string, currentAction: string, linkedConvIds: string[]) => void;
  renameProject: (id: string, newName: string) => void;
  archiveProject: (id: string) => void;
  linkConversationToProject: (projectId: string, convId: string) => void;
  unlinkConversationFromProject: (projectId: string, convId: string) => void;

  retryKnowledgeIntake: (id: string) => void;
  revokeKnowledgeItem: (id: string) => void;

  toggleFollowConversation: (id: string) => void;
  markConversationAsRead: (id: string) => void;

  // Global Engine Operations
  runIncrementalOrganize: () => Promise<{ addedCount: number; message: string }>;
  isSyncing: boolean;
  modelDiagnosticStatus: 'normal' | 'simulate_offline' | 'recovered';
  setModelDiagnosticStatus: (status: 'normal' | 'simulate_offline' | 'recovered') => void;

  // Jump to Source
  jumpToMessage: (conversationId: string, messageId: string) => void;

  // Command Palette / Search
  isCommandPaletteOpen: boolean;
  setIsCommandPaletteOpen: (open: boolean) => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;

  // Secondary Tools Modal
  activeAuxModal: 'audit' | 'graph' | 'engine' | null;
  setActiveAuxModal: (m: 'audit' | 'graph' | 'engine' | null) => void;

  // Notification Toast
  toastMessage: string | null;
  showToast: (msg: string) => void;

  // Enterprise Scale Simulation
  enterpriseScale: 'curated' | 'medium_120' | 'max_500';
  setEnterpriseScale: (scale: 'curated' | 'medium_120' | 'max_500') => void;
}

const ChatOSContext = createContext<ChatOSContextType | undefined>(undefined);

export const ChatOSProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceType>('conversations');
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() =>
    window.matchMedia('(max-width: 767px)').matches
  );
  const [enterpriseScale, setEnterpriseScaleState] = useState<'curated' | 'medium_120' | 'max_500'>('curated');

  const [conversations, setConversations] = useState<Conversation[]>(INITIAL_CONVERSATIONS);
  const [conversationDataSource, setConversationDataSource] = useState<'loading' | 'database' | 'prototype'>('loading');
  const [conversationDataUpdatedAt, setConversationDataUpdatedAt] = useState<string | null>(null);
  const [conversationTotal, setConversationTotal] = useState<number>(INITIAL_CONVERSATIONS.length);
  const conversationRevisionRef = useRef<string | null>(null);
  const [todos, setTodos] = useState<TodoItem[]>(INITIAL_TODOS);
  const [projects, setProjects] = useState<Project[]>(INITIAL_PROJECTS);
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItem[]>(INITIAL_KNOWLEDGE_ITEMS);
  const [messages, setMessages] = useState<Record<string, ChatMessage[]>>(INITIAL_MESSAGES);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>(INITIAL_AUDIT_LOGS);

  const [selectedConversation, setSelectedConversation] = useState<Conversation | null>(INITIAL_CONVERSATIONS[0]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [selectedTodo, setSelectedTodo] = useState<TodoItem | null>(null);
  const [selectedKnowledgeItem, setSelectedKnowledgeItem] = useState<KnowledgeItem | null>(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null);
  const [isInspectorOpen, setIsInspectorOpen] = useState<boolean>(false);

  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [activeAuxModal, setActiveAuxModal] = useState<'audit' | 'graph' | 'engine' | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage(prev => (prev === msg ? null : prev));
    }, 3800);
  }, []);

  const [isSyncing, setIsSyncing] = useState<boolean>(false);
  const [modelDiagnosticStatus, setModelDiagnosticStatus] = useState<'normal' | 'simulate_offline' | 'recovered'>('normal');

  // Collaborative State
  const [sharedLists, setSharedLists] = useState<SharedTodoList[]>(INITIAL_SHARED_LISTS);
  const [activeSharedListId, setActiveSharedListId] = useState<string | null>(
    INITIAL_SHARED_LISTS[0]?.id || null
  );
  const [teamMembers] = useState<User[]>(AVAILABLE_TEAM_MEMBERS);
  const [projectComments, setProjectComments] = useState<Record<string, ProjectComment[]>>(
    INITIAL_PROJECT_COMMENTS
  );
  const [activeDiscussingProjectId, setActiveDiscussingProjectId] = useState<string | null>(null);

  // Mindmap & Bi-directional Links State
  const [customBiLinks, setCustomBiLinks] = useState<MindMapBiLink[]>([]);

  const addCustomBiLink = useCallback(
    (
      sourceId: string,
      targetId: string,
      relationType: 'references' | 'produces' | 'belongs_to' | 'assignee' | 'timeline' | 'custom',
      label: string,
      snippet?: string
    ) => {
      const newLink: MindMapBiLink = {
        id: `custom-bilink-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
        source: sourceId,
        target: targetId,
        relationType,
        label: label || '双链互联',
        createdAt: '2026-09-15',
        strength: 1.5,
        snippet: snippet || `手动建立双向引用: [[${sourceId}]] <===> [[${targetId}]]`,
      };
      setCustomBiLinks(prev => [...prev, newLink]);
      showToast(`已成功建立双链: ${label || '自定义互联'}`);
    },
    [showToast]
  );

  const removeCustomBiLink = useCallback(
    (linkId: string) => {
      setCustomBiLinks(prev => prev.filter(l => l.id !== linkId));
      showToast('已解除该双向链接');
    },
    [showToast]
  );

  // Sync collaborative state from server
  useEffect(() => {
    fetch('/api/collaborative/shared-lists')
      .then(res => (res.ok ? res.json() : null))
      .then(data => {
        if (Array.isArray(data) && data.length > 0) {
          setSharedLists(data);
        }
      })
      .catch(() => {});
  }, []);

  const [engineState, setEngineState] = useState<SystemEngineState>({
    status: 'idle',
    lastRunTimestamp: '2026-09-15 10:15:00',
    processedDays: 90,
    stats: {
      totalConversations: INITIAL_CONVERSATIONS.length,
      activeTodos: INITIAL_TODOS.filter(t => t.status !== 'completed' && !t.isIgnored).length,
      storedFiles: INITIAL_KNOWLEDGE_ITEMS.filter(k => k.intakeStatus === 'stored').length,
      dedupedCount: 1,
    },
  });

  useEffect(() => {
    let active = true;

    const refreshConversations = async () => {
      try {
        const response = await fetch('/api/conversations/live', { cache: 'no-store' });
        if (!response.ok) throw new Error(`conversation database returned ${response.status}`);
        const snapshot = await response.json() as {
          source: string;
          total: number;
          revision: string;
          updatedAt: string;
          items: Conversation[];
        };
        if (!active || snapshot.source !== 'sqlite-readonly' || !Array.isArray(snapshot.items)) return;

        setConversationDataSource('database');
        setConversationDataUpdatedAt(snapshot.updatedAt);
        setConversationTotal(snapshot.total);

        if (conversationRevisionRef.current === snapshot.revision) return;
        conversationRevisionRef.current = snapshot.revision;

        // The backend is the source of truth for isFollowed (it persists to
        // conversation_overrides).  Do NOT overlay stale local state, otherwise
        // a persisted star/unstar would be undone on the next poll.
        setConversations(snapshot.items);
        setSelectedConversation(previous => {
          if (!previous) return snapshot.items[0] || null;
          return snapshot.items.find(item => item.id === previous.id) || snapshot.items[0] || null;
        });
        setEngineState(previous => ({
          ...previous,
          stats: { ...previous.stats, totalConversations: snapshot.total },
        }));
      } catch {
        if (!active || conversationRevisionRef.current) return;
        setConversationDataSource('prototype');
        setConversationTotal(INITIAL_CONVERSATIONS.length);
      }
    };

    void refreshConversations();
    const intervalId = window.setInterval(refreshConversations, 30_000);
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') void refreshConversations();
    };
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      active = false;
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  useEffect(() => {
    if (conversationDataSource !== 'database' || !selectedConversation) return;
    const controller = new AbortController();
    fetch(`/api/conversations/${encodeURIComponent(selectedConversation.id)}/messages?limit=100`, {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(response => response.ok ? response.json() : null)
      .then(data => {
        if (Array.isArray(data?.items)) {
          setMessages(previous => ({ ...previous, [selectedConversation.id]: data.items }));
        }
      })
      .catch(() => {});
    return () => controller.abort();
  }, [conversationDataSource, selectedConversation?.id]);

  // Load real todos from the gateway (derived `tasks` table).  These replace
  // the prototype list once the database is reachable; completion/ignore are
  // persisted via PATCH /api/gateway/tasks/:id.
  useEffect(() => {
    if (conversationDataSource !== 'database') return;
    let active = true;
    const loadRealTodos = async () => {
      try {
        const res = await fetch('/api/gateway/tasks?limit=300', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json() as { items?: Array<Record<string, unknown>> };
        if (!active || !Array.isArray(data.items)) return;
        const realTodos: TodoItem[] = data.items.map((t) => {
          const gStatus = String(t.status || 'todo');
          const isIgnored = gStatus === 'ignored';
          const status: TodoStatus = gStatus === 'completed' || gStatus === 'done'
            ? 'completed'
            : 'pending';
          return {
            id: String(t.taskId || t.task_id || ''),
            title: String(t.content || ''),
            description: '',
            status,
            assigneeRole: 'other',
            dueDate: String(t.dueDate || t.due_date || '待确认'),
            isUrgent: t.priority === 'high',
            isOverdue: false,
            sourceMessageId: t.sourceMessageId != null ? String(t.sourceMessageId) : '',
            sourceConversationId: String(t.conversationId || t.conversation_id || ''),
            sourceConversationName: String(t.conversationTitle || ''),
            sourceSnippet: String(t.content || ''),
            evidenceText: t.senderName ? `来自 ${String(t.senderName)} 的会话任务` : '来自本地会话任务库',
            isIgnored,
            manualModified: false,
            createdAt: new Date(Number(t.sentAt || Date.now())).toISOString(),
            confidence: 'clear',
          };
        });
        setTodos(realTodos);
      } catch {
        // Keep prototype todos if the gateway is unreachable.
      }
    };
    void loadRealTodos();
  }, [conversationDataSource]);

  // Global Ctrl/Cmd + K shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsCommandPaletteOpen(prev => !prev);
      }
      if (e.key === 'Escape') {
        setIsCommandPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Jump to a source message anywhere in history, not just the last 100 rows.
  // When the message is older than the loaded window, ask the server to build
  // a window anchored on it.  If it cannot be located we say so explicitly
  // instead of pretending we highlighted it.
  const jumpToMessage = useCallback(async (conversationId: string, messageId: string) => {
    const targetConv = conversations.find(c => c.id === conversationId);
    if (!targetConv) return;
    setSelectedConversation(targetConv);
    setSelectedProject(null);
    setSelectedTodo(null);
    setSelectedKnowledgeItem(null);

    try {
      const res = await fetch(
        `/api/conversations/${encodeURIComponent(conversationId)}/messages?targetMessageId=${encodeURIComponent(messageId)}&limit=100`,
        { cache: 'no-store' }
      );
      if (res.status === 404) {
        showToast('未找到指定来源消息，可能已超出当前可加载范围。');
        setHighlightedMessageId(null);
        return;
      }
      if (!res.ok) throw new Error(`messages ${res.status}`);
      const data = await res.json() as { items?: ChatMessage[] };
      if (!Array.isArray(data.items) || data.items.length === 0) {
        showToast('未找到指定来源消息。');
        setHighlightedMessageId(null);
        return;
      }
      setMessages(prev => ({ ...prev, [conversationId]: data.items! }));
      setHighlightedMessageId(messageId);
      setIsInspectorOpen(true);
      showToast(`已定位到会话「${targetConv.name}」中的原始消息`);
    } catch {
      showToast('定位原始消息失败，请稍后重试。');
      setHighlightedMessageId(null);
    }
  }, [conversations, showToast]);

  // Update Todo status: optimistic local update, then persist to the gateway
  // derived `tasks` table.  A failed request rolls back the optimistic change
  // and surfaces an explicit error toast (never silent success).
  const updateTodoStatus = useCallback((id: string, status: TodoStatus) => {
    if (modelDiagnosticStatus === 'simulate_offline') {
      showToast('⚠️ 仿真模式：模型服务不可用，任务状态更新失败已自动回滚');
      return false;
    }

    const previous = todos.find(t => t.id === id);
    setTodos(prev =>
      prev.map(t => {
        if (t.id === id) {
          return { ...t, status, manualModified: true };
        }
        return t;
      })
    );

    const target = previous;
    if (target) {
      const logEntry: AuditLogEntry = {
        id: `audit-${Date.now()}`,
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        action: `待办状态变更 -> ${status}`,
        type: 'manual_override',
        details: `人工调整任务「${target.title}」状态至 ${status}，已标记为人工保护。`,
        actor: 'JoyBoy',
      };
      setAuditLogs(prev => [logEntry, ...prev]);
      showToast(`待办已移动至「${status === 'draft' ? '草稿箱' : status === 'pending' ? '待处理' : status === 'in_progress' ? '进行中' : '已完成'}」`);
    }

    const gatewayStatus = status === 'completed' ? 'completed' : 'todo';
    void (async () => {
      try {
        const res = await fetch(`/api/gateway/tasks/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: gatewayStatus }),
        });
        if (!res.ok) throw new Error(`gateway returned ${res.status}`);
      } catch {
        if (previous) {
          setTodos(prev => prev.map(t => t.id === id
            ? { ...t, status: previous.status, manualModified: previous.manualModified }
            : t));
        }
        showToast('待办状态更新失败，已回滚');
      }
    })();
    return true;
  }, [todos, modelDiagnosticStatus, showToast]);

  const updateTodoDueDate = useCallback((id: string, newDate: string) => {
    setTodos(prev =>
      prev.map(t => (t.id === id ? { ...t, dueDate: newDate, manualModified: true } : t))
    );
    showToast(`截止期限已修改为：${newDate}`);
  }, [showToast]);

  const toggleIgnoreTodo = useCallback((id: string) => {
    const previous = todos.find(t => t.id === id);
    const nextIgnored = !previous?.isIgnored;

    setTodos(prev =>
      prev.map(t => {
        if (t.id === id) {
          const logEntry: AuditLogEntry = {
            id: `audit-${Date.now()}`,
            timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
            action: nextIgnored ? '人工忽略待办' : '撤销忽略待办',
            type: 'manual_override',
            details: `${nextIgnored ? '忽略' : '恢复'}任务「${t.title}」，后续增量扫描遵循该人工决策。`,
            actor: 'JoyBoy',
          };
          setAuditLogs(l => [logEntry, ...l]);
          showToast(nextIgnored ? '已忽略该待办（自动增量不会重复提出）' : '已恢复该待办');
          return { ...t, isIgnored: nextIgnored, manualModified: true };
        }
        return t;
      })
    );

    void (async () => {
      try {
        const res = await fetch(`/api/gateway/tasks/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: nextIgnored ? 'ignored' : 'todo' }),
        });
        if (!res.ok) throw new Error(`gateway returned ${res.status}`);
      } catch {
        if (previous) {
          setTodos(prev => prev.map(t => t.id === id
            ? { ...t, isIgnored: previous.isIgnored, manualModified: previous.manualModified }
            : t));
        }
        showToast('忽略状态更新失败，已回滚');
      }
    })();
  }, [todos, showToast]);

  const createTodo = useCallback((newTodo: Partial<TodoItem>) => {
    const item: TodoItem = {
      id: `todo-custom-${Date.now()}`,
      title: newTodo.title || '新待办任务',
      description: newTodo.description || '',
      status: newTodo.status || 'pending',
      assigneeRole: newTodo.assigneeRole || 'i_am_responsible',
      dueDate: newTodo.dueDate || '2026-09-17 18:00',
      isUrgent: false,
      isOverdue: false,
      sourceMessageId: 'manual',
      sourceConversationId: 'manual',
      sourceConversationName: '人工手动创建',
      sourceSnippet: '由 JoyBoy 在 ChatOS 工作区手动建立',
      evidenceText: '人工直接指派并确认行动。',
      isIgnored: false,
      manualModified: true,
      createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
      confidence: 'clear',
    };
    setTodos(prev => [item, ...prev]);
    showToast('已创建新待办');
  }, [showToast]);

  const deleteTodo = useCallback((id: string) => {
    setTodos(prev => prev.filter(t => t.id !== id));
    showToast('已删除待办项');
  }, [showToast]);

  // Project operations
  const createProject = useCallback((name: string, currentAction: string, linkedConvIds: string[]) => {
    const newProj: Project = {
      id: `proj-${Date.now()}`,
      name,
      code: name.slice(0, 8).toUpperCase(),
      oneLineProgress: '刚刚创建的项目，正在聚合多方会话证据',
      currentAction: currentAction || '梳理项目目标并确立首批行动计划',
      linkedConversationIds: linkedConvIds,
      completionPercentage: 10,
      status: 'active',
      isAutoGenerated: false,
      evidenceCount: 1,
      lastUpdated: '刚刚',
      color: '#8b5cf6',
    };
    setProjects(prev => [newProj, ...prev]);
    // update linked conversations
    setConversations(prev =>
      prev.map(c =>
        linkedConvIds.includes(c.id)
          ? { ...c, linkedProjectIds: Array.from(new Set([...c.linkedProjectIds, newProj.id])) }
          : c
      )
    );
    showToast(`项目「${name}」已创建并关联 ${linkedConvIds.length} 个会话`);
  }, [showToast]);

  const renameProject = useCallback((id: string, newName: string) => {
    setProjects(prev =>
      prev.map(p => (p.id === id ? { ...p, name: newName, lastUpdated: '刚刚' } : p))
    );
    showToast(`项目名称已更新为「${newName}」`);
  }, [showToast]);

  const archiveProject = useCallback((id: string) => {
    setProjects(prev =>
      prev.map(p => (p.id === id ? { ...p, status: 'archived', lastUpdated: '刚刚' } : p))
    );
    showToast('项目已归档');
  }, [showToast]);

  const linkConversationToProject = useCallback((projectId: string, convId: string) => {
    setProjects(prev =>
      prev.map(p => {
        if (p.id === projectId && !p.linkedConversationIds.includes(convId)) {
          return { ...p, linkedConversationIds: [...p.linkedConversationIds, convId] };
        }
        return p;
      })
    );
    setConversations(prev =>
      prev.map(c => {
        if (c.id === convId && !c.linkedProjectIds.includes(projectId)) {
          return { ...c, linkedProjectIds: [...c.linkedProjectIds, projectId] };
        }
        return c;
      })
    );
    showToast('已成功添加会话关联（不影响该会话与其他项目的关联）');
  }, [showToast]);

  const unlinkConversationFromProject = useCallback((projectId: string, convId: string) => {
    setProjects(prev =>
      prev.map(p => {
        if (p.id === projectId) {
          return { ...p, linkedConversationIds: p.linkedConversationIds.filter(id => id !== convId) };
        }
        return p;
      })
    );
    setConversations(prev =>
      prev.map(c => {
        if (c.id === convId) {
          return { ...c, linkedProjectIds: c.linkedProjectIds.filter(id => id !== projectId) };
        }
        return c;
      })
    );
    showToast('已移除该会话与此项目的关联（保留在其他项目中）');
  }, [showToast]);

  // Knowledge operations
  const retryKnowledgeIntake = useCallback((_id: string) => {
    showToast('重试服务尚未接入，资料状态与原始信息未改变');
  }, [showToast]);

  const revokeKnowledgeItem = useCallback((id: string) => {
    setKnowledgeItems(prev => prev.filter(k => k.id !== id));
    showToast('已撤回收录并移出资料库');
  }, [showToast]);

  // ==========================================
  // Collaborative Actions (Shared Lists & Tasks)
  // ==========================================

  const createSharedTodoList = useCallback(
    async (
      title: string,
      description: string,
      collaboratorIds: string[],
      associatedProjectId?: string
    ) => {
      const selectedCollabs = teamMembers.filter(m => collaboratorIds.includes(m.id));
      if (!selectedCollabs.some(c => c.id === CURRENT_USER.id)) {
        selectedCollabs.unshift(CURRENT_USER);
      }
      const newList: SharedTodoList = {
        id: `shared-list-${Date.now()}`,
        title,
        description,
        createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
        createdBy: CURRENT_USER,
        associatedProjectId,
        collaborators: selectedCollabs,
        tasks: [],
      };

      setSharedLists(prev => [newList, ...prev]);
      setActiveSharedListId(newList.id);
      showToast(`已创建共享清单「${title}」，包含 ${selectedCollabs.length} 位协作成员`);

      const logEntry: AuditLogEntry = {
        id: `audit-${Date.now()}`,
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        action: '创建跨团队共享待办清单',
        type: 'manual_override',
        details: `JoyBoy 发起共享清单「${title}」，协作成员：${selectedCollabs.map(c => c.name).join('、')}。`,
        actor: 'JoyBoy',
      };
      setAuditLogs(l => [logEntry, ...l]);

      try {
        const res = await fetch('/api/collaborative/shared-lists', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title,
            description,
            collaboratorIds,
            associatedProjectId,
            createdBy: CURRENT_USER,
          }),
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (err) {
        setSharedLists(prev => prev.filter(l => l.id !== newList.id));
        showToast('共享清单保存失败，已回滚');
        return null;
      }

      return newList;
    },
    [teamMembers, showToast]
  );

  const deleteSharedTodoList = useCallback(
    async (listId: string) => {
      const listToDelete = sharedLists.find(l => l.id === listId);
      setSharedLists(prev => prev.filter(l => l.id !== listId));
      if (activeSharedListId === listId) {
        const remaining = sharedLists.filter(l => l.id !== listId);
        setActiveSharedListId(remaining[0]?.id || null);
      }
      showToast(listToDelete ? `已删除共享清单「${listToDelete.title}」` : '已删除共享清单');

      try {
        const res = await fetch(`/api/collaborative/shared-lists/${listId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        if (listToDelete) setSharedLists(prev => [listToDelete!, ...prev]);
        showToast('删除清单失败，已回滚');
      }
    },
    [sharedLists, activeSharedListId, showToast]
  );

  const addSharedTask = useCallback(
    async (
      listId: string,
      taskData: {
        title: string;
        description?: string;
        assigneeIds: string[];
        dueDate: string;
        priority: 'high' | 'medium' | 'low';
      }
    ) => {
      const assignees = teamMembers.filter(m => taskData.assigneeIds.includes(m.id));
      const newTask: SharedTask = {
        id: `stask-${Date.now()}`,
        listId,
        title: taskData.title,
        description: taskData.description || '',
        isCompleted: false,
        assignees: assignees.length > 0 ? assignees : [CURRENT_USER],
        dueDate: taskData.dueDate || '2026-09-18 18:00',
        priority: taskData.priority || 'medium',
        createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
        createdBy: CURRENT_USER,
      };

      setSharedLists(prev =>
        prev.map(list => {
          if (list.id === listId) {
            return { ...list, tasks: [...list.tasks, newTask] };
          }
          return list;
        })
      );

      const targetList = sharedLists.find(l => l.id === listId);
      const assigneeNames = newTask.assignees.map(a => a.name).join('、');
      showToast(`已在清单「${targetList?.title || '共享待办'}」添加新任务，指派给：${assigneeNames}`);

      const logEntry: AuditLogEntry = {
        id: `audit-${Date.now()}`,
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        action: '协同指派任务',
        type: 'manual_override',
        details: `在「${targetList?.title || '共享清单'}」新增协同任务「${newTask.title}」，指派人员：${assigneeNames}。`,
        actor: 'JoyBoy',
      };
      setAuditLogs(l => [logEntry, ...l]);

      try {
        const res = await fetch(`/api/collaborative/shared-lists/${listId}/tasks`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: taskData.title,
            description: taskData.description,
            assigneeIds: taskData.assigneeIds,
            dueDate: taskData.dueDate,
            priority: taskData.priority,
            createdBy: CURRENT_USER,
          }),
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        setSharedLists(prev => prev.map(list =>
          list.id === listId
            ? { ...list, tasks: list.tasks.filter(t => t.id !== newTask.id) }
            : list
        ));
        showToast('协作任务保存失败，已回滚');
        return null;
      }

      return newTask;
    },
    [teamMembers, sharedLists, showToast]
  );

  const toggleCompleteSharedTask = useCallback(
    async (listId: string, taskId: string, completedByUserId?: string) => {
      const targetUser = teamMembers.find(m => m.id === completedByUserId) || CURRENT_USER;
      let nextState = false;
      let taskTitle = '';

      setSharedLists(prev =>
        prev.map(list => {
          if (list.id === listId) {
            return {
              ...list,
              tasks: list.tasks.map(t => {
                if (t.id === taskId) {
                  nextState = !t.isCompleted;
                  taskTitle = t.title;
                  return {
                    ...t,
                    isCompleted: nextState,
                    completedAt: nextState
                      ? new Date().toISOString().replace('T', ' ').slice(0, 16)
                      : undefined,
                    completedBy: nextState ? targetUser : undefined,
                  };
                }
                return t;
              }),
            };
          }
          return list;
        })
      );

      const statusDesc = nextState ? '已完成' : '重新开启';
      showToast(`${targetUser.name} 将任务「${taskTitle}」标记为 ${statusDesc}`);

      const logEntry: AuditLogEntry = {
        id: `audit-${Date.now()}`,
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        action: `协同任务状态变更 -> ${statusDesc}`,
        type: 'manual_override',
        details: `${targetUser.name} 协同更新共享任务「${taskTitle}」为 ${statusDesc}。`,
        actor: targetUser.name,
      };
      setAuditLogs(l => [logEntry, ...l]);

      try {
        const res = await fetch(`/api/collaborative/shared-lists/${listId}/tasks/${taskId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            isCompleted: nextState,
            completedBy: nextState ? targetUser : null,
          }),
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        setSharedLists(prev => prev.map(list => {
          if (list.id === listId) {
            return {
              ...list,
              tasks: list.tasks.map(t =>
                t.id === taskId
                  ? { ...t, isCompleted: !nextState, completedAt: undefined, completedBy: undefined }
                  : t
              ),
            };
          }
          return list;
        }));
        showToast('协作任务状态更新失败，已回滚');
        return !nextState;
      }

      return nextState;
    },
    [teamMembers, showToast]
  );

  const deleteSharedTask = useCallback(
    async (listId: string, taskId: string) => {
      setSharedLists(prev =>
        prev.map(list => {
          if (list.id === listId) {
            return { ...list, tasks: list.tasks.filter(t => t.id !== taskId) };
          }
          return list;
        })
      );
      showToast('协同任务已删除');
      try {
        const res = await fetch(`/api/collaborative/shared-lists/${listId}/tasks/${taskId}`, {
          method: 'DELETE',
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        showToast('删除协作任务失败，请刷新后重试');
      }
    },
    [showToast]
  );

  const updateSharedTask = useCallback(
    async (listId: string, taskId: string, updates: Partial<SharedTask>) => {
      setSharedLists(prev =>
        prev.map(list => {
          if (list.id === listId) {
            return {
              ...list,
              tasks: list.tasks.map(t => (t.id === taskId ? { ...t, ...updates } : t)),
            };
          }
          return list;
        })
      );
      showToast('任务已更新');
      try {
        const res = await fetch(`/api/collaborative/shared-lists/${listId}/tasks/${taskId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updates),
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        showToast('协作任务更新失败，请刷新后重试');
      }
    },
    [showToast]
  );

  // ==========================================
  // Collaborative Actions (Project Comments)
  // ==========================================

  const addProjectComment = useCallback(
    async (projectId: string, content: string, asUserId?: string) => {
      const author = teamMembers.find(m => m.id === asUserId) || CURRENT_USER;
      const newComment: ProjectComment = {
        id: `pcomm-${Date.now()}`,
        projectId,
        author,
        content: content.trim(),
        timestamp: '刚刚',
        reactions: [],
      };

      setProjectComments(prev => ({
        ...prev,
        [projectId]: [newComment, ...(prev[projectId] || [])],
      }));

      // Update project comments count
      setProjects(prev =>
        prev.map(p => {
          if (p.id === projectId) {
            return {
              ...p,
              commentsCount: (p.commentsCount || 0) + 1,
              lastUpdated: '刚刚',
            };
          }
          return p;
        })
      );

      const targetProj = projects.find(p => p.id === projectId);
      showToast(`已在项目「${targetProj?.name || projectId}」发表讨论批注`);

      const logEntry: AuditLogEntry = {
        id: `audit-${Date.now()}`,
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        action: '项目讨论卡片发布评论',
        type: 'manual_override',
        details: `${author.name} 对项目「${targetProj?.name || projectId}」发表了协作讨论批注。`,
        actor: author.name,
      };
      setAuditLogs(l => [logEntry, ...l]);

      try {
        const res = await fetch(`/api/collaborative/projects/${projectId}/comments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content,
            author,
          }),
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        setProjectComments(prev => ({
          ...prev,
          [projectId]: (prev[projectId] || []).filter(c => c.id !== newComment.id),
        }));
        setProjects(prev => prev.map(p =>
          p.id === projectId
            ? { ...p, commentsCount: Math.max(0, (p.commentsCount || 1) - 1) }
            : p
        ));
        showToast('评论保存失败，已回滚');
        return null;
      }

      return newComment;
    },
    [teamMembers, projects, showToast]
  );

  const deleteProjectComment = useCallback(
    async (projectId: string, commentId: string) => {
      setProjectComments(prev => ({
        ...prev,
        [projectId]: (prev[projectId] || []).filter(c => c.id !== commentId),
      }));
      setProjects(prev =>
        prev.map(p => {
          if (p.id === projectId) {
            return {
              ...p,
              commentsCount: Math.max(0, (p.commentsCount || 1) - 1),
            };
          }
          return p;
        })
      );
      showToast('讨论评论已删除');

      try {
        const res = await fetch(`/api/collaborative/projects/${projectId}/comments/${commentId}`, {
          method: 'DELETE',
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        showToast('删除评论失败，请刷新后重试');
      }
    },
    [showToast]
  );

  const toggleCommentReaction = useCallback(
    async (projectId: string, commentId: string, emoji: string, asUserId?: string) => {
      const uId = asUserId || CURRENT_USER.id;
      setProjectComments(prev => {
        const comments = prev[projectId] || [];
        const updated = comments.map(c => {
          if (c.id === commentId) {
            const reactions = [...(c.reactions || [])];
            const rIdx = reactions.findIndex(r => r.emoji === emoji);
            if (rIdx > -1) {
              const r = reactions[rIdx];
              if (r.users.includes(uId)) {
                const nextUsers = r.users.filter(u => u !== uId);
                if (nextUsers.length === 0) {
                  reactions.splice(rIdx, 1);
                } else {
                  reactions[rIdx] = { ...r, users: nextUsers, count: nextUsers.length };
                }
              } else {
                reactions[rIdx] = { ...r, users: [...r.users, uId], count: r.count + 1 };
              }
            } else {
              reactions.push({ emoji, count: 1, users: [uId] });
            }
            return { ...c, reactions };
          }
          return c;
        });
        return { ...prev, [projectId]: updated };
      });

      try {
        const res = await fetch(`/api/collaborative/projects/${projectId}/comments/${commentId}/reactions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ emoji, userId: uId }),
        });
        if (!res.ok) throw new Error(`collaborative ${res.status}`);
      } catch (e) {
        showToast('表情反应同步失败，请刷新后重试');
      }
    },
    []
  );

  const toggleFollowConversation = useCallback((id: string) => {
    const previous = conversations.find(c => c.id === id);
    if (!previous) return;
    const nextFollow = !previous.isFollowed;

    const applyOptimistic = (c: Conversation): Conversation => ({
      ...c,
      isFollowed: nextFollow,
      priorityScore: nextFollow ? c.priorityScore + 25 : Math.max(20, c.priorityScore - 25),
    });

    setConversations(prev => prev.map(c => (c.id === id ? applyOptimistic(c) : c)));
    setSelectedConversation(curr => (curr && curr.id === id ? applyOptimistic(curr) : curr));
    showToast(nextFollow ? `⭐ 已将「${previous.name}」加入左侧星标会话` : `已从星标会话移除「${previous.name}」`);

    void (async () => {
      try {
        const res = await fetch(`/api/gateway/conversations/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ isFavorite: nextFollow }),
        });
        if (!res.ok) throw new Error(`gateway returned ${res.status}`);
      } catch {
        const rollback = (c: Conversation): Conversation => ({
          ...c,
          isFollowed: previous.isFollowed,
          priorityScore: previous.priorityScore,
        });
        setConversations(prev => prev.map(c => (c.id === id ? rollback(c) : c)));
        setSelectedConversation(curr => (curr && curr.id === id ? rollback(curr) : curr));
        showToast('星标状态更新失败，已回滚');
      }
    })();
  }, [conversations, showToast]);

  const setEnterpriseScale = useCallback(
    (scale: 'curated' | 'medium_120' | 'max_500') => {
      setEnterpriseScaleState(scale);
      if (scale === 'curated') {
        setConversations(INITIAL_CONVERSATIONS);
        showToast('已切换至精选核心协作圈 (21 个会话)');
      } else if (scale === 'medium_120') {
        const expanded = generateScaleEnterpriseDirectory(120);
        setConversations(expanded);
        showToast('已载入企业级协作目录 (120 位联系人与群组)');
      } else {
        const expanded = generateScaleEnterpriseDirectory(500);
        setConversations(expanded);
        showToast('⚡ 已扩展至近千人高规模仿真目录 (500 位联系人与群组)');
      }
    },
    [showToast]
  );

  const markConversationAsRead = useCallback((id: string) => {
    setConversations(prev =>
      prev.map(c => (c.id === id ? { ...c, unreadCount: 0 } : c))
    );
  }, []);

  // Real incremental organize: POST to the gateway, poll the job until it
  // leaves `running`, surface honest status (completed / degraded / failed),
  // then re-read the live snapshot so derived state (todos, overrides) shows.
  const runIncrementalOrganize = useCallback(async () => {
    if (modelDiagnosticStatus === 'simulate_offline') {
      const message = '当前为故障演示，未执行真实整理或回滚';
      setEngineState(prev => ({ ...prev, status: 'error_fallback' }));
      showToast(message);
      return { addedCount: 0, message };
    }
    setIsSyncing(true);
    setEngineState(prev => ({ ...prev, status: 'syncing' }));
    try {
      const startRes = await fetch('/api/gateway/organize/incremental', { method: 'POST' });
      if (!startRes.ok) throw new Error(`organize start ${startRes.status}`);
      const job = await startRes.json() as { jobId?: string; id?: string };
      const jobId = job.jobId || job.id;
      if (!jobId) throw new Error('网关未返回任务句柄');

      let finalStatus = 'running';
      let result: Record<string, unknown> | null = null;
      for (let i = 0; i < 180; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const jobRes = await fetch(`/api/gateway/jobs/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
        if (!jobRes.ok) throw new Error(`任务轮询 ${jobRes.status}`);
        const jobData = await jobRes.json() as { status?: string; result?: Record<string, unknown> };
        finalStatus = jobData.status || 'running';
        result = jobData.result || null;
        if (finalStatus !== 'running') break;
      }
      const addedCount = Number(result?.addedCount ?? result?.newTasks ?? result?.tasks ?? 0);
      const message = finalStatus === 'completed'
        ? `增量整理完成：处理 ${addedCount} 项`
        : finalStatus === 'degraded'
        ? `增量整理完成（部分降级）：${addedCount} 项，详情见日志`
        : `增量整理失败：${finalStatus}`;
      setEngineState(prev => ({
        ...prev,
        status: finalStatus === 'failed' ? 'error_fallback' : 'idle',
        lastRunTimestamp: new Date().toISOString().slice(0, 19).replace('T', ' '),
      }));
      showToast(message);

      // Re-read live conversations so derived overrides appear immediately.
      try {
        const snap = await fetch('/api/conversations/live', { cache: 'no-store' }).then(r => r.json() as { items?: Conversation[] });
        if (Array.isArray(snap.items)) {
          conversationRevisionRef.current = null;
          setConversations(snap.items);
        }
      } catch { /* non-fatal */ }
      return { addedCount, message };
    } catch (e) {
      const message = `增量整理失败：${e instanceof Error ? e.message : '网络错误'}`;
      setEngineState(prev => ({ ...prev, status: 'error_fallback' }));
      showToast(message);
      return { addedCount: 0, message };
    } finally {
      setIsSyncing(false);
    }
  }, [modelDiagnosticStatus, showToast]);

  const value = useMemo(
    () => ({
      activeWorkspace,
      setActiveWorkspace,
      isSidebarCollapsed,
      setIsSidebarCollapsed,
      selectedConversation,
      setSelectedConversation,
      selectedProject,
      setSelectedProject,
      selectedTodo,
      setSelectedTodo,
      selectedKnowledgeItem,
      setSelectedKnowledgeItem,
      highlightedMessageId,
      setHighlightedMessageId,
      isInspectorOpen,
      setIsInspectorOpen,
      conversations,
      conversationDataSource,
      conversationDataUpdatedAt,
      conversationTotal,
      todos,
      projects,
      knowledgeItems,
      messages,
      auditLogs,
      engineState,
      // Collaborative Shared Lists & Tasks
      sharedLists,
      activeSharedListId,
      setActiveSharedListId,
      teamMembers,
      createSharedTodoList,
      deleteSharedTodoList,
      addSharedTask,
      toggleCompleteSharedTask,
      deleteSharedTask,
      updateSharedTask,
      // Collaborative Project Comments
      projectComments,
      activeDiscussingProjectId,
      setActiveDiscussingProjectId,
      addProjectComment,
      deleteProjectComment,
      toggleCommentReaction,
      // Mindmap & Bi-directional Links
      customBiLinks,
      addCustomBiLink,
      removeCustomBiLink,
      // Standard Actions
      updateTodoStatus,
      updateTodoDueDate,
      toggleIgnoreTodo,
      createTodo,
      deleteTodo,
      createProject,
      renameProject,
      archiveProject,
      linkConversationToProject,
      unlinkConversationFromProject,
      retryKnowledgeIntake,
      revokeKnowledgeItem,
      toggleFollowConversation,
      markConversationAsRead,
      runIncrementalOrganize,
      isSyncing,
      modelDiagnosticStatus,
      setModelDiagnosticStatus,
      jumpToMessage,
      isCommandPaletteOpen,
      setIsCommandPaletteOpen,
      searchQuery,
      setSearchQuery,
      activeAuxModal,
      setActiveAuxModal,
      toastMessage,
      showToast,
      enterpriseScale,
      setEnterpriseScale,
    }),
    [
      activeWorkspace,
      isSidebarCollapsed,
      selectedConversation,
      selectedProject,
      selectedTodo,
      selectedKnowledgeItem,
      highlightedMessageId,
      isInspectorOpen,
      conversations,
      conversationDataSource,
      conversationDataUpdatedAt,
      conversationTotal,
      todos,
      projects,
      knowledgeItems,
      messages,
      auditLogs,
      engineState,
      sharedLists,
      activeSharedListId,
      teamMembers,
      createSharedTodoList,
      deleteSharedTodoList,
      addSharedTask,
      toggleCompleteSharedTask,
      deleteSharedTask,
      updateSharedTask,
      projectComments,
      activeDiscussingProjectId,
      addProjectComment,
      deleteProjectComment,
      toggleCommentReaction,
      customBiLinks,
      addCustomBiLink,
      removeCustomBiLink,
      updateTodoStatus,
      updateTodoDueDate,
      toggleIgnoreTodo,
      createTodo,
      deleteTodo,
      createProject,
      renameProject,
      archiveProject,
      linkConversationToProject,
      unlinkConversationFromProject,
      retryKnowledgeIntake,
      revokeKnowledgeItem,
      toggleFollowConversation,
      markConversationAsRead,
      runIncrementalOrganize,
      isSyncing,
      modelDiagnosticStatus,
      jumpToMessage,
      isCommandPaletteOpen,
      searchQuery,
      activeAuxModal,
      toastMessage,
      showToast,
      enterpriseScale,
      setEnterpriseScale,
    ]
  );

  return <ChatOSContext.Provider value={value}>{children}</ChatOSContext.Provider>;
};

export const useChatOS = () => {
  const context = useContext(ChatOSContext);
  if (!context) {
    throw new Error('useChatOS must be used within a ChatOSProvider');
  }
  return context;
};
