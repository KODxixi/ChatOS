import type {
  ConversationPage,
  ConversationPreviewPage,
  HealthResponse,
  ProjectPage,
  SearchPage,
  TaskPage,
  Task,
  TaskStatus,
  ConversationSummary,
  AuditStats,
  MediaPage,
  MediaItem,
  LinkPage,
  GraphData,
  SemanticSearchResult,
  RagResponse,
  VectorStatus,
} from '../types'

const API_BASE = '/api'
const API_TIMEOUT_MS = 10_000

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), API_TIMEOUT_MS)
  try {
    const res = await fetch(`${API_BASE}${url}`, { ...init, signal: ctrl.signal })
    if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
    return (await res.json()) as T
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error(`API timeout after ${API_TIMEOUT_MS}ms: ${url}`)
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}

export function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>('/health')
}

export function getConversations(
  limit = 50,
  cursor?: string,
  kind?: string,
): Promise<ConversationPage> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) params.set('cursor', cursor)
  if (kind) params.set('kind', kind)
  return fetchJson<ConversationPage>(`/conversations?${params.toString()}`)
}

export function getConversationPreviews(
  limit = 100,
  onlyActive = true,
  domain?: string,
): Promise<ConversationPreviewPage> {
  const params = new URLSearchParams({ limit: String(limit), onlyActive: String(onlyActive) })
  if (domain) params.set('domain', domain)
  return fetchJson<ConversationPreviewPage>(`/conversations/preview?${params.toString()}`)
}

export function searchMessages(
  q: string,
  limit = 50,
): Promise<SearchPage> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  return fetchJson<SearchPage>(`/search?${params.toString()}`)
}

export function evaluateActivity(inactiveDays = 30): Promise<{
  userWxid: string
  inactiveDays: number
  evaluated: number
  markedInactive: number
  alreadyInactive: number
  active: number
  markedIds: string[]
}> {
  return fetchJson(`/conversations/evaluate-activity?inactiveDays=${inactiveDays}`, {
    method: 'POST',
  })
}

export function getProjects(minMessages = 20): Promise<ProjectPage> {
  return fetchJson<ProjectPage>(`/projects?minMessages=${minMessages}`)
}

export function classifyConversations(all = false): Promise<{
  evaluated: number
  classified: number
  needsReview: number
  byDomain: Record<string, number>
}> {
  return fetchJson(`/conversations/classify?all=${all}`, { method: 'POST' })
}

export function extractProjects(minMessages = 20): Promise<{
  projectCount: number
  projects: Array<{
    projectId: string
    projectName: string
    messageCount: number
    decisions: number
    milestones: number
    clientFeedback: number
  }>
}> {
  return fetchJson(`/conversations/extract-projects?minMessages=${minMessages}`, { method: 'POST' })
}

/* ============ v0.3.0 extended API ============ */

// Tasks
export function getTasks(status?: string, limit = 50): Promise<TaskPage> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (status) params.set('status', status)
  return fetchJson<TaskPage>(`/tasks?${params.toString()}`)
}

export function extractTasks(): Promise<{ extracted: number }> {
  return fetchJson('/tasks/extract', { method: 'POST' })
}

export function updateTask(taskId: string, status: TaskStatus, priority?: string): Promise<Task> {
  const body: Record<string, string> = { status }
  if (priority) body.priority = priority
  return fetchJson<Task>(`/tasks/${taskId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// Conversation summary
export function getConversationSummary(id: string): Promise<ConversationSummary> {
  return fetchJson<ConversationSummary>(`/conversations/${id}/summary`)
}

// Audit stats
export function getAuditStats(): Promise<AuditStats> {
  return fetchJson<AuditStats>('/audit/stats')
}

// Media & links
interface RawMediaItem {
  id: number | string
  kind?: string
  type?: string
  name?: string | null
  fileName?: string | null
  size?: number | null
  fileSize?: number | null
  duration?: number | null
  senderName?: string | null
  sentAt?: number | null
  hasLocalFile?: boolean
  assetId?: number | string | null
  asset_id?: number | string | null
  url?: string | null
  path?: string | null
}
interface RawMediaPage { total?: number; items?: RawMediaItem[] }

export function getConversationMedia(id: string, kind?: string): Promise<MediaPage> {
  const params = new URLSearchParams()
  if (kind) params.set('kind', kind)
  const qs = params.toString()
  return fetchJson<RawMediaPage>(`/conversations/${id}/media${qs ? `?${qs}` : ''}`).then((page) => ({
    total: page?.total ?? page?.items?.length ?? 0,
    items: (page?.items ?? []).map((raw): MediaItem => {
      const t = raw.kind || raw.type || 'file'
      const kind = t === 'attachment' ? 'file' : t
      const assetId = raw.assetId ?? raw.asset_id
      return {
        id: String(raw.id),
        kind: kind as MediaItem['kind'],
        name: raw.name ?? raw.fileName ?? null,
        size: raw.size ?? raw.fileSize ?? null,
        duration: raw.duration ?? null,
        senderName: raw.senderName ?? null,
        sentAt: raw.sentAt ?? null,
        hasLocalFile: raw.hasLocalFile ?? false,
        url: assetId ? `/api/assets/${assetId}` : raw.url ?? null,
        path: raw.path ?? null,
      }
    }),
  }))
}

export function getConversationLinks(id: string): Promise<LinkPage> {
  return fetchJson<LinkPage>(`/conversations/${id}/links`)
}

// Knowledge archive
export function archiveKnowledge(): Promise<{ filesArchived: number; linksSaved: number }> {
  return fetchJson('/knowledge/archive', { method: 'POST' })
}

// Knowledge graph
export function getGraph(limit = 80, hideNoise = true): Promise<GraphData> {
  return fetchJson<GraphData>(`/graph?limit=${limit}&hideNoise=${hideNoise}`)
}

// Vector + RAG (backend may not be ready; callers degrade gracefully)
export function searchSemantic(q: string, conversationId?: string, limit = 10): Promise<{ results: SemanticSearchResult[] }> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (conversationId) params.set('conversation_id', conversationId)
  return fetchJson(`/search/semantic?${params.toString()}`)
}

export function askRag(question: string, conversationId?: string, limit = 8): Promise<RagResponse> {
  return fetchJson('/rag/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, conversationId, limit }),
  })
}

export function getVectorStatus(): Promise<VectorStatus> {
  return fetchJson('/vector/status')
}

export function triggerIndex(conversationId?: string): Promise<void> {
  return fetchJson('/vector/index', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversationId: conversationId ?? null }),
  })
}
