import { Conversation, TodoItem } from '../types';

/**
 * Priority Scoring Engine for ChatOS 2.0
 *
 * Implements strict specification from UI_REBUILD_SPEC.md:
 * 1. Active Attention Weight (isFollowed) -> +25 points
 * 2. Urgent / Imminent Action Todos -> +30 points
 * 3. 90-Day Two-Way Interaction Frequency (0-90 days) -> up to +35 points
 * 4. Self Participation Rate (joyBoyParticipationScore, 0-100%) -> up to +20 points
 * 5. Permanent Family Core & VIP Protection:
 *    - Family contacts (e.g., Mom) and low-frequency key advisors (e.g., Dr. Warren)
 *      are algorithmically safeguarded from ever dropping below visibility threshold.
 * 6. High-Volume Chatter Noise Suppression:
 *    - Noisy groups with low JoyBoy involvement receive dampening penalty.
 */

export interface PriorityBreakdown {
  baseScore: number;
  followBonus: number;
  todoBonus: number;
  interactionBonus: number;
  participationBonus: number;
  protectionBonus: number;
  noisePenalty: number;
  totalScore: number;
  explanation: string;
}

export function calculateConversationPriority(
  conversation: Conversation,
  activeTodos: TodoItem[]
): PriorityBreakdown {
  let baseScore = 20;
  let followBonus = 0;
  let todoBonus = 0;
  let interactionBonus = 0;
  let participationBonus = 0;
  let protectionBonus = 0;
  let noisePenalty = 0;

  // 1. Followed Bonus (Starred by JoyBoy)
  if (conversation.isFollowed) {
    followBonus = 25;
  }

  // 2. Urgent / Active Todo Association
  const relatedTodos = activeTodos.filter(
    t => t.sourceConversationId === conversation.id && t.status !== 'completed' && !t.isIgnored
  );
  const hasUrgentTodo = relatedTodos.some(t => t.isUrgent || t.status === 'in_progress');
  if (hasUrgentTodo) {
    todoBonus = 30;
  } else if (relatedTodos.length > 0) {
    todoBonus = 18;
  }

  // 3. 90-Day Interaction Days
  interactionBonus = Math.min(35, Math.round((conversation.interactionDays90d / 90) * 35));

  // 4. JoyBoy Participation Rate (0-100%)
  participationBonus = Math.min(20, Math.round((conversation.joyBoyParticipationScore / 100) * 20));

  // 5. Protected Categories (Mom / Family Core / Low-Freq Advisors)
  if (conversation.categoryTag === 'family_core') {
    protectionBonus = 35; // Guarantee family remains in top view regardless of message volume
  } else if (conversation.categoryTag === 'low_freq_important') {
    protectionBonus = 22; // Protect key advisors
  }

  // 6. Noise Suppression for Idle / Massive Chatter Groups
  if (conversation.isGroup && conversation.joyBoyParticipationScore < 15 && conversation.categoryTag === 'noisy_group') {
    noisePenalty = -35;
  }

  const rawTotal = baseScore + followBonus + todoBonus + interactionBonus + participationBonus + protectionBonus + noisePenalty;
  const totalScore = Math.max(5, Math.min(100, rawTotal));

  // Explanatory breakdown text
  let explanation = '';
  if (conversation.categoryTag === 'family_core') {
    explanation = '算法常驻保护 (家庭核心)，确保重要亲友免受降噪过滤';
  } else if (hasUrgentTodo) {
    explanation = '关联近期紧迫行动待办，优先上浮至最前';
  } else if (followBonus > 0) {
    explanation = '本人主动星标关注，全时段高权重点亮';
  } else if (conversation.interactionDays90d > 60) {
    explanation = `近90天高频双向协同 (${conversation.interactionDays90d}天互动)`;
  } else if (noisePenalty < 0) {
    explanation = '低参与度闲聊群聊，智能降噪抑制';
  } else {
    explanation = `综合协同分 (${totalScore}分)`;
  }

  return {
    baseScore,
    followBonus,
    todoBonus,
    interactionBonus,
    participationBonus,
    protectionBonus,
    noisePenalty,
    totalScore,
    explanation,
  };
}

/**
 * Sorts conversations deterministically according to ChatOS priority rules.
 */
export function sortConversationsByPriority(
  conversations: Conversation[],
  todos: TodoItem[]
): Conversation[] {
  return [...conversations].sort((a, b) => {
    const scoreA = calculateConversationPriority(a, todos).totalScore;
    const scoreB = calculateConversationPriority(b, todos).totalScore;
    if (scoreB !== scoreA) {
      return scoreB - scoreA;
    }
    // Tie-breaker: Starred > Unread > Recency
    if (a.isFollowed !== b.isFollowed) return a.isFollowed ? -1 : 1;
    if (a.unreadCount !== b.unreadCount) return b.unreadCount - a.unreadCount;
    return b.lastActiveTime.localeCompare(a.lastActiveTime);
  });
}
