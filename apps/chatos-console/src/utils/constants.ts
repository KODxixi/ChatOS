// Shared magic numbers and strings. Keep these in one place so components
// stay declarative; render values that genuinely need inline layout stay inline.

export const LIST_WIDTH_PX = 320
export const SEARCH_DEBOUNCE_MS = 300
export const PREVIEW_MAX_CHARS = 40
export const RECENT_HOURS = 1
export const HOUR_MS = 3_600_000

export const FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'direct', label: '私聊' },
  { id: 'group', label: '群聊' },
  { id: 'project', label: '项目相关' },
] as const

export const TASK_GROUPS = [
  { status: 'todo', label: '待处理', bar: '#ffd60a' },
  { status: 'in_progress', label: '进行中', bar: '#74a7ff' },
  { status: 'done', label: '已完成', bar: '#5fe386' },
] as const

export const SHORTCUTS = {
  focusSearch: { key: 'k', modifiers: ['meta', 'ctrl'] },
  closeOverlay: { key: 'Escape' },
} as const

export const KIND_LABEL: Record<string, string> = {
  group: '群聊',
  direct: '私聊',
  system: '公众号',
}
