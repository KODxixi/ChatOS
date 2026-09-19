import React, { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import {
  Star,
  Users,
  Clock,
  CheckCircle2,
  FileText,
  AlertTriangle,
  TrendingUp,
  Search,
  MessageSquare,
  Layers,
  Database,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { Conversation } from '../../types';
import {
  buildConversationFeatureFilters,
  filterConversationsByGroup,
  getConversationRelationshipHalo,
  getConversationAttentionReason,
  selectDailyFocusConversations,
  selectDailyPriorityConversations,
  selectConversationAlerts,
  selectPulsingImportantConversationIds,
  type ConversationFeatureFilterId,
} from '../../core/conversationFeatureFilters';

interface ConversationsViewProps {
  mode: 'focus' | 'all';
}

export const ConversationsView: React.FC<ConversationsViewProps> = ({ mode: workspaceMode }) => {
  const {
    setActiveWorkspace,
    conversations,
    selectedConversation,
    setSelectedConversation,
    setSelectedProject,
    setSelectedTodo,
    setSelectedKnowledgeItem,
    setIsInspectorOpen,
    toggleFollowConversation,
    markConversationAsRead,
    jumpToMessage,
    projects,
    conversationDataSource,
    conversationDataUpdatedAt,
    conversationTotal,
    isAlertMotionEnabled,
    activeConversationGroupId,
    setActiveConversationGroupId,
    seenImportantMessageSignatures,
  } = useChatOS();

  // Tab 1: Chat type filter (User request: 选项卡或下拉菜单筛选仅私信和仅群聊)
  const [chatTypeFilter, setChatTypeFilter] = useState<'all' | 'private' | 'group'>('all');

  // Modifier filter
  const [activeFilter, setActiveFilter] = useState<ConversationFeatureFilterId>('all');

  // Card display level (User request: 紧凑 / 标准 / 舒适 三种显示层级)
  const [cardDensity, setCardDensity] = useState<'compact' | 'standard' | 'comfortable'>('standard');

  // Search filter for handling hundreds/thousands of contacts
  const [searchKeywords, setSearchKeywords] = useState<string>('');

  // Slicing/pagination limit for massive 1000 contacts scale
  const [visibleLimit, setVisibleLimit] = useState<number>(60);
  const [toolbarHost, setToolbarHost] = useState<HTMLElement | null>(null);

  React.useEffect(() => {
    setToolbarHost(document.getElementById('conversation-toolbar-host'));
  }, []);

  // Reset limit when filters change
  React.useEffect(() => {
    setVisibleLimit(60);
  }, [workspaceMode, chatTypeFilter, activeFilter, searchKeywords, activeConversationGroupId]);

  // Counts for tabs
  const totalCount = conversations.length;
  const privateCount = useMemo(() => conversations.filter(c => !c.isGroup).length, [conversations]);
  const groupCount = useMemo(() => conversations.filter(c => c.isGroup).length, [conversations]);
  const featureFilters = useMemo(() => buildConversationFeatureFilters(conversations), [conversations]);
  const dailyFocusConversations = useMemo(
    () => conversationDataSource === 'loading' ? [] : selectDailyFocusConversations(conversations),
    [conversationDataSource, conversations]
  );
  const dailyPriorityConversations = useMemo(
    () => selectDailyPriorityConversations(dailyFocusConversations),
    [dailyFocusConversations]
  );
  const orderedDailyConversations = useMemo(() => {
    const primaryIds = new Set(dailyPriorityConversations.map(conversation => conversation.id));
    return [
      ...dailyPriorityConversations,
      ...dailyFocusConversations.filter(conversation => !primaryIds.has(conversation.id)),
    ];
  }, [dailyFocusConversations, dailyPriorityConversations]);
  const alertConversations = useMemo(() => selectConversationAlerts(conversations), [conversations]);
  const selectedFeature = featureFilters.find(filter => filter.id === activeFilter) || featureFilters[0];

  React.useEffect(() => {
    if (!featureFilters.some(filter => filter.id === activeFilter)) {
      setActiveFilter('all');
    }
  }, [activeFilter, featureFilters]);

  // Sort conversations by priorityScore
  const sortedConversations = useMemo(() => {
    return [...conversations].sort((a, b) => b.priorityScore - a.priorityScore);
  }, [conversations]);
  const displayedConversations = workspaceMode === 'focus'
    ? orderedDailyConversations
    : filterConversationsByGroup(sortedConversations, activeConversationGroupId);
  const effectiveDensity = workspaceMode === 'focus' ? 'comfortable' : cardDensity;

  // Filter conversations
  const filteredConversations = useMemo(() => {
    return displayedConversations.filter(c => {
      // 1. Chat type tab filter
      if (chatTypeFilter === 'private' && c.isGroup) return false;
      if (chatTypeFilter === 'group' && !c.isGroup) return false;

      // 2. Modifier filter
      if (workspaceMode !== 'focus' && selectedFeature && !selectedFeature.matches(c)) return false;

      // 3. Search query
      if (searchKeywords.trim()) {
        const query = searchKeywords.toLowerCase().trim();
        const matchesName = c.name.toLowerCase().includes(query);
        const matchesRole = (c.roleTitle || '').toLowerCase().includes(query);
        const matchesTopic = (c.groupTopic || '').toLowerCase().includes(query);
        const matchesSummary = c.twoLineSummary.toLowerCase().includes(query);
        const matchesReason = c.priorityReason.toLowerCase().includes(query);
        if (!matchesName && !matchesRole && !matchesTopic && !matchesSummary && !matchesReason) {
          return false;
        }
      }

      return true;
    });
  }, [displayedConversations, workspaceMode, chatTypeFilter, selectedFeature, searchKeywords]);

  // Sliced conversations for smooth rendering
  const visibleConversations = useMemo(() => {
    return filteredConversations.slice(0, visibleLimit);
  }, [filteredConversations, visibleLimit]);

  const handleSelect = (conv: Conversation) => {
    setSelectedConversation(conv);
    setSelectedProject(null);
    setSelectedTodo(null);
    setSelectedKnowledgeItem(null);
    setIsInspectorOpen(true);
    markConversationAsRead(conv.id);
  };

  const handleDragStart = (event: React.DragEvent<HTMLDivElement>, conv: Conversation) => {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('application/x-chatos-conversation', conv.id);
    event.dataTransfer.setData('text/plain', conv.id);
  };

  const handleCardKeyDown = (event: React.KeyboardEvent<HTMLDivElement>, conv: Conversation) => {
    if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    handleSelect(conv);
  };

  /**
   * Helper: Render Avatar based on conversation type (Single avatar for 1v1, Stacked avatars for Groups)
   */
  const renderCardAvatar = (conv: Conversation, density: 'compact' | 'standard' | 'comfortable') => {
    const relationship = getConversationRelationshipHalo(conv);
    const relationshipAttributes = {
      'data-relationship-kind': relationship.kind,
      'data-relationship-tone': relationship.tone,
      'data-relationship-score': relationship.score,
      title: relationship.label,
    };

    if (!conv.isGroup) {
      // 私信卡片：单头像；不使用数据库不存在的在线状态。
      const sizeClasses = {
        compact: 'w-8 h-8',
        standard: 'w-10 h-10',
        comfortable: 'w-12 h-12',
      }[density];

      return (
        <div className="flex-shrink-0">
          <img
            {...relationshipAttributes}
            src={conv.avatar || 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=60'}
            alt={conv.name}
            className={`${sizeClasses} relationship-avatar-halo rounded-full object-cover`}
          />
        </div>
      );
    }

    // 群聊卡片：堆叠头像标识 (Stacked Avatars Stack)
    const participants = conv.groupParticipants || [];
    const memberCount = conv.memberCount || Math.max(3, participants.length);

    const avatarSize = {
      compact: 'w-6 h-6',
      standard: 'w-7 h-7',
      comfortable: 'w-8 h-8',
    }[density];

    const badgeSize = {
      compact: 'w-6 h-6 text-[8px]',
      standard: 'w-7 h-7 text-[9px]',
      comfortable: 'w-8 h-8 text-[10px]',
    }[density];

    const spacing = {
      compact: '-space-x-2',
      standard: '-space-x-2.5',
      comfortable: '-space-x-3',
    }[density];

    return (
      <div className="flex items-center flex-shrink-0">
        <div className={`flex items-center ${spacing}`}>
          {participants.length > 0 ? (
            participants.slice(0, 2).map((p, idx) => (
              <img
                key={p.id || idx}
                {...relationshipAttributes}
                src={p.avatar}
                alt={p.name}
                className={`${avatarSize} relationship-avatar-halo rounded-full object-cover`}
                style={{ zIndex: 10 - idx }}
              />
            ))
          ) : (
            <div {...relationshipAttributes} className={`${avatarSize} relationship-avatar-halo rounded-full bg-indigo-900 flex items-center justify-center text-indigo-300 z-10`}>
              <Users className="w-3 h-3" />
            </div>
          )}
          {/* Stacked count pill badge */}
          <div
            className={`${badgeSize} rounded-full bg-indigo-600 text-white font-bold flex items-center justify-center ring-2 ring-[#131728] shadow-sm z-[5] flex-shrink-0`}
            title={`群聊成员共 ${memberCount} 人`}
          >
            +{Math.max(1, memberCount - Math.min(2, participants.length))}
          </div>
        </div>
      </div>
    );
  };

  const primaryConversationIds = useMemo(
    () => new Set(dailyPriorityConversations.map(conversation => conversation.id)),
    [dailyPriorityConversations]
  );
  const pulsingConversationIds = useMemo(
    () => new Set(selectPulsingImportantConversationIds(visibleConversations, seenImportantMessageSignatures)),
    [seenImportantMessageSignatures, visibleConversations]
  );

  const getCardStyle = (conv: Conversation, isSelected: boolean, isPrimary: boolean) =>
    `conversation-card-surface ${isPrimary ? 'gary-glass gary-regular is-primary' : 'gary-solid-plate is-secondary'} ${conv.isGroup ? 'is-group' : 'is-direct'}${isSelected ? ' is-selected' : ''}${pulsingConversationIds.has(conv.id) ? ' has-unseen-important' : ''}`;

  const renderAlertCopy = (duplicate = false) => (
    <div className="conversation-alert-copy" aria-hidden={duplicate || undefined}>
      {alertConversations.map(conv => {
        const reminder = conv.topActionTodo
          ? `${conv.topActionTodo.title} · 截止 ${conv.topActionTodo.dueDate}`
          : conv.priorityReason || '星标重要会话';
        const content = (
          <>
            <strong>{conv.name}</strong>
            <span aria-hidden="true">·</span>
            <span>{reminder}</span>
          </>
        );

        return duplicate ? (
          <span
            key={conv.id}
            className="conversation-alert-item"
          >
            {content}
          </span>
        ) : (
          <button
            key={conv.id}
            type="button"
            className="conversation-alert-item"
            onClick={() => handleSelect(conv)}
            title={`打开 ${conv.name}：${reminder}`}
          >
            {content}
          </button>
        );
      })}
    </div>
  );

  return (
    <div className="conversation-view flex-1 flex flex-col overflow-y-auto p-6 pt-4 space-y-4">
      {/* Conversation filters stay expanded under the sidebar conversation entry. */}
      {workspaceMode === 'all' && toolbarHost && createPortal(
      <div className="conversation-toolbar" data-mode={workspaceMode}>
        <div id="conversation-options-panel" className="conversation-options-panel">
                <div className="conversation-control-module conversation-search-module relative">
                  <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3.5 pointer-events-none" />
                  <input
                    id="conv-search-input"
                    type="text"
                    value={searchKeywords}
                    onChange={event => setSearchKeywords(event.target.value)}
                    placeholder="搜索联系人、群聊或摘要"
                    aria-label="搜索会话"
                    className="w-full min-h-11 pl-9 pr-8 rounded-lg bg-black/20 border border-white/10 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  />
                  {searchKeywords && <button type="button" onClick={() => setSearchKeywords('')} className="absolute right-2.5 top-3 text-slate-500 hover:text-slate-300" aria-label="清除搜索">✕</button>}
                </div>

                <label className="conversation-option-field">
                  <span>会话类型</span>
                  <select id="conversation-type-filter" value={chatTypeFilter} onChange={event => setChatTypeFilter(event.target.value as 'all' | 'private' | 'group')}>
                    <option value="all">全部 · {totalCount}</option>
                    <option value="private">私信 · {privateCount}</option>
                    <option value="group">群聊 · {groupCount}</option>
                  </select>
                </label>

                <label className="conversation-option-field">
                  <span>特征</span>
                  <select id="conversation-feature-filter" value={activeFilter} onChange={event => setActiveFilter(event.target.value as ConversationFeatureFilterId)}>
                    {featureFilters.map(filter => <option key={filter.id} value={filter.id}>{filter.label} · {filter.count}</option>)}
                  </select>
                </label>

                <label className="conversation-option-field conversation-density-module">
                  <span className="inline-flex items-center gap-1"><Layers className="w-3.5 h-3.5" />显示密度</span>
                  <select id="conversation-density-select" value={cardDensity} onChange={event => setCardDensity(event.target.value as 'compact' | 'standard' | 'comfortable')}>
                    <option value="compact">紧凑</option>
                    <option value="standard">标准</option>
                    <option value="comfortable">舒适</option>
                  </select>
                </label>

                <div className="conversation-source-meta">
                  <Database className="w-3.5 h-3.5" />
                  <span>{conversationDataSource === 'database' ? `本地数据库 · ${conversationTotal} 个会话` : conversationDataSource === 'loading' ? '正在读取本地数据库' : `原型数据 · ${conversationTotal} 个会话`}</span>
                  {conversationDataSource === 'database' && conversationDataUpdatedAt && <span title={conversationDataUpdatedAt}>自动刷新</span>}
                </div>
        </div>
      </div>,
      toolbarHost
      )}

      {workspaceMode === 'all' && <section className="conversation-alert-rail" aria-label="待办与重要信息提醒">
        <div className="conversation-alert-viewport">
          {alertConversations.length > 0 ? (
            <div className={`conversation-alert-track${alertConversations.length > 1 ? ' is-moving' : ''}${isAlertMotionEnabled ? ' is-motion-allowed' : ''}`}>
              {renderAlertCopy()}
              {alertConversations.length > 1 && renderAlertCopy(true)}
            </div>
          ) : (
            <p className="conversation-alert-empty">当前没有挂起待办或星标提醒</p>
          )}
        </div>
      </section>}

      {/* Empty State */}
      {filteredConversations.length === 0 && (
        <div className="p-12 text-center rounded-xl bg-[#101522] border border-[#1e293b]">
          <MessageSquare className="w-8 h-8 text-slate-500 mx-auto mb-3" />
          <p className="text-sm font-medium text-slate-300">
            {conversationDataSource === 'loading'
              ? '正在读取本地会话'
              : workspaceMode === 'focus'
                ? '当前没有已星标或含挂起事项的会话'
                : '没有符合筛选条件的会话'}
          </p>
          <p className="text-xs text-slate-500 mt-1">
            {workspaceMode === 'focus' ? '不会用消息量或推测的本人责任填充首页' : '请调整筛选或重置搜索词'}
          </p>
          {conversationDataSource !== 'loading' && <button
            onClick={() => {
              if (workspaceMode === 'focus') setActiveWorkspace('conversations');
              setActiveConversationGroupId('all');
              setChatTypeFilter('all');
              setActiveFilter('all');
              setSearchKeywords('');
            }}
            className="mt-4 min-h-11 px-3 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-500 transition-colors"
          >
            {workspaceMode === 'focus' ? '查看全部会话' : '重置所有筛选'}
          </button>}
        </div>
      )}

      {/* ========================================================================= */}
      {/* LEVEL 1: COMPACT (紧凑模式)                                               */}
      {/* High-density grid, low card height (~68px), for massive directory scan   */}
      {/* ========================================================================= */}
      {effectiveDensity === 'compact' && filteredConversations.length > 0 && (
        <div className="conversation-grid conversation-grid--compact grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-2.5">
          {visibleConversations.map(conv => {
            const isSelected = selectedConversation?.id === conv.id;

            return (
              <div
                key={conv.id}
                id={`conv-card-compact-${conv.id}`}
                role="button"
                tabIndex={0}
                draggable
                onDragStart={event => handleDragStart(event, conv)}
                onClick={() => handleSelect(conv)}
                onKeyDown={event => handleCardKeyDown(event, conv)}
                aria-label={`打开会话：${conv.name}`}
                className={`p-2.5 rounded-lg border transition-all cursor-pointer group flex items-center justify-between gap-2.5 select-none ${getCardStyle(
                  conv,
                  isSelected,
                  primaryConversationIds.has(conv.id)
                )}`}
              >
                {/* Left: Avatar (Single vs Stacked) + Name */}
                <div className="conversation-card-identity flex items-center gap-2.5 min-w-0 flex-1">
                  {renderCardAvatar(conv, 'compact')}

                  <div className="min-w-0 flex-1">
                    <div className="conversation-card-name-row">
                      <span className="conversation-card-name text-xs font-semibold text-slate-200 group-hover:text-indigo-300" title={conv.name}>
                        {conv.name}
                      </span>
                    </div>

                    <p className="text-[10px] text-slate-400 truncate mt-0.5">
                      {conv.isGroup
                        ? (conv.groupTopic || '群聊讨论')
                        : (conv.roleTitle || `90天互动 ${conv.interactionDays90d} 天`)}
                    </p>
                  </div>
                </div>

                {/* Right: Badge / Unread / Star Toggle */}
                <div className="conversation-card-actions flex items-center gap-1 flex-shrink-0">
                  {conv.unreadCount > 0 && (
                    <span className="text-[10px] font-mono font-bold px-1.5 py-0.2 rounded-full bg-indigo-600 text-white">
                      {conv.unreadCount}
                    </span>
                  )}
                  {conv.topActionTodo && (
                    <span
                      className="w-2 h-2 rounded-full bg-amber-400 ring-1 ring-amber-300"
                      title={`最需行动待办: ${conv.topActionTodo.title}`}
                    />
                  )}
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      toggleFollowConversation(conv.id);
                    }}
                    className={`chatos-icon-button transition-colors ${
                      conv.isFollowed
                        ? 'text-amber-400'
                        : 'text-slate-600 opacity-0 group-hover:opacity-100 hover:text-amber-400'
                    }`}
                    title={conv.isFollowed ? '从左侧星标移除' : '固定至左侧星标'}
                  >
                    <Star className={`w-3 h-3 ${conv.isFollowed ? 'fill-amber-400' : ''}`} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ========================================================================= */}
      {/* LEVEL 2: STANDARD (标准模式)                                              */}
      {/* Balanced layout, medium card height (~145px), topic/role + 1-line summary */}
      {/* ========================================================================= */}
      {effectiveDensity === 'standard' && filteredConversations.length > 0 && (
        <div className="conversation-grid conversation-grid--standard grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3.5">
          {visibleConversations.map(conv => {
            const isSelected = selectedConversation?.id === conv.id;

            return (
              <div
                key={conv.id}
                id={`conv-card-std-${conv.id}`}
                role="button"
                tabIndex={0}
                draggable
                onDragStart={event => handleDragStart(event, conv)}
                onClick={() => handleSelect(conv)}
                onKeyDown={event => handleCardKeyDown(event, conv)}
                aria-label={`打开会话：${conv.name}`}
                className={`p-3.5 rounded-xl border transition-all cursor-pointer group flex flex-col justify-between ${getCardStyle(
                  conv,
                  isSelected,
                  primaryConversationIds.has(conv.id)
                )}`}
              >
                <div>
                  {/* Top Bar: Single vs Stacked Avatar, Name, Badges, Star */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="conversation-card-identity flex items-center gap-3 min-w-0 flex-1">
                      {renderCardAvatar(conv, 'standard')}

                      <div className="min-w-0 flex-1">
                        <div className="conversation-card-name-row">
                          <h3 className="conversation-card-name text-xs font-semibold text-slate-100 group-hover:text-indigo-300" title={conv.name}>
                            {conv.name}
                          </h3>
                        </div>

                        <p className="text-[11px] text-slate-400 truncate mt-0.5">
                          {workspaceMode === 'focus'
                            ? getConversationAttentionReason(conv)
                            : conv.isGroup
                              ? (conv.groupTopic || '群协作攻坚')
                              : (conv.roleTitle || `近90天互动 ${conv.interactionDays90d} 天`)}
                        </p>
                      </div>
                    </div>

                    <div className="conversation-card-actions flex items-center gap-1 flex-shrink-0">
                      {conv.unreadCount > 0 && (
                        <span className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-indigo-500/25 text-indigo-200 border border-indigo-500/40">
                          +{conv.unreadCount}
                        </span>
                      )}
                      <button
                        onClick={e => {
                          e.stopPropagation();
                          toggleFollowConversation(conv.id);
                        }}
                        className={`chatos-icon-button transition-colors ${
                          conv.isFollowed
                            ? 'text-amber-400'
                            : 'text-slate-500 hover:text-amber-400'
                        }`}
                        title={conv.isFollowed ? '从左侧星标移除' : '固定至左侧星标'}
                      >
                        <Star className={`w-3.5 h-3.5 ${conv.isFollowed ? 'fill-amber-400' : ''}`} />
                      </button>
                    </div>
                  </div>

                  {/* One-line context; details stay in the inspector. */}
                  <p className="mt-2.5 text-xs text-slate-300 line-clamp-1 leading-relaxed">
                    {workspaceMode === 'focus' ? (conv.topActionTodo?.title || conv.twoLineSummary) : conv.twoLineSummary}
                  </p>
                </div>

                {/* Bottom Action & Priority Indicator */}
                {workspaceMode === 'all' && <div className="mt-3 pt-2.5 border-t border-[#1e283d] flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-1.5 truncate">
                    {conv.topActionTodo ? (
                      <span className="text-amber-400 flex items-center gap-1 truncate font-medium">
                        <AlertTriangle className="w-3 h-3 text-amber-400 flex-shrink-0" />
                        <span className="truncate">{conv.topActionTodo.title}</span>
                      </span>
                    ) : (
                      <span className="text-slate-500 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3 text-emerald-500/70" />
                        <span>暂无挂起待办</span>
                      </span>
                    )}
                  </div>

                  <span className="text-[10px] font-mono text-slate-400 flex-shrink-0 ml-2">
                    评分 {conv.priorityScore}
                  </span>
                </div>}
              </div>
            );
          })}
        </div>
      )}

      {/* ========================================================================= */}
      {/* LEVEL 3: COMFORTABLE (舒适模式 - 全量摘要与深度行动)                       */}
      {/* Full analytical cards with AI reasoning, 2-line summary, action todo card */}
      {/* ========================================================================= */}
      {effectiveDensity === 'comfortable' && filteredConversations.length > 0 && (
        <div className="conversation-grid conversation-grid--comfortable grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {visibleConversations.map(conv => {
            const isSelected = selectedConversation?.id === conv.id;
            const linkedProjectsList = projects.filter(p => conv.linkedProjectIds.includes(p.id));

            return (
              <div
                key={conv.id}
                id={`conv-card-${conv.id}`}
                role="button"
                tabIndex={0}
                draggable
                onDragStart={event => handleDragStart(event, conv)}
                onClick={() => handleSelect(conv)}
                onKeyDown={event => handleCardKeyDown(event, conv)}
                aria-label={`打开会话：${conv.name}`}
                className={`relative flex flex-col justify-between p-4 rounded-xl transition-all duration-150 cursor-pointer group select-none ${getCardStyle(
                  conv,
                  isSelected,
                  primaryConversationIds.has(conv.id)
                )}`}
              >
                {/* Card Header: Avatar, Name, Priority Reason */}
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="conversation-card-identity flex items-center gap-3 min-w-0 flex-1">
                      {renderCardAvatar(conv, 'comfortable')}

                      {/* Name & Role / Topic Info */}
                      <div className="min-w-0 flex-1">
                        <div className="conversation-card-name-row">
                          <h3 className="conversation-card-name text-sm font-semibold text-slate-100 group-hover:text-indigo-300 transition-colors" title={conv.name}>
                            {conv.name}
                          </h3>
                        </div>

                        {/* Distinct sub-lines */}
                        <div className="mt-0.5 text-[11px] text-slate-400 truncate">
                          {conv.isGroup ? (
                            <span className="text-indigo-300/80 font-medium truncate">
                              {conv.groupTopic || '#团队协作'}
                            </span>
                          ) : (
                            <span className="text-emerald-300/80 font-medium truncate">
                              {conv.roleTitle || '核心业务对齐人'}
                            </span>
                          )}
                        </div>

                        <div className="conversation-card-meta flex items-center gap-2 mt-0.5 text-[10px] text-slate-500">
                          <span className="flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {conv.lastActiveTime}
                          </span>
                          <span>·</span>
                          {conv.isGroup ? (
                            <span>本人参与率 {conv.joyBoyParticipationScore}%</span>
                          ) : (
                            <span>90天双向互动 {conv.interactionDays90d} 天</span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Actions & Unread Watermark */}
                    <div className="conversation-card-actions flex items-center gap-1.5 flex-shrink-0">
                      {conv.unreadCount > 0 && (
                        <span
                          className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"
                          title="ChatOS 本地增量水位新消息"
                        >
                          +{conv.unreadCount}
                        </span>
                      )}

                      <button
                        id={`star-btn-${conv.id}`}
                        onClick={e => {
                          e.stopPropagation();
                          toggleFollowConversation(conv.id);
                        }}
                        className={`chatos-icon-button transition-colors ${
                          conv.isFollowed
                            ? 'text-amber-400'
                            : 'text-slate-500 hover:text-slate-300'
                        }`}
                        title={conv.isFollowed ? '从左侧星标移除' : '固定至左侧星标'}
                      >
                        <Star className={`w-3.5 h-3.5 ${conv.isFollowed ? 'fill-current' : ''}`} />
                      </button>
                    </div>
                  </div>

                  {/* Priority Reason Banner */}
                  <div className="mt-2.5 px-2.5 py-1 rounded bg-[#0e131e] border border-[#1b2334] flex items-center justify-between text-[11px]">
                    <span className="text-slate-400 truncate flex items-center gap-1">
                      <TrendingUp className="w-3 h-3 text-indigo-400 flex-shrink-0" />
                      <span className="truncate">{conv.priorityReason}</span>
                    </span>
                    <span className="font-mono text-slate-500 flex-shrink-0 ml-2">
                      评分 {conv.priorityScore}
                    </span>
                  </div>

                  {/* Two-line Summary */}
                  <p className="mt-3 text-xs text-slate-300 line-clamp-2 leading-relaxed">
                    {conv.twoLineSummary}
                  </p>

                  {/* Top Action Todo (Highlighting What Needs To Be Done) */}
                  {conv.topActionTodo ? (
                    <div
                      id={`card-todo-${conv.topActionTodo.id}`}
                      onClick={e => {
                        e.stopPropagation();
                        jumpToMessage(conv.id, conv.topActionTodo!.sourceMessageId);
                      }}
                      className="mt-3.5 p-2.5 rounded-lg bg-[#182033] border border-[#2a3754] hover:border-indigo-500/50 transition-colors"
                      title="点击跳转会话并定位该行动的消息来源"
                    >
                      <div className="flex items-center justify-between gap-1.5 text-[11px] mb-1">
                        <span className="font-medium text-amber-400 flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3 text-amber-400" />
                          最需行动待办
                        </span>
                        <span className="text-slate-400 font-mono text-[10px]">
                          截止: {conv.topActionTodo.dueDate}
                        </span>
                      </div>
                      <p className="text-xs font-medium text-slate-200 line-clamp-1">
                        {conv.topActionTodo.title}
                      </p>
                    </div>
                  ) : (
                    <div className="mt-3.5 py-2 px-2.5 rounded-lg bg-[#0e131e] border border-[#182132] text-[11px] text-slate-500 flex items-center gap-1.5">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                      <span>暂无紧急挂起待办</span>
                    </div>
                  )}
                </div>

                {/* Card Footer: Linked Projects & Related Resources Thumbnails */}
                <div className="mt-4 pt-3 border-t border-[#1d273a] flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 overflow-hidden">
                    {linkedProjectsList.length > 0 ? (
                      linkedProjectsList.map(proj => (
                        <span
                          key={proj.id}
                          className="text-[10px] font-medium px-2 py-0.5 rounded bg-[#182133] text-indigo-300 border border-[#283754] truncate max-w-[120px]"
                          title={`关联项目：${proj.name}`}
                        >
                          {proj.name}
                        </span>
                      ))
                    ) : (
                      <span className="text-[10px] text-slate-500">非项目专属会话</span>
                    )}
                  </div>

                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    {conv.relatedFilesPreview && conv.relatedFilesPreview.length > 0 ? (
                      conv.relatedFilesPreview.map(file => (
                        <div
                          key={file.id}
                          className="w-5 h-5 rounded bg-[#1a2336] border border-[#273652] flex items-center justify-center text-[10px] text-slate-300 overflow-hidden"
                          title={`资料：${file.fileName}`}
                        >
                          {file.thumbnailUrl ? (
                            <img src={file.thumbnailUrl} alt={file.fileName} className="w-full h-full object-cover" />
                          ) : (
                            <FileText className="w-3 h-3 text-slate-400" />
                          )}
                        </div>
                      ))
                    ) : (
                      <span className="text-[10px] text-slate-600">0 资料</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination & Infinite Scale Footer Bar for high volumes (e.g. 500 contacts) */}
      {workspaceMode === 'all' && filteredConversations.length > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl bg-[#101522] border border-[#1e293b] text-xs mt-2">
          <div className="flex items-center gap-2 text-slate-400">
            <span>
              已渲染显示 <span className="text-indigo-300 font-semibold">{visibleConversations.length}</span> / 共{' '}
              <span className="text-slate-200 font-semibold">{filteredConversations.length}</span> 位联系人与群聊
            </span>
            {visibleConversations.length < filteredConversations.length && (
              <span className="text-[11px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30">
                高性能切片防护中
              </span>
            )}
          </div>

          {visibleConversations.length < filteredConversations.length && (
            <div className="flex items-center gap-2">
              <button
                id="load-more-60-btn"
                onClick={() => setVisibleLimit(prev => Math.min(prev + 60, filteredConversations.length))}
                className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition-colors shadow-xs"
              >
                加载下 60 位
              </button>
              <button
                id="load-all-scale-btn"
                onClick={() => setVisibleLimit(filteredConversations.length)}
                className="px-3 py-1.5 rounded-lg bg-[#182133] hover:bg-[#202c44] text-indigo-300 border border-indigo-500/30 font-medium transition-colors"
              >
                一键全量展开 ({filteredConversations.length})
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
