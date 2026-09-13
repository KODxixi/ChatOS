import type { ConversationPage, HealthResponse, ProjectPage, SearchPage } from '../types'

const API_BASE = '/api'

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, init)
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`)
  }
  return res.json() as Promise<T>
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
