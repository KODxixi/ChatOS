import type { Conversation } from '../types';

export type ConversationFeatureFilterId =
  | 'all'
  | 'followed'
  | 'has_todo'
  | 'unread'
  | 'project_linked'
  | 'high_interaction'
  | 'family_advisor'
  | 'active_collab'
  | 'noisy_group';

export type ConversationFeatureIcon =
  | 'all'
  | 'star'
  | 'todo'
  | 'unread'
  | 'project'
  | 'trend'
  | 'shield'
  | 'collaboration'
  | 'muted';

export interface ConversationFeatureFilter {
  id: ConversationFeatureFilterId;
  label: string;
  count: number;
  icon: ConversationFeatureIcon;
  matches: (conversation: Conversation) => boolean;
}

const percentile = (values: number[], ratio: number) => {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(sorted.length * ratio) - 1)];
};

export const buildConversationFeatureFilters = (
  conversations: Conversation[]
): ConversationFeatureFilter[] => {
  const highInteractionThreshold = percentile(
    conversations.map(item => item.interactionDays90d).filter(value => value > 0),
    0.75
  );

  const candidates: Omit<ConversationFeatureFilter, 'count'>[] = [
    { id: 'followed', label: '星标关注', icon: 'star', matches: item => item.isFollowed },
    { id: 'has_todo', label: '行动待办', icon: 'todo', matches: item => Boolean(item.topActionTodo) },
    { id: 'unread', label: '待读消息', icon: 'unread', matches: item => item.unreadCount > 0 },
    { id: 'project_linked', label: '项目关联', icon: 'project', matches: item => item.linkedProjectIds.length > 0 },
    {
      id: 'high_interaction',
      label: '高频互动',
      icon: 'trend',
      matches: item => highInteractionThreshold > 0 && item.interactionDays90d >= highInteractionThreshold,
    },
    {
      id: 'family_advisor',
      label: '亲友与顾问',
      icon: 'shield',
      matches: item => item.categoryTag === 'family_core' || item.categoryTag === 'low_freq_important',
    },
    {
      id: 'active_collab',
      label: '活跃协作',
      icon: 'collaboration',
      matches: item => item.categoryTag === 'active_collab',
    },
    {
      id: 'noisy_group',
      label: '低优先群',
      icon: 'muted',
      matches: item => item.categoryTag === 'noisy_group',
    },
  ];

  return [
    {
      id: 'all',
      label: '全部',
      icon: 'all',
      count: conversations.length,
      matches: () => true,
    },
    ...candidates.flatMap(candidate => {
      const count = conversations.reduce(
        (total, conversation) => total + Number(candidate.matches(conversation)),
        0
      );
      return count > 0 ? [{ ...candidate, count }] : [];
    }),
  ];
};

export const filterConversationsByFeature = (
  conversations: Conversation[],
  filterId: ConversationFeatureFilterId
) => {
  const filter = buildConversationFeatureFilters(conversations).find(item => item.id === filterId);
  return filter ? conversations.filter(filter.matches) : conversations;
};

export const filterConversationsByGroup = (
  conversations: Conversation[],
  groupId: string
) => conversations.filter(conversation => {
  if (groupId === 'archived') return Boolean(conversation.isArchived);
  if (conversation.isArchived) return false;
  if (groupId === 'all') return true;
  if (groupId === 'favorite') return conversation.isFollowed;
  return conversation.groupId === groupId;
});

export const shouldPulseImportantConversation = (
  conversation: Conversation,
  seenSignatures: Record<string, string>
) => {
  const todo = conversation.topActionTodo;
  if (!todo || (!todo.isUrgent && conversation.priorityScore < 80)) return false;
  return seenSignatures[conversation.id] !== getImportantConversationSignature(conversation);
};

export const getImportantConversationSignature = (conversation: Conversation) => {
  const todo = conversation.topActionTodo;
  if (!todo) return '';
  return [todo.id, todo.title, todo.dueDate || '', Number(todo.isUrgent)].join('|');
};

export const selectPulsingImportantConversationIds = (
  conversations: Conversation[],
  seenSignatures: Record<string, string>,
  limit = 3
) => conversations
  .filter(conversation => shouldPulseImportantConversation(conversation, seenSignatures))
  .sort((a, b) =>
    Number(Boolean(b.topActionTodo?.isUrgent)) - Number(Boolean(a.topActionTodo?.isUrgent))
    || b.priorityScore - a.priorityScore
    || a.id.localeCompare(b.id)
  )
  .slice(0, Math.max(0, limit))
  .map(conversation => conversation.id);

export const selectConversationAlerts = (conversations: Conversation[], limit = 10) =>
  [...conversations]
    .filter(conversation => conversation.topActionTodo || conversation.isFollowed)
    .sort(
      (a, b) =>
        Number(Boolean(b.topActionTodo)) - Number(Boolean(a.topActionTodo)) ||
        b.priorityScore - a.priorityScore
    )
    .slice(0, limit);

const attentionRank = (conversation: Conversation) => {
  if (conversation.isFollowed) return 0;
  if (conversation.topActionTodo?.isUrgent) return 1;
  return 2;
};

const isSubscriptionConversation = (conversation: Conversation) =>
  conversation.roleTitle === '订阅会话' || conversation.groupTopic === '订阅群聊';

const clampPercent = (value: number) =>
  Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));

export const getConversationRelationshipHalo = (conversation: Conversation) => {
  if (isSubscriptionConversation(conversation)) {
    return {
      kind: 'subscription' as const,
      tone: 'broadcast' as const,
      score: 0,
      label: '订阅信息源 · 单向更新',
    };
  }

  const frequencyScore = clampPercent(Math.round((conversation.interactionDays90d / 90) * 100));
  const score = conversation.isGroup
    ? Math.round(frequencyScore * 0.4 + clampPercent(conversation.joyBoyParticipationScore) * 0.6)
    : frequencyScore;
  const tone = score >= 75 ? 'hot' : score >= 50 ? 'warm' : score >= 20 ? 'cool' : 'cold';
  const labels = conversation.isGroup
    ? { cold: '低参与群聊', cool: '常规参与群聊', warm: '活跃协作群聊', hot: '核心协作群聊' }
    : { cold: '陌生客气', cool: '逐渐熟悉', warm: '关系密切', hot: '高频热络' };

  return {
    kind: conversation.isGroup ? 'group' as const : 'direct' as const,
    tone,
    score,
    label: conversation.isGroup
      ? `${labels[tone]} · 本人参与率 ${clampPercent(conversation.joyBoyParticipationScore)}%`
      : `${labels[tone]} · 近90天互动 ${Math.max(0, conversation.interactionDays90d)} 天`,
  };
};

export const selectDailyPriorityConversations = (conversations: Conversation[]) => {
  const ranked = [...conversations].sort((a, b) =>
    attentionRank(a) - attentionRank(b)
    || b.priorityScore - a.priorityScore
    || a.id.localeCompare(b.id)
  );
  return [
    ranked.find(conversation => !conversation.isGroup && !isSubscriptionConversation(conversation)),
    ranked.find(conversation => conversation.isGroup && !isSubscriptionConversation(conversation)),
    ranked.find(isSubscriptionConversation),
  ].filter((conversation): conversation is Conversation => Boolean(conversation));
};

export const getConversationAttentionReason = (conversation: Conversation) => {
  const reasons = [
    conversation.isFollowed ? '已手动星标' : '',
    conversation.topActionTodo
      ? (conversation.topActionTodo.isUrgent ? '会话中存在高优先级挂起事项' : '会话中存在挂起事项')
      : '',
  ].filter(Boolean);
  return reasons.join(' · ');
};

export const selectDailyFocusConversations = (conversations: Conversation[], limit = 8) =>
  [...conversations]
    .filter(conversation => conversation.isFollowed || Boolean(conversation.topActionTodo))
    .sort((a, b) =>
      attentionRank(a) - attentionRank(b)
      || b.priorityScore - a.priorityScore
      || a.id.localeCompare(b.id)
    )
    .slice(0, limit);
