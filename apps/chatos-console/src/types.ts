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

export type Domain = 'project' | 'work' | 'personal' | 'service' | 'subscription' | 'archived'

export interface Category {
  id: Domain
  label: string
  color: string
}
