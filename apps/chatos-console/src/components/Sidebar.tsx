import React, { useState } from 'react';
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
  Star,
  Users,
  ChevronDown,
  ChevronRight,
  Brain,
} from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';
import { WorkspaceType } from '../types';
import { CURRENT_USER } from '../mockData';

interface SidebarProps {
  onOpenThinking: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ onOpenThinking }) => {
  const {
    activeWorkspace,
    setActiveWorkspace,
    isSidebarCollapsed,
    setIsSidebarCollapsed,
    conversations,
    todos,
    projects,
    knowledgeItems,
    runIncrementalOrganize,
    isSyncing,
    setActiveAuxModal,
    modelDiagnosticStatus,
    selectedConversation,
    setSelectedConversation,
    setIsInspectorOpen,
    toggleFollowConversation,
    markConversationAsRead,
  } = useChatOS();

  const [isStarredOpen, setIsStarredOpen] = useState<boolean>(true);
  const starredConversations = conversations.filter(c => c.isFollowed);

  const unreadMessagesCount = conversations.reduce((acc, c) => acc + c.unreadCount, 0);
  const activeTodosCount = todos.filter(t => t.status !== 'completed' && !t.isIgnored).length;
  const activeProjectsCount = projects.filter(p => p.status === 'active').length;
  const pendingLibraryCount = knowledgeItems.filter(k => k.intakeStatus === 'pending_confirmation' || k.intakeStatus === 'pending_fetch').length;

  const navItems: { type: WorkspaceType; label: string; icon: React.FC<{ className?: string }>; badge?: number; badgeColor?: string }[] = [
    {
      type: 'conversations',
      label: '会话',
      icon: MessageSquare,
      badge: unreadMessagesCount > 0 ? unreadMessagesCount : undefined,
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
    {
      type: 'mindmap',
      label: '思维图谱',
      icon: Network,
      badgeColor: 'bg-purple-500/20 text-purple-300 border border-purple-500/30',
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
        <div className="flex items-center justify-between px-3.5 py-4 border-b border-[#1e293b]/70">
          {!isSidebarCollapsed && (
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-indigo-700 flex items-center justify-center font-bold text-white shadow-md shadow-indigo-900/40">
                C
              </div>
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

          {isSidebarCollapsed && (
            <div className="w-8 h-8 mx-auto rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white text-sm">
              C
            </div>
          )}

          <button
            id="sidebar-toggle-btn"
            onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
            className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-[#192233] rounded-md transition-colors"
            title={isSidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
            aria-label="Toggle Sidebar"
          >
            {isSidebarCollapsed ? (
              <PanelLeftOpen className="w-4 h-4" />
            ) : (
              <PanelLeftClose className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* Core Workspaces */}
        <nav className="chatos-sidebar-nav" aria-label="主工作区">
          {navItems.map(item => {
            const Icon = item.icon;
            const isActive = activeWorkspace === item.type;

            return (
              <button
                key={item.type}
                id={`nav-${item.type}`}
                onClick={() => setActiveWorkspace(item.type)}
                className={`chatos-sidebar-nav-item${isActive ? ' is-active' : ''}`}
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
            );
          })}
        </nav>

        {/* Starred Conversations Section (星标会话) */}
        {!isSidebarCollapsed ? (
          <div className="px-2 mt-3 flex-1 min-h-0 flex flex-col">
            <div className="flex items-center justify-between px-2 py-1.5 text-xs font-semibold text-slate-400">
              <button
                id="sidebar-starred-toggle-btn"
                onClick={() => setIsStarredOpen(!isStarredOpen)}
                className="flex items-center gap-1.5 text-slate-300 hover:text-white transition-colors"
                title="折叠/展开星标会话"
              >
                <Star className="w-3.5 h-3.5 text-amber-400 fill-amber-400" />
                <span>星标会话</span>
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 font-semibold">
                  {starredConversations.length}
                </span>
                {isStarredOpen ? (
                  <ChevronDown className="w-3 h-3 text-slate-500" />
                ) : (
                  <ChevronRight className="w-3 h-3 text-slate-500" />
                )}
              </button>
            </div>

            {isStarredOpen && (
              <div className="mt-1 space-y-0.5 overflow-y-auto max-h-52 pr-1">
                {starredConversations.length === 0 ? (
                  <div className="px-3 py-2 text-[11px] text-slate-500 italic bg-[#101522] rounded-lg border border-[#1b2334] text-center">
                    主会话右上角点击 ⭐ 即可加入星标
                  </div>
                ) : (
                  starredConversations.map(conv => {
                    const isSelected = selectedConversation?.id === conv.id && activeWorkspace === 'conversations';
                    return (
                      <div
                        key={conv.id}
                        id={`sidebar-starred-${conv.id}`}
                        onClick={() => {
                          setSelectedConversation(conv);
                          setActiveWorkspace('conversations');
                          setIsInspectorOpen(true);
                          markConversationAsRead(conv.id);
                        }}
                        className={`group flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition-all ${
                          isSelected
                            ? 'bg-indigo-600/90 text-white font-medium shadow-sm'
                            : 'text-slate-300 hover:text-white hover:bg-[#161d2d]'
                        }`}
                        title={`点击进入「${conv.name}」`}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <div className="relative flex-shrink-0">
                            {conv.isGroup ? (
                              <div className="w-5 h-5 rounded-md bg-[#1d273a] flex items-center justify-center border border-[#2d3d5c]">
                                <Users className="w-3 h-3 text-indigo-400" />
                              </div>
                            ) : (
                              <img
                                src={conv.avatar}
                                alt={conv.name}
                                className="w-5 h-5 rounded-full object-cover ring-1 ring-slate-700"
                              />
                            )}
                            {conv.unreadCount > 0 && (
                              <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-indigo-500 ring-1 ring-[#0b0f17]" />
                            )}
                          </div>
                          <span className="truncate text-xs">{conv.name}</span>
                        </div>

                        <div className="flex items-center gap-1 flex-shrink-0">
                          {conv.unreadCount > 0 && (
                            <span className="text-[10px] font-mono font-bold px-1 rounded bg-indigo-500/20 text-indigo-300">
                              {conv.unreadCount}
                            </span>
                          )}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleFollowConversation(conv.id);
                            }}
                            className="opacity-0 group-hover:opacity-100 p-0.5 text-amber-400 hover:text-rose-400 transition-opacity"
                            title="取消星标"
                          >
                            <Star className="w-3 h-3 fill-amber-400" />
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="px-2 mt-3 flex flex-col items-center">
            <button
              id="sidebar-collapsed-starred-btn"
              onClick={() => {
                setActiveWorkspace('conversations');
              }}
              className="relative p-2 rounded-lg text-amber-400 hover:bg-[#161d2d] transition-colors"
              title={`星标会话 (${starredConversations.length}个)`}
            >
              <Star className="w-5 h-5 fill-amber-400" />
              {starredConversations.length > 0 && (
                <span className="absolute -top-0.5 -right-0.5 text-[9px] font-bold font-mono px-1 rounded-full bg-amber-500 text-slate-950">
                  {starredConversations.length}
                </span>
              )}
            </button>
          </div>
        )}

      </div>

      {/* Bottom User Card & Aux Tools */}
      <div className="p-2 border-t border-[#1e293b]/70 space-y-2">
        {/* Aux Tools Trigger Buttons */}
        <div className={`chatos-sidebar-tools${isSidebarCollapsed ? ' is-collapsed' : ''}`}>
          <button
            id="sidebar-sync-btn"
            onClick={() => runIncrementalOrganize()}
            disabled={isSyncing}
            className="chatos-sidebar-tool"
            title="检查近90天关系与30天待办整理服务的接入状态"
            aria-label={isSyncing ? '正在增量整理' : '增量整理'}
            aria-busy={isSyncing}
          >
            <RefreshCw className={`chatos-sidebar-tool-icon text-indigo-400 ${isSyncing ? 'animate-spin' : ''}`} />
            {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">{isSyncing ? '整理中' : '整理'}</span>}
          </button>

          <button
            id="aux-audit-btn"
            onClick={() => setActiveAuxModal('audit')}
            className="chatos-sidebar-tool"
            title="整理审计与判定追溯"
            aria-label="整理审计"
          >
            <FileCheck2 className="chatos-sidebar-tool-icon" />
            {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">审计</span>}
          </button>

          <button
            id="aux-graph-btn"
            onClick={() => setActiveAuxModal('graph')}
            className="chatos-sidebar-tool"
            title="90天联系强度图谱"
            aria-label="联系图谱"
          >
            <GitFork className="chatos-sidebar-tool-icon" />
            {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">图谱</span>}
          </button>

          <button
            id="aux-engine-btn"
            onClick={() => setActiveAuxModal('engine')}
            className="chatos-sidebar-tool"
            title="模型诊断与处理状态"
            aria-label="模型状态"
          >
            <Cpu
              className={`chatos-sidebar-tool-icon ${
                modelDiagnosticStatus === 'simulate_offline'
                  ? 'text-rose-400'
                  : 'text-emerald-400'
              }`}
            />
            {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">状态</span>}
          </button>

          <button
            id="sidebar-thinking-trigger-btn"
            onClick={onOpenThinking}
            className="chatos-sidebar-tool"
            title="Thinking Mode 深度推演"
            aria-label="深度推演"
          >
            <Brain className="chatos-sidebar-tool-icon" />
            {!isSidebarCollapsed && <span className="chatos-sidebar-tool-label">推演</span>}
          </button>
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
