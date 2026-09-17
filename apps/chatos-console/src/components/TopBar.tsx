interface Props {
  query: string
  onQuery: (q: string) => void
  onOpenAudit: () => void
  onOpenGraph: () => void
  onOpenAgent: () => void
  todoCount: number
  onOpenTodos: () => void
}

export default function TopBar({ query, onQuery, onOpenAudit, onOpenGraph, onOpenAgent, todoCount, onOpenTodos }: Props) {
  return (
    <header className="topbar">
      <span className="topbar__brand">
        ChatOS <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--quaternary)', marginLeft: 6 }}>v0.3.0</span>
      </span>
      <input
        id="global-search"
        className="topbar__search"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder="搜索会话和消息…"
      />
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="icon-btn" onClick={onOpenAgent} title="Agent Runtime" aria-label="Agent Runtime">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="7" width="18" height="13" rx="2" /><path d="M12 7V3M8 12h.01M16 12h.01M9 16h6" />
          </svg>
        </button>
        <button className="icon-btn todo-badge" onClick={onOpenTodos} title="待办" aria-label="待办">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 12l3 3 5-6" />
          </svg>
          {todoCount > 0 && <span className="todo-badge__dot">{todoCount}</span>}
        </button>
        <button className="icon-btn" onClick={onOpenAudit} title="审计分析" aria-label="审计分析">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" /><path d="M7 15l4-6 4 3 5-8" />
          </svg>
        </button>
        <button className="icon-btn" onClick={onOpenGraph} title="知识图谱" aria-label="知识图谱">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="5" cy="6" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" />
            <path d="M7 7.5L10.5 16M17 7.5L13.5 16M7.5 6h9" />
          </svg>
        </button>
      </div>
    </header>
  )
}
