import React, { useEffect, useRef, useState } from 'react';
import {
  MessageSquare,
  CheckSquare,
  FolderKanban,
  FileText,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  GitFork,
  FileCheck2,
  Cpu,
  ShieldCheck,
  Network,
  Brain,
  Search,
  Sparkles,
  MoveHorizontal,
  Check,
  Layers,
  Star,
  UserRound,
  BriefcaseBusiness,
  GraduationCap,
  Archive,
  Plus,
  Trash2,
} from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';
import { WorkspaceType } from '../types';
import { CURRENT_USER } from '../mockData';
import { selectDailyFocusConversations } from '../core/conversationFeatureFilters';

interface SidebarProps {
  onOpenThinking: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ onOpenThinking }) => {
  const {
    activeWorkspace,
    setActiveWorkspace,
    isSidebarCollapsed,
    setIsSidebarCollapsed,
    isAlertMotionEnabled,
    setIsAlertMotionEnabled,
    conversations,
    todos,
    projects,
    knowledgeItems,
    runIncrementalOrganize,
    isSyncing,
    setActiveAuxModal,
    modelDiagnosticStatus,
    isCommandPaletteOpen,
    setIsCommandPaletteOpen,
    conversationGroups,
    activeConversationGroupId,
    setActiveConversationGroupId,
    createConversationGroup,
    deleteConversationGroup,
    moveConversationToGroup,
  } = useChatOS();

  const [openToolGroup, setOpenToolGroup] = useState<'insights' | 'system' | 'view' | null>(null);
  const sidebarToolsRef = useRef<HTMLDivElement>(null);
  const [isAddingGroup, setIsAddingGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [dragTarget, setDragTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!openToolGroup) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!sidebarToolsRef.current?.contains(event.target as Node)) setOpenToolGroup(null);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [openToolGroup]);

  const unreadMessagesCount = conversations.reduce((acc, c) => acc + c.unreadCount, 0);
  const activeTodosCount = todos.filter(t => t.status !== 'completed' && !t.isIgnored).length;
  const activeProjectsCount = projects.filter(p => p.status === 'active').length;
  const pendingLibraryCount = knowledgeItems.filter(k => k.intakeStatus === 'pending_confirmation' || k.intakeStatus === 'pending_fetch').length;
  const dailyFocusCount = selectDailyFocusConversations(conversations).length;
  const secondaryGroups = [
    { id: 'favorite', name: '收藏', count: conversations.filter(item => item.isFollowed && !item.isArchived).length, Icon: Star, canDelete: false },
    ...conversationGroups.map(group => ({
      id: group.id,
      name: group.name,
      count: conversations.filter(item => !item.isArchived && item.groupId === group.id).length,
      Icon: group.id === 'personal' ? UserRound : group.id === 'work' ? BriefcaseBusiness : group.id === 'study' ? GraduationCap : FolderKanban,
      canDelete: true,
    })),
    { id: 'archived', name: '归档', count: conversations.filter(item => item.isArchived).length, Icon: Archive, canDelete: false },
  ];

  const openConversationGroup = (id: string) => {
    setActiveWorkspace('conversations');
    setActiveConversationGroupId(id);
    setOpenToolGroup(null);
    if (window.matchMedia('(max-width: 767px)').matches) setIsSidebarCollapsed(true);
  };

  const handleGroupDrop = (event: React.DragEvent, groupId: string) => {
    event.preventDefault();
    const conversationId = event.dataTransfer.getData('application/x-chatos-conversation') || event.dataTransfer.getData('text/plain');
    setDragTarget(null);
    if (conversationId) void moveConversationToGroup(conversationId, groupId);
  };

  const submitNewGroup = async (event: React.FormEvent) => {
    event.preventDefault();
    const created = await createConversationGroup(newGroupName);
    if (!created) return;
    setNewGroupName('');
    setIsAddingGroup(false);
    openConversationGroup(created.id);
  };

  const navItems: { type: WorkspaceType; label: string; icon: React.FC<{ className?: string }>; badge?: number; badgeColor?: string }[] = [
    {
      type: 'conversations',
      label: '会话',
      icon: MessageSquare,
      badge: unreadMessagesCount > 0 ? unreadMessagesCount : undefined,
      badgeColor: 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30',
    },
    {
      type: 'today',
      label: '今日',
      icon: Sparkles,
      badge: dailyFocusCount,
      badgeColor: 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30',
    },
    {
      type: 'todos',
      label: '待办',
      icon: CheckSquare,
      badge: activeTodosCount > 0 ? activeTodosCount : undefined,
      badgeColor: 'bg-amber-500/20 text-amber-300 border border-amber-500/30',
    },
    {
      type: 'projects',
      label: '项目',
      icon: FolderKanban,
      badge: activeProjectsCount,
      badgeColor: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
    },
    {
      type: 'library',
      label: '资料库',
      icon: FileText,
      badge: pendingLibraryCount > 0 ? pendingLibraryCount : undefined,
      badgeColor: 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30',
    },
  ];

  return (
    <aside
      className={`chatos-sidebar gary-glass gary-ultrathin relative flex flex-col justify-between h-full bg-[#0d121c] border-r border-[#1e293b] transition-all duration-200 z-20 select-none ${
        isSidebarCollapsed ? 'w-16' : 'w-64'
      }`}
    >
      {/* Top Header & Brand */}
      <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
        <div className={`flex items-center py-4 border-b border-[#1e293b]/70 ${
          isSidebarCollapsed ? 'justify-center px-2' : 'justify-between px-3.5'
        }`}>
          {!isSidebarCollapsed && (
            <div className="flex items-center gap-2.5">
              <img src="/chatos-mark.svg" alt="" className="w-8 h-8 flex-shrink-0" aria-hidden="true" />
              <div>
                <h1 className="text-sm font-semibold tracking-wide text-white flex items-center gap-1.5">
                  ChatOS
                  <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-indigo-950/70 border border-indigo-800/60 text-indigo-300">
                    2.0
                  </span>
                </h1>
                <p className="text-[11px] text-slate-400">关系与行动工作台</p>
              </div>
            </div>
          )}

          <button
            id="sidebar-toggle-btn"
            onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
            className="chatos-icon-button text-slate-400 hover:text-slate-200 transition-colors"
            title={isSidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
            aria-label={isSidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
          >
            {isSidebarCollapsed ? (
              <PanelLeftOpen className="w-4 h-4" />
            ) : (
              <PanelLeftClose className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* Primary navigation */}
        <nav className="chatos-sidebar-nav" aria-label="主工作区">
          {navItems.map(item => {
            const Icon = item.icon;
            const isActive = activeWorkspace === item.type;

            return (
              <React.Fragment key={item.type}>
              <button
                id={`nav-${item.type}`}
                onClick={() => {
                  setActiveWorkspace(item.type);
                  if (item.type === 'conversations') setActiveConversationGroupId('all');
                  setOpenToolGroup(null);
                  if (window.matchMedia('(max-width: 767px)').matches) setIsSidebarCollapsed(true);
                }}
                onDragOver={item.type === 'conversations' ? event => { event.preventDefault(); setDragTarget('all'); } : undefined}
                onDragLeave={item.type === 'conversations' ? () => setDragTarget(null) : undefined}
                onDrop={item.type === 'conversations' ? event => handleGroupDrop(event, 'all') : undefined}
                className={`chatos-sidebar-nav-item${isActive ? ' is-active' : ''}${item.type === 'conversations' && dragTarget === 'all' ? ' is-dragover' : ''}`}
                title={item.label}
                aria-label={item.label}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className="chatos-sidebar-nav-icon" />
                {!isSidebarCollapsed && (
                  <div className="chatos-sidebar-nav-copy">
                    <span className="chatos-sidebar-nav-label">{item.label}</span>
                    {item.badge !== undefined && (
                      <span className={`text-[11px] font-mono px-2 py-0.5 rounded-full font-semibold ${item.badgeColor || 'bg-slate-700 text-slate-200'}`}>
                        {item.badge}
                      </span>
                    )}
                  </div>
                )}
              </button>
              {item.type === 'conversations' && activeWorkspace === 'conversations' && !isCommandPaletteOpen && !isSidebarCollapsed && (
                <div className="chatos-conversation-groups" aria-label="会话分组">
                  {secondaryGroups.map(group => {
                    const GroupIcon = group.Icon;
                    const isGroupActive = activeWorkspace === 'conversations' && activeConversationGroupId === group.id;
                    return (
                      <div key={group.id} className="chatos-conversation-group-row">
                        <button
                          type="button"
                          className={`chatos-conversation-group${isGroupActive ? ' is-active' : ''}${dragTarget === group.id ? ' is-dragover' : ''}`}
                          onClick={() => openConversationGroup(group.id)}
                          onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setDragTarget(group.id); }}
                          onDragLeave={() => setDragTarget(null)}
                          onDrop={event => handleGroupDrop(event, group.id)}
                        >
                          <GroupIcon className="w-3.5 h-3.5" aria-hidden="true" />
                          <span>{group.name}</span>
                          <span className="chatos-conversation-group-count">{group.count}</span>
                        </button>
                        {group.canDelete && (
                          <button
                            type="button"
                            className="chatos-conversation-group-delete"
                            onClick={() => void deleteConversationGroup(group.id)}
                            aria-label={`删除分组 ${group.name}`}
                            title={`删除分组 ${group.name}`}
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        )}
                      </div>
                    );
                  })}
                  {isAddingGroup ? (
                    <form className="chatos-conversation-group-form" onSubmit={submitNewGroup}>
                      <input
                        autoFocus
                        value={newGroupName}
                        onChange={event => setNewGroupName(event.target.value)}
                        maxLength={12}
                        placeholder="分组名称"
                        aria-label="自定义分组名称"
                      />
                      <button type="submit" disabled={!newGroupName.trim()} aria-label="保存自定义分组"><Check className="w-3.5 h-3.5" /></button>
                    </form>
                  ) : (
                    <button type="button" className="chatos-conversation-group is-create" onClick={() => setIsAddingGroup(true)}>
                      <Plus className="w-3.5 h-3.5" /><span>自定义</span>
                    </button>
                  )}
                </div>
              )}
              </React.Fragment>
            );
          })}
          <button
            id="sidebar-global-search-btn"
            type="button"
            onClick={() => setIsCommandPaletteOpen(true)}
            className={`chatos-sidebar-nav-item${isCommandPaletteOpen ? ' is-active' : ''}`}
            aria-label="全局搜索"
            title="全局搜索"
            aria-current={isCommandPaletteOpen ? 'page' : undefined}
          >
            <Search className="chatos-sidebar-nav-icon" aria-hidden="true" />
            {!isSidebarCollapsed && <span className="chatos-sidebar-nav-label">搜索</span>}
          </button>
        </nav>

      </div>

      {/* Bottom User Card & Aux Tools */}
      <div className="p-2 border-t border-[#1e293b]/70 space-y-2">
        {/* Secondary capabilities stay behind three compact peer tools. */}
        <div ref={sidebarToolsRef} className={`chatos-sidebar-tools${isSidebarCollapsed ? ' is-collapsed' : ''}`}>
          <div className="chatos-sidebar-tool-group">
            <button
              id="sidebar-insights-btn"
              onClick={() => setOpenToolGroup(current => current === 'insights' ? null : 'insights')}
              className={`chatos-sidebar-tool${activeWorkspace === 'mindmap' ? ' is-active' : ''}`}
              title="关系、知识与推演"
              aria-label="洞察"
              aria-expanded={openToolGroup === 'insights'}
              aria-haspopup="menu"
            >
              <Brain className="chatos-sidebar-tool-icon" />
              {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">洞察</span>}
            </button>
            {openToolGroup === 'insights' && (
              <div className="chatos-sidebar-tool-menu" role="menu" aria-label="洞察工具">
                <button id="sidebar-mindmap-entry" role="menuitem" className="chatos-sidebar-menu-item" onClick={() => { setActiveWorkspace('mindmap'); setOpenToolGroup(null); }}>
                  <Network className="w-4 h-4" /><span>思维图谱</span>
                </button>
                <button id="aux-graph-btn" role="menuitem" className="chatos-sidebar-menu-item" onClick={() => { setActiveAuxModal('graph'); setOpenToolGroup(null); }}>
                  <GitFork className="w-4 h-4" /><span>关系图谱</span>
                </button>
                <button id="sidebar-thinking-trigger-btn" role="menuitem" className="chatos-sidebar-menu-item" onClick={() => { onOpenThinking(); setOpenToolGroup(null); }}>
                  <Brain className="w-4 h-4" /><span>深度推演</span>
                </button>
              </div>
            )}
          </div>

          <div className="chatos-sidebar-tool-group">
            <button
              id="sidebar-system-btn"
              onClick={() => setOpenToolGroup(current => current === 'system' ? null : 'system')}
              className="chatos-sidebar-tool"
              title="整理、审计与模型状态"
              aria-label="系统"
              aria-expanded={openToolGroup === 'system'}
              aria-haspopup="menu"
            >
              <Cpu className={`chatos-sidebar-tool-icon ${modelDiagnosticStatus === 'simulate_offline' ? 'text-rose-400' : 'text-emerald-400'}`} />
              {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">系统</span>}
            </button>
            {openToolGroup === 'system' && (
              <div className="chatos-sidebar-tool-menu" role="menu" aria-label="系统工具">
                <button id="sidebar-sync-btn" role="menuitem" className="chatos-sidebar-menu-item" onClick={() => { runIncrementalOrganize(); setOpenToolGroup(null); }} disabled={isSyncing} aria-busy={isSyncing}>
                  <RefreshCw className={`w-4 h-4 text-indigo-400 ${isSyncing ? 'animate-spin' : ''}`} /><span>{isSyncing ? '整理中' : '增量整理'}</span>
                </button>
                <button id="aux-audit-btn" role="menuitem" className="chatos-sidebar-menu-item" onClick={() => { setActiveAuxModal('audit'); setOpenToolGroup(null); }}>
                  <FileCheck2 className="w-4 h-4" /><span>整理审计</span>
                </button>
                <button id="aux-engine-btn" role="menuitem" className="chatos-sidebar-menu-item" onClick={() => { setActiveAuxModal('engine'); setOpenToolGroup(null); }}>
                  <Cpu className="w-4 h-4" /><span>模型状态</span>
                </button>
                <button
                  id="alert-motion-toggle"
                  role="menuitemcheckbox"
                  aria-checked={isAlertMotionEnabled}
                  className="chatos-sidebar-menu-item"
                  onClick={() => setIsAlertMotionEnabled(!isAlertMotionEnabled)}
                  title="只控制顶部提醒弹幕；不会更改系统无障碍设置"
                >
                  <MoveHorizontal className="w-4 h-4" />
                  <span>允许弹幕滚动</span>
                  {isAlertMotionEnabled && <Check className="ml-auto w-4 h-4 text-emerald-400" aria-hidden="true" />}
                </button>
              </div>
            )}
          </div>

          <div className="chatos-sidebar-tool-group">
            <button
              id="sidebar-view-btn"
              onClick={() => {
                setActiveWorkspace('conversations');
                setOpenToolGroup(current => current === 'view' && activeWorkspace === 'conversations' ? null : 'view');
              }}
              className="chatos-sidebar-tool"
              title="会话筛选与显示密度"
              aria-label="视图"
              aria-expanded={openToolGroup === 'view'}
              aria-haspopup="dialog"
            >
              <Layers className="chatos-sidebar-tool-icon" />
              {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">视图</span>}
            </button>
            <div
              className="chatos-sidebar-tool-menu chatos-sidebar-view-menu"
              role="dialog"
              aria-label="会话视图"
              hidden={openToolGroup !== 'view'}
            >
              <div id="conversation-toolbar-host" />
            </div>
          </div>
        </div>

        {/* User Profile Card */}
        <div className="flex items-center gap-2.5 p-2 rounded-lg bg-[#111724] border border-[#1e293b]">
          <div className="relative flex-shrink-0">
            <img
              src={CURRENT_USER.avatar}
              alt={CURRENT_USER.name}
              className="w-8 h-8 rounded-full object-cover ring-1 ring-indigo-500/50"
            />
            <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-emerald-500 rounded-full ring-2 ring-[#0d121c]" />
          </div>

          {!isSidebarCollapsed && (
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-semibold text-slate-200 truncate">{CURRENT_USER.name}</span>
                <ShieldCheck className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
              </div>
              <p className="text-[10px] text-slate-400 truncate">演示身份 · 未绑定账号</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};
