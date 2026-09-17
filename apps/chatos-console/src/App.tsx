import { useCallback, useEffect, useState } from 'react'
import type { ConversationPreview, Project } from './types'
import { getConversationPreviews, getTasks, getProjects, searchMessages, searchSemantic } from './api'
import TopBar from './components/TopBar'
import ConversationCard from './components/ConversationCard'
import ProjectCard from './components/ProjectCard'
import ConversationDetail from './components/ConversationDetail'
import TodoSidebar from './components/TodoSidebar'
import AuditOverlay from './components/AuditOverlay'
import GraphOverlay from './components/GraphOverlay'
import AgentPanel from './components/agent/AgentPanel'

interface HitRow {
  key: string
  conversationId: string
  conversationTitle: string
  senderName: string
  snippet: string
  kind: 'keyword' | 'semantic'
  score?: number
}

export default function App() {
  const [query, setQuery] = useState('')
  const [previews, setPreviews] = useState<ConversationPreview[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [todoCount, setTodoCount] = useState(0)
  const [loadError, setLoadError] = useState(false)
  const [auditOpen, setAuditOpen] = useState(false)
  const [graphOpen, setGraphOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [todosOpen, setTodosOpen] = useState(false)
  const [hits, setHits] = useState<HitRow[] | null>(null)
  const [filter, setFilter] = useState<'all' | 'todo' | 'project' | 'unread'>('all')

  const load = useCallback(() => {
    setLoadError(false)
    getConversationPreviews(150, true).then((p) => setPreviews(p.items)).catch((e) => { console.error(e); setLoadError(true) })
    getProjects(5).then((p) => setProjects(p.items)).catch((e) => console.error('[projects]', e))
    getTasks('todo', 100).then((t) => setTodoCount(t.total)).catch((e) => console.error(e))
  }, [])

  useEffect(() => { load() }, [load])

  // Semantic + keyword search merge.
  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) { setHits(null); return }
    let cancelled = false
    const t = setTimeout(() => {
      Promise.allSettled([searchMessages(q, 30), searchSemantic(q, undefined, 10)]).then(([kw, sem]) => {
        if (cancelled) return
        const rows: HitRow[] = []
        const seen = new Set<string>()
        if (sem.status === 'fulfilled') for (const s of sem.value.results) {
          seen.add(s.messageId)
          rows.push({ key: 's' + s.messageId, conversationId: s.conversationId, conversationTitle: s.conversationTitle, senderName: s.senderName, snippet: s.highlight || s.content, kind: 'semantic', score: s.similarityScore })
        }
        if (kw.status === 'fulfilled') for (const k of kw.value.items) {
          if (seen.has(String(k.id))) continue
          seen.add(String(k.id))
          rows.push({ key: 'k' + k.id, conversationId: k.conversationId, conversationTitle: k.conversationTitle, senderName: k.senderName, snippet: k.content, kind: 'keyword' })
        }
        setHits(rows.length ? rows : null)
      })
    }, 300)
    return () => { cancelled = true; clearTimeout(t) }
  }, [query])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); document.getElementById('global-search')?.focus() }
      if (e.key === 'Escape') { setAuditOpen(false); setGraphOpen(false); setTodosOpen(false); setAgentOpen(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const searching = query.trim().length >= 2 && hits !== null
  const selected = previews.find((c) => c.id === selectedId) || null
  const activeProjectId = selected?.projectId ?? null

  const HOUR = 3600_000
  const multiProjects = projects.filter((p) => p.conversationCount > 1)
  const visiblePreviews = previews.filter((c) => {
    if (filter === 'todo') return (c.todoCount ?? 0) > 0
    if (filter === 'unread') {
      const t = c.latestSentAt || c.lastMessageAt
      return !!t && Date.now() - t < HOUR
    }
    return true
  })
  const visibleProjects = filter === 'project' ? multiProjects : (filter === 'all' ? multiProjects : [])

  const FILTERS: { id: typeof filter; label: string }[] = [
    { id: 'all', label: '全部' },
    { id: 'todo', label: '待办' },
    { id: 'project', label: '项目' },
    { id: 'unread', label: '未读' },
  ]

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>
      <TopBar
        query={query}
        onQuery={setQuery}
        onOpenAudit={() => setAuditOpen(true)}
        onOpenGraph={() => setGraphOpen(true)}
        onOpenAgent={() => setAgentOpen(true)}
        todoCount={todoCount}
        onOpenTodos={() => setTodosOpen(true)}
      />

      <div className="dash-scroll">
        {!searching && (
          <div className="list-filter" style={{ marginBottom: 16 }}>
            {FILTERS.map((f) => (
              <button key={f.id} className={`filter-button ${filter === f.id ? 'is-active' : ''}`}
                style={{ minHeight: 28, padding: '3px 14px', fontSize: 12 }} onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
        )}
        {loadError ? (
          <div style={{ padding: 48, textAlign: 'center' }}>
            <div style={{ fontSize: 18, color: 'var(--ink)', marginBottom: 8 }}>加载失败</div>
            <div style={{ fontSize: 13, color: 'var(--tertiary)', marginBottom: 16 }}>无法获取会话列表</div>
            <button className="button button--small button--primary" onClick={load}>重试</button>
          </div>
        ) : searching ? (
          <div className="dash-grid">
            {hits!.map((h) => (
              <button key={h.key} className={`ch-card ${selectedId === h.conversationId ? 'is-selected' : ''}`} onClick={() => setSelectedId(h.conversationId)}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{h.conversationTitle}</span>
                  <span style={{ fontSize: 10, color: h.kind === 'semantic' ? 'var(--accent)' : 'var(--quaternary)' }}>
                    {h.kind === 'semantic' ? `语义 ${Math.round((h.score ?? 0) * 100)}%` : '匹配'}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: 'var(--tertiary)', marginTop: 8 }}>{h.senderName}: {h.snippet.slice(0, 80)}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="dash-grid">
            {visibleProjects.map((p) => (
              <ProjectCard key={p.projectId} project={p} selected={activeProjectId === p.projectId} onSelect={() => {
                const conv = previews.find((c) => c.projectId === p.projectId)
                setSelectedId(conv?.id ?? null)
              }} />
            ))}
            {visiblePreviews.map((c) => (
              <ConversationCard key={c.id} conv={c} selected={selectedId === c.id} onSelect={() => setSelectedId(c.id)} />
            ))}
          </div>
        )}
      </div>

      {selected && (
        <>
          <div className="side-panel__backdrop" onClick={() => setSelectedId(null)} />
          <aside className="side-panel">
            <ConversationDetail conv={selected} />
          </aside>
        </>
      )}

      {todosOpen && (
        <TodoSidebar onClose={() => setTodosOpen(false)} onJumpConversation={(id) => { setTodosOpen(false); setSelectedId(id) }} />
      )}
      {auditOpen && <AuditOverlay onClose={() => setAuditOpen(false)} />}
      {graphOpen && <GraphOverlay onClose={() => setGraphOpen(false)} />}
      {agentOpen && <AgentPanel onClose={() => setAgentOpen(false)} />}
    </div>
  )
}
