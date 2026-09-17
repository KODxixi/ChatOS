import assert from 'node:assert/strict';
import {
  buildConversationFeatureFilters,
  filterConversationsByFeature,
  selectConversationAlerts,
} from '../src/core/conversationFeatureFilters.ts';
import type { Conversation } from '../src/types.ts';

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

console.log('Conversation feature filters regenerate from the current dataset.');
