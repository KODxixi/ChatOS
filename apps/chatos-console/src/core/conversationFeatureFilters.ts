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
