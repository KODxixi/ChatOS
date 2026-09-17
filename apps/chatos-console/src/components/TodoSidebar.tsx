import { useEffect, useState } from 'react'
import type { Task } from '../types'
import { getTasks, updateTask } from '../api'
import { cleanText } from '../utils/denoise'

interface Props {
  onClose: () => void
  onJumpConversation: (id: string) => void
}

export default function TodoSidebar({ onClose, onJumpConversation }: Props) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [error, setError] = useState(false)

  const load = () => {
    setError(false)
    getTasks(undefined, 100).then((t) => setTasks(t.items)).catch((e) => { console.error(e); setError(true) })
  }
  useEffect(() => { load() }, [])

  const toggle = async (t: Task) => {
    const next = t.status === 'done' ? 'todo' : 'done'
    setTasks((arr) => arr.map((x) => (x.id === t.id ? { ...x, status: next as Task['status'] } : x)))
    try { await updateTask(t.id, next) } catch (e) { console.error(e) }
  }

  const groups = [
    { label: '待处理', items: tasks.filter((t) => t.status !== 'done') },
    { label: '已完成', items: tasks.filter((t) => t.status === 'done') },
  ]

  return (
    <>
      <div className="side-panel__backdrop" onClick={onClose} />
      <aside className="side-panel" style={{ width: 360 }}>
        <div style={{ padding: '24px 24px 12px' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>待办 · {groups[0].items.length}</div>
        </div>
        {error && (
          <div style={{ padding: '0 24px 12px' }}>
            <button className="button button--small" onClick={load}>加载失败，点击重试</button>
          </div>
        )}
        <div style={{ padding: '0 12px 24px' }}>
          {groups.map((g) => (
            <div key={g.label} style={{ marginBottom: 16 }}>
              <p className="detail-eyebrow" style={{ padding: '8px 12px 4px' }}>{g.label} · {g.items.length}</p>
              {g.items.map((t) => (
                <button key={t.id} onClick={() => toggle(t)} style={{ display: 'flex', width: '100%', gap: 10, alignItems: 'flex-start', background: 'none', border: 'none', padding: '10px 12px', textAlign: 'left' }}>
                  <span style={{ width: 16, height: 16, marginTop: 2, border: '1.5px solid var(--quaternary)', borderRadius: 4, flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>
                    <span style={{ display: 'block', fontSize: 14, color: t.status === 'done' ? 'var(--quaternary)' : 'var(--ink)', textDecoration: t.status === 'done' ? 'line-through' : 'none' }}>
                      {cleanText(t.content)}
                    </span>
                    {t.sourceConversationTitle && (
                      <span style={{ display: 'block', fontSize: 11, color: 'var(--quaternary)', marginTop: 2, cursor: 'pointer' }}
                        onClick={(e) => { e.stopPropagation(); if (t.sourceConversationId) onJumpConversation(t.sourceConversationId) }}>
                        {t.sourceConversationTitle}
                      </span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
