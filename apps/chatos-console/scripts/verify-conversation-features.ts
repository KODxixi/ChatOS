import assert from 'node:assert/strict';
import * as conversationFeatures from '../src/core/conversationFeatureFilters.ts';
import type { Conversation } from '../src/types.ts';

const {
  buildConversationFeatureFilters,
  filterConversationsByFeature,
  filterConversationsByGroup,
  getConversationAttentionReason,
  getImportantConversationSignature,
  selectPulsingImportantConversationIds,
  shouldPulseImportantConversation,
  selectDailyFocusConversations,
  selectConversationAlerts,
} = conversationFeatures;

const conversation = (overrides: Partial<Conversation>): Conversation => ({
  id: 'base',
  name: 'Base',
  isGroup: false,
  avatar: '',
  isFollowed: false,
  interactionDays90d: 1,
  joyBoyParticipationScore: 0,
  lastActiveTime: '刚刚',
  twoLineSummary: '',
  unreadCount: 0,
  priorityScore: 50,
  priorityReason: '',
  categoryTag: 'general',
  linkedProjectIds: [],
  isArchived: false,
  groupId: null,
  ...overrides,
});

const first = conversation({
  id: 'followed',
  name: 'Alex',
  isFollowed: true,
  unreadCount: 3,
  interactionDays90d: 72,
  linkedProjectIds: ['project-1'],
});
const second = conversation({
  id: 'todo',
  name: '产品群',
  isGroup: true,
  interactionDays90d: 24,
  categoryTag: 'active_collab',
  topActionTodo: { title: '验收', } as Conversation['topActionTodo'],
});
const third = conversation({ id: 'quiet', name: '偶尔联系', interactionDays90d: 2 });

const filters = buildConversationFeatureFilters([first, second, third]);
assert.deepEqual(
  filters.map(filter => filter.id),
  ['all', 'followed', 'has_todo', 'unread', 'project_linked', 'high_interaction', 'active_collab'],
  'only data-backed, non-zero feature filters should be generated'
);
assert.equal(filters.find(filter => filter.id === 'unread')?.count, 1);
assert.deepEqual(filterConversationsByFeature([first, second, third], 'has_todo').map(item => item.id), ['todo']);

const archived = conversation({ id: 'archived', isArchived: true });
const work = conversation({ id: 'work', groupId: 'work' });
assert.deepEqual(filterConversationsByGroup([first, archived, work], 'all').map(item => item.id), ['followed', 'work']);
assert.deepEqual(filterConversationsByGroup([first, archived, work], 'favorite').map(item => item.id), ['followed']);
assert.deepEqual(filterConversationsByGroup([first, archived, work], 'archived').map(item => item.id), ['archived']);
assert.deepEqual(filterConversationsByGroup([first, archived, work], 'work').map(item => item.id), ['work']);

const refreshed = buildConversationFeatureFilters([
  { ...first, isFollowed: false, unreadCount: 0 },
  second,
  third,
]);
assert.ok(!refreshed.some(filter => filter.id === 'followed'), 'removed database features must disappear');
assert.ok(!refreshed.some(filter => filter.id === 'unread'), 'counts must follow refreshed conversation data');

assert.deepEqual(
  selectConversationAlerts([first, second, third]).map(item => item.id),
  ['todo', 'followed'],
  'actionable conversations must lead starred reminders regardless of input order'
);

const noisy = conversation({
  id: 'noisy',
  name: '高频闲聊群',
  isGroup: true,
  categoryTag: 'noisy_group',
  priorityScore: 99,
});
const project = conversation({
  id: 'project',
  name: '项目协作',
  linkedProjectIds: ['project-2'],
  priorityScore: 30,
});
const urgent = conversation({
  id: 'urgent',
  name: '紧急行动',
  priorityScore: 10,
  topActionTodo: { id: 'todo-urgent', title: '今天确认方案', isUrgent: true } as Conversation['topActionTodo'],
});
const ordinaryTodo = conversation({
  id: 'ordinary-todo',
  name: '普通行动',
  priorityScore: 90,
  topActionTodo: { id: 'todo-ordinary', title: '后续跟进', isUrgent: false } as Conversation['topActionTodo'],
});

assert.equal(shouldPulseImportantConversation(urgent, {}), true);
assert.equal(
  shouldPulseImportantConversation(urgent, { urgent: getImportantConversationSignature(urgent) }),
  false
);
assert.equal(shouldPulseImportantConversation(ordinaryTodo, {}), true);
assert.equal(shouldPulseImportantConversation({ ...ordinaryTodo, priorityScore: 79 }, {}), false);
assert.notEqual(
  getImportantConversationSignature(urgent),
  getImportantConversationSignature({
    ...urgent,
    topActionTodo: { ...urgent.topActionTodo!, title: '今天确认修改后的方案' },
  }),
  'changed important content must become visible again even when the task id is stable'
);
assert.deepEqual(
  selectPulsingImportantConversationIds(
    [urgent, ordinaryTodo, { ...ordinaryTodo, id: 'third-important', priorityScore: 95 }],
    {},
    2
  ),
  ['urgent', 'third-important'],
  'only the highest-priority bounded set may animate at once'
);

assert.deepEqual(
  selectDailyFocusConversations([noisy, third, project, ordinaryTodo, first, urgent], 3).map(item => item.id),
  ['followed', 'urgent', 'ordinary-todo'],
  'only manual focus and actual pending work may enter daily focus'
);
assert.deepEqual(
  selectDailyFocusConversations([noisy, third, project], 8),
  [],
  'message volume, project links and inferred categories are not enough evidence for daily focus'
);
assert.equal(
  selectDailyFocusConversations([{ ...noisy, isFollowed: true }], 1)[0]?.id,
  'noisy',
  'manual pinning must be able to restore an automatically de-prioritized conversation'
);
assert.match(getConversationAttentionReason(urgent), /挂起事项/);
assert.doesNotMatch(
  getConversationAttentionReason(urgent),
  /我负责|需要你|你的任务/,
  'focus explanations must not invent an unbound user identity or responsibility'
);

assert.equal(
  typeof conversationFeatures.selectDailyPriorityConversations,
  'function',
  'daily focus must expose one selector for the private, group and subscription lead cards'
);
const subscription = conversation({
  id: 'subscription',
  name: '服务通知',
  isGroup: false,
  roleTitle: '订阅会话',
  priorityScore: 12,
  topActionTodo: { title: '查看更新', isUrgent: false } as Conversation['topActionTodo'],
});
const lowerPrivate = conversation({
  id: 'lower-private',
  name: '普通私信',
  priorityScore: 1,
});
const selectDailyPriorityConversations = conversationFeatures.selectDailyPriorityConversations as (
  conversations: Conversation[]
) => Conversation[];
assert.deepEqual(
  selectDailyPriorityConversations([subscription, lowerPrivate, second, first]).map(item => item.id),
  ['followed', 'todo', 'subscription'],
  'today must lead with the highest-priority private, group and subscription conversation in that order'
);

assert.equal(
  typeof conversationFeatures.getConversationRelationshipHalo,
  'function',
  'avatars must expose one data-driven relationship halo classifier'
);
const getConversationRelationshipHalo = conversationFeatures.getConversationRelationshipHalo as (
  conversation: Conversation
) => { kind: string; tone: string; score: number; label: string };
assert.deepEqual(
  getConversationRelationshipHalo(conversation({ interactionDays90d: 0 })),
  { kind: 'direct', tone: 'cold', score: 0, label: '陌生客气 · 近90天互动 0 天' }
);
assert.deepEqual(
  getConversationRelationshipHalo(conversation({ interactionDays90d: 72 })),
  { kind: 'direct', tone: 'hot', score: 80, label: '高频热络 · 近90天互动 72 天' }
);
assert.deepEqual(
  getConversationRelationshipHalo(conversation({
    isGroup: true,
    interactionDays90d: 45,
    joyBoyParticipationScore: 50,
  })),
  { kind: 'group', tone: 'warm', score: 50, label: '活跃协作群聊 · 本人参与率 50%' }
);
assert.deepEqual(
  getConversationRelationshipHalo(conversation({
    roleTitle: '订阅会话',
    interactionDays90d: 90,
    joyBoyParticipationScore: 100,
  })),
  { kind: 'subscription', tone: 'broadcast', score: 0, label: '订阅信息源 · 单向更新' },
  'subscription traffic must not be mistaken for interpersonal closeness'
);

console.log('Conversation feature filters regenerate from the current dataset.');
