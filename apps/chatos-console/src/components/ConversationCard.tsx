import { useEffect, useState } from 'react'
import type { ConversationPreview, MediaItem } from '../types'
import { getConversationMedia } from '../api'
import { formatTime } from '../utils/format'

interface Props {
  conv: ConversationPreview
  selected: boolean
  onSelect: () => void
}

function SafeImg({ src, alt }: { src: string; alt: string }) {
  const [err, setErr] = useState(false)
  if (err || !src) {
    return <div style={{ height: 72, background: 'var(--raised)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--tertiary)', fontSize: 20 }}>🖼</div>
  }
  return <img src={src} alt={alt} loading="lazy" onError={() => setErr(true)} style={{ width: '100%', height: 72, objectFit: 'cover', display: 'block' }} />
}

function MediaStrip({ convId }: { convId: string }) {
  const [items, setItems] = useState<MediaItem[]>([])
  useEffect(() => {
    let cancelled = false
    getConversationMedia(convId).then((p) => { if (!cancelled) setItems(p.items.filter((m) => m.kind === 'image').slice(0, 3)) }).catch(() => {})
    return () => { cancelled = true }
  }, [convId])
  if (items.length === 0) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginTop: 10 }}>
      {items.map((m) => <SafeImg key={m.id} src={m.url || ''} alt="" />)}
    </div>
  )
}

export default function ConversationCard({ conv, selected, onSelect }: Props) {
  const todoCount = conv.todoCount ?? 0
  const summary = conv.summary?.trim()
  return (
    <button className={`ch-card ${selected ? 'is-selected' : ''}`} onClick={onSelect}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>
          {conv.title}
        </span>
        {conv.domain === 'project' && (
          <span style={{ fontSize: 10, color: 'var(--tertiary)', background: 'var(--panel)', borderRadius: 999, padding: '2px 8px', flexShrink: 0 }}>项目</span>
        )}
      </div>
      <div style={{ fontSize: 13, color: summary ? 'var(--secondary)' : 'var(--quaternary)', marginTop: 8, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {summary || '正在生成摘要…'}
      </div>
      {todoCount > 0 && conv.topTodo && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 12, color: 'var(--accent)' }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 12l3 3 5-6" />
          </svg>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{conv.topTodo}</span>
          {todoCount > 1 && <span style={{ flexShrink: 0 }}>+{todoCount - 1}</span>}
        </div>
      )}
      <MediaStrip convId={conv.id} />
      <div style={{ fontSize: 11, color: 'var(--quaternary)', marginTop: 10 }}>{formatTime(conv.lastMessageAt)}</div>
    </button>
  )
}
