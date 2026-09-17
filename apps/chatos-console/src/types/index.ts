export interface Conversation {
  id: string
  sourceConversationId: string
  title: string
  kind: 'group' | 'direct' | 'system'
  lastMessageAt: number | null
  messageCount: number
  isFavorite: boolean
  isArchived: boolean
  isIndexingEnabled: boolean
  domain: string | null
  projectId: string | null
  projectRole: string | null
  classificationStatus: string | null
  note: string
}

export interface ConversationPage {
  items: Conversation[]
  nextCursor: string | null
}

export interface HealthResponse {
  app: string
  source: string
  library: string
  lm: string
  tasks: { status: string }
  retrieval: { status: string }
  media: { status: string }
}

export interface SearchItem {
  id: number
  conversationId: string
  sourceMessageId: string | null
  senderId: string | null
  senderName: string
  sentAt: number
  type: string
  content: string
  conversationTitle: string
}

export interface SearchPage {
  items: SearchItem[]
  nextCursor: string | null
}

export interface ConversationPreview {
  id: string
  title: string
  kind: 'group' | 'direct' | 'system'
  messageCount: number
  lastMessageAt: number | null
  isIndexingEnabled: boolean
  domain: string | null
  projectId: string | null
  projectRole: string | null
  classificationStatus: string | null
  latestMessage: string | null
  latestSender: string | null
  latestSentAt: number | null
  topSpeakers: Array<{ name: string; count: number }>
  todoCount?: number
  summary?: string | null
  topTodo?: string | null
}

export interface ConversationPreviewPage {
  items: ConversationPreview[]
  total: number
}

export interface Project {
  projectId: string
  projectName: string
  conversationCount: number
  messageCount: number
  decisions: number
  milestones: number
  clientFeedback: number
  lastUpdated: number
}

export interface ProjectPage {
  total: number
  items: Project[]
}

export type Domain = 'project' | 'work' | 'personal' | 'service' | 'subscription' | 'archived'

export interface Category {
  id: Domain
  label: string
  color: string
}

/* ============ v0.3.0 extended types ============ */

export type TaskStatus = 'draft' | 'todo' | 'in_progress' | 'done'
export type TaskPriority = 'low' | 'medium' | 'high'

export interface Task {
  id: string
  content: string
  status: TaskStatus
  priority: TaskPriority
  sourceConversationId?: string | null
  sourceConversationTitle?: string | null
  senderName?: string | null
  sentAt?: number | null
  dueDate?: string | null
  extractedAt?: number | null
}

export interface TaskPage {
  items: Task[]
  total: number
}

export interface SummaryTodo {
  id?: string
  content: string
  done: boolean
}

export interface ConversationSummary {
  summary: string
  keyPeople: Array<{ name: string; count: number }>
  keyTopics: Array<{ term: string; count: number }>
  keyDates: Array<{ date: string; events?: string[] }>
  todos: SummaryTodo[]
  files: string[]
  links: Array<{ url: string; title?: string; sender?: string; sentAt?: number }>
  mediaCount?: { image?: number; voice?: number; video?: number; file?: number }
}

export interface AuditStats {
  messageTrend: Array<{ date: string; count: number }>
  domainDistribution: Array<{ domain: string; conversationCount: number; messageCount: number }>
  topConversations: Array<{ id: string; title: string; kind: string; messageCount: number; lastMessageAt: number | null }>
  attentionMetrics: Array<{ id: string; title: string; userMessageRatio: number; interactionCount7d: number; avgReplyMinutes: number; attentionScore: number }>
  kindDistribution: Array<{ kind: string; conversationCount: number; messageCount: number }>
}

export type MediaKind = 'image' | 'voice' | 'video' | 'file'

export interface MediaItem {
  id: string
  kind: MediaKind
  url?: string | null
  path?: string | null
  name?: string | null
  size?: number | null
  duration?: number | null
  senderName?: string | null
  sentAt?: number | null
  hasLocalFile?: boolean
}

export interface MediaPage {
  items: MediaItem[]
  total: number
}

export interface LinkItem {
  id: string
  url: string
  title?: string | null
  senderName?: string | null
  sentAt?: number | null
  context?: string | null
}

export interface LinkPage {
  items: LinkItem[]
  total: number
}

export type GraphNodeKind = 'contact' | 'group' | 'topic' | 'project'

export interface GraphNode {
  id: string
  label: string
  kind: GraphNodeKind
  size?: number
  color?: string
}

export interface GraphEdge {
  id: string
  sourceNodeId: string
  targetNodeId: string
  weight?: number
  kind?: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface SemanticSearchResult {
  messageId: string
  conversationId: string
  conversationTitle: string
  senderName: string
  content: string
  createdAt: number
  similarityScore: number
  highlight: string
}

export interface RagCitation {
  messageId: string
  senderName: string
  createdAt: number
  snippet: string
}

export interface RagResponse {
  answer: string | null
  citations: RagCitation[]
  sourcesUsed: number
  semanticResults?: SemanticSearchResult[]
}

export interface VectorStatus {
  totalMessages: number
  indexed: number
  pending: number
  failed: number
  conversationsIndexed: number
}
