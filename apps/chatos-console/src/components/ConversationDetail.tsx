import { useEffect, useState } from 'react'
import type { ConversationPreview, ConversationSummary, MediaItem, LinkItem } from '../types'
import { getConversationSummary, getConversationMedia, getConversationLinks, updateTask, askRag } from '../api'
import type { RagResponse } from '../types'
import { formatTime } from '../utils/format'
import { cleanText, isRealPerson, displayName } from '../utils/denoise'

interface Props {
  conv: ConversationPreview
}

export default function ConversationDetail({ conv }: Props) {
  const [summary, setSummary] = useState<ConversationSummary | null>(null)
  const [media, setMedia] = useState<MediaItem[]>([])
  const [links, setLinks] = useState<LinkItem[]>([])
  const [askInput, setAskInput] = useState('')
  const [asking, setAsking] = useState(false)
  const [rag, setRag] = useState<RagResponse | null>(null)

  useEffect(() => {
    setSummary(null); setMedia([]); setLinks([]); setRag(null)
    let cancelled = false
    Promise.all([
      getConversationSummary(conv.id).catch(() => null),
      getConversationMedia(conv.id).catch(() => null),
      getConversationLinks(conv.id).catch(() => null),
    ]).then(([s, m, l]) => {
      if (cancelled) return
      setSummary(s); setMedia(m?.items || []); setLinks(l?.items || [])
    })
    return () => { cancelled = true }
  }, [conv.id])

  const todos = (summary?.todos || []).filter((t) => !t.done)
  const people = (summary?.keyPeople || []).filter((p) => isRealPerson(p.name)).slice(0, 3)
  const images = media.filter((m) => m.kind === 'image')
  const files = media.filter((m) => m.kind === 'file')

  const toggleTodo = async (i: number) => {
    const t = todos[i]
    if (!t?.id) return
    setSummary((s) => s && { ...s, todos: s.todos.map((x) => x.id === t.id ? { ...x, done: true } : x) })
    try { await updateTask(t.id, 'done') } catch (e) { console.error(e) }
  }

  const ask = async () => {
    if (!askInput.trim()) return
    setAsking(true)
    try { setRag(await askRag(askInput.trim(), conv.id)) } catch (e) { console.error(e) }
    finally { setAsking(false) }
  }

  const hostOf = (url: string) => { try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url } }

  return (
    <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14, height: '100%' }}>
      {/* Header ~20% */}
      <div style={{ flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <h1 className="detail-title" style={{ fontSize: 18, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{conv.title}</h1>
          {conv.domain === 'project' && <span style={{ fontSize: 10, color: 'var(--tertiary)', background: 'var(--panel)', borderRadius: 999, padding: '2px 8px' }}>项目</span>}
        </div>
        <div className="detail-subtitle">{formatTime(conv.lastMessageAt)} · {conv.messageCount.toLocaleString()} 条</div>
      </div>

      {/* Summary ~40% */}
      <div style={{ flexShrink: 0 }}>
        <p style={{ fontSize: 11, color: 'var(--quaternary)', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 6 }}>摘要</p>
        <p style={{ fontSize: 15, lineHeight: 1.75, color: 'var(--secondary)', margin: 0, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {summary?.summary ? cleanText(summary.summary) : '暂无摘要'}
        </p>
        {people.length > 0 && (
          <div style={{ fontSize: 12, color: 'var(--tertiary)', marginTop: 8 }}>
            {people.map((p) => displayName(p.name)).join(' · ')}
          </div>
        )}
      </div>

      {/* Todos */}
      {todos.length > 0 && (
        <div style={{ flexShrink: 0 }}>
          <p style={{ fontSize: 11, color: 'var(--quaternary)', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 4 }}>待办 · {todos.length}</p>
          {todos.slice(0, 3).map((t, i) => (
            <label key={t.id ?? i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, color: 'var(--secondary)', padding: '3px 0', cursor: 'pointer' }}>
              <input type="checkbox" onChange={() => toggleTodo(i)} style={{ width: 14, height: 14, accentColor: 'var(--accent)' }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cleanText(t.content)}</span>
            </label>
          ))}
        </div>
      )}

      {/* Media */}
      {media.length > 0 && (
        <div style={{ flexShrink: 0 }}>
          <p style={{ fontSize: 11, color: 'var(--quaternary)', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 6 }}>媒体 · {media.length}</p>
          {images.length > 0 ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6 }}>
              {images.slice(0, 4).map((m) => (
                m.url
                  ? <img key={m.id} src={m.url} alt="" style={{ width: '100%', height: 72, objectFit: 'cover', borderRadius: 8 }} />
                  : <div key={m.id} style={{ height: 72, borderRadius: 8, background: 'var(--raised)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>🖼</div>
              ))}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {files.slice(0, 2).map((f) => (
                <div key={f.id} style={{ fontSize: 12, color: 'var(--tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>📎 {f.name || '文件'}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Links */}
      {links.length > 0 && (
        <div style={{ flexShrink: 0 }}>
          <p style={{ fontSize: 11, color: 'var(--quaternary)', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 4 }}>链接 · {links.length}</p>
          {links.slice(0, 2).map((l) => (
            <a key={l.id} href={l.url} target="_blank" rel="noreferrer" style={{ display: 'block', fontSize: 12, color: 'var(--accent)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {l.title || hostOf(l.url)}
            </a>
          ))}
        </div>
      )}

      {/* RAG ask */}
      <div style={{ marginTop: 'auto', flexShrink: 0, display: 'flex', gap: 8 }}>
        <input
          value={askInput}
          onChange={(e) => setAskInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') ask() }}
          placeholder="提问本会话…"
          style={{ flex: 1, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 99, padding: '6px 14px', color: 'var(--ink)', fontSize: 13 }}
        />
        <button className="button button--small button--primary" onClick={ask} disabled={asking}>{asking ? '…' : '问'}</button>
      </div>
      {rag?.answer && (
        <p style={{ flexShrink: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--secondary)', margin: 0 }}>{rag.answer}</p>
      )}
    </div>
  )
}
