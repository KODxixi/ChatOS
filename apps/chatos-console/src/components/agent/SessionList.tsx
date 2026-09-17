import { useState } from 'react'
import type { AgentSession } from '../../types/agent'

interface Props {
  sessions: AgentSession[]
  activeId: string | null
  runningSessionId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m}分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}小时前`
  return `${Math.floor(h / 24)}天前`
}

export default function SessionList({ sessions, activeId, runningSessionId, onSelect, onNew, onRename, onDelete }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')

  const startRename = (s: AgentSession) => {
    setEditingId(s.id)
    setEditText(s.title)
  }

  const commitRename = () => {
    if (editingId && editText.trim()) onRename(editingId, editText.trim())
    setEditingId(null)
  }

  return (
    <div style={{ width: 220, display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--demo-border, rgba(255,255,255,0.08))', background: 'var(--demo-panel, rgba(15,15,18,0.5))' }}>
      <div style={{ padding: '12px 12px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--quaternary, #888)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Agent 会话
        </span>
        <button
          className="button button--small button--ghost"
          onClick={onNew}
          style={{ minHeight: 24, padding: '2px 10px', fontSize: 11, borderRadius: 6 }}
          title="新建会话"
        >
          + 新建
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        {sessions.length === 0 && (
          <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--quaternary, #666)' }}>
            暂无会话，点击新建开始
          </div>
        )}
        {sessions.map((s) => {
          const isActive = s.id === activeId
          const isRunning = s.id === runningSessionId
          return (
            <div
              key={s.id}
              onClick={() => !editingId && onSelect(s.id)}
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                marginBottom: 2,
                cursor: 'pointer',
                background: isActive ? 'var(--demo-ink, #fff)' : 'transparent',
                color: isActive ? 'var(--demo-bg, #000)' : 'var(--demo-ink, #fff)',
                transition: 'background 0.15s',
              }}
              onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--raised, rgba(255,255,255,0.06))' }}
              onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
            >
              {editingId === s.id ? (
                <input
                  autoFocus
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setEditingId(null) }}
                  onClick={(e) => e.stopPropagation()}
                  style={{ width: '100%', background: 'var(--demo-bg)', border: '1px solid var(--accent)', borderRadius: 4, padding: '2px 6px', fontSize: 12, color: 'var(--demo-ink)' }}
                />
              ) : (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {isRunning && <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#2ecc71', animation: 'pulse 1.5s infinite' }} />}
                    <span style={{ fontSize: 13, fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s.title}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3, fontSize: 10, color: isActive ? 'var(--demo-bg)' : 'var(--quaternary, #666)', opacity: 0.7 }}>
                    <span>{s.messageCount} 条</span>
                    <span>{timeAgo(s.updatedAt)}</span>
                  </div>
                  <div style={{ display: 'flex', gap: 4, marginTop: 4, opacity: 0, transition: 'opacity 0.15s' }}
                    onMouseEnter={(e) => e.currentTarget.style.opacity = '1'}
                    onMouseLeave={(e) => e.currentTarget.style.opacity = '0'}
                  >
                    <button
                      onClick={(e) => { e.stopPropagation(); startRename(s) }}
                      style={{ fontSize: 10, background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', padding: '1px 4px' }}
                    >重命名</button>
                    <button
                      onClick={(e) => { e.stopPropagation(); if (confirm(`删除会话"${s.title}"？`)) onDelete(s.id) }}
                      style={{ fontSize: 10, background: 'transparent', border: 'none', color: '#e74c3c', cursor: 'pointer', padding: '1px 4px' }}
                    >删除</button>
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
