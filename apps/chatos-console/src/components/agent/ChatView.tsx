import { useEffect, useRef } from 'react'
import type { AgentMessage, StreamTurn } from '../../types/agent'

interface Props {
  sessionId: string | null
  history: AgentMessage[]
  stream: StreamTurn | null
  connectionError?: string | null
}

function ToolCallCard({ name, args, result, status }: { name: string; args: string; result?: string; status: 'running' | 'done' }) {
  const [expanded, setExpanded] = useState(false)
  let parsedArgs = args
  try { parsedArgs = JSON.stringify(JSON.parse(args), null, 2) } catch { /* keep raw */ }

  return (
    <div style={{
      margin: '6px 0',
      padding: '8px 12px',
      borderRadius: 8,
      background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.08)',
      fontSize: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 18, height: 18, borderRadius: 4, fontSize: 10, fontWeight: 700,
          background: status === 'running' ? '#f39c12' : '#27ae60', color: '#fff',
        }}>
          {status === 'running' ? '…' : '✓'}
        </span>
        <span style={{ fontWeight: 600, color: 'var(--demo-ink, #fff)' }}>{name}</span>
        <span style={{ fontSize: 10, color: 'var(--quaternary, #888)' }}>
          {status === 'running' ? '执行中' : '完成'}
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--quaternary)' }}>
          {expanded ? '收起' : '展开'}
        </span>
      </div>
      {expanded && (
        <div style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 10, color: 'var(--quaternary)', marginBottom: 2 }}>参数</div>
            <pre style={{ margin: 0, padding: '6px 8px', background: 'rgba(0,0,0,0.3)', borderRadius: 4, fontSize: 11, overflow: 'auto', maxHeight: 120 }}>{parsedArgs}</pre>
          </div>
          {result !== undefined && (
            <div>
              <div style={{ fontSize: 10, color: 'var(--quaternary)', marginBottom: 2 }}>结果</div>
              <pre style={{ margin: 0, padding: '6px 8px', background: 'rgba(0,0,0,0.3)', borderRadius: 4, fontSize: 11, overflow: 'auto', maxHeight: 200, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{result.slice(0, 2000)}{result.length > 2000 ? `…(${result.length} chars)` : ''}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// useState import for ToolCallCard
import { useState } from 'react'

export default function ChatView({ sessionId, history, stream, connectionError }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, stream?.content, stream?.toolCalls.length])

  if (!sessionId) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--quaternary, #666)', fontSize: 14 }}>
        选择或新建一个会话开始
      </div>
    )
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      {connectionError && (
        <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(231,76,60,0.15)', border: '1px solid rgba(231,76,60,0.3)', color: '#e74c3c', fontSize: 12, marginBottom: 8 }}>
          {connectionError}
        </div>
      )}

      {history.map((msg) => {
        if (msg.role === 'user') {
          return (
            <div key={msg.id} style={{ alignSelf: 'flex-end', maxWidth: '80%', margin: '4px 0' }}>
              <div style={{
                padding: '10px 14px', borderRadius: '12px 12px 2px 12px',
                background: 'var(--demo-ink, #fff)', color: 'var(--demo-bg, #000)',
                fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {msg.content}
              </div>
            </div>
          )
        }
        if (msg.role === 'assistant') {
          return (
            <div key={msg.id} style={{ alignSelf: 'flex-start', maxWidth: '85%', margin: '4px 0' }}>
              <div style={{
                padding: '10px 14px', borderRadius: '12px 12px 12px 2px',
                background: 'var(--demo-panel, rgba(30,30,36,0.8))',
                border: '1px solid var(--demo-border, rgba(255,255,255,0.08))',
                color: 'var(--demo-ink, #fff)', fontSize: 13, lineHeight: 1.6,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {msg.content}
              </div>
            </div>
          )
        }
        return null
      })}

      {/* 流式输出 */}
      {stream && stream.sessionId === sessionId && (
        <div style={{ alignSelf: 'flex-start', maxWidth: '85%', margin: '4px 0' }}>
          {stream.thinking && (
            <details style={{ marginBottom: 6, fontSize: 11, color: 'var(--quaternary, #888)' }}>
              <summary style={{ cursor: 'pointer' }}>推理过程…</summary>
              <div style={{ padding: '6px 8px', marginTop: 4, background: 'rgba(0,0,0,0.2)', borderRadius: 4, whiteSpace: 'pre-wrap' }}>
                {stream.thinking}
              </div>
            </details>
          )}
          {stream.toolCalls.map((tc, i) => (
            <ToolCallCard key={i} name={tc.name} args={tc.args} result={tc.result} status={tc.status} />
          ))}
          {(stream.content || !stream.isComplete) && (
            <div style={{
              padding: '10px 14px', borderRadius: '12px 12px 12px 2px',
              background: 'var(--demo-panel, rgba(30,30,36,0.8))',
              border: '1px solid var(--demo-border, rgba(255,255,255,0.08))',
              color: 'var(--demo-ink, #fff)', fontSize: 13, lineHeight: 1.6,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {stream.content}
              {!stream.isComplete && <span style={{ animation: 'blink 1s infinite' }}>▋</span>}
            </div>
          )}
          {stream.error && (
            <div style={{ marginTop: 6, padding: '6px 10px', borderRadius: 6, background: 'rgba(231,76,60,0.15)', color: '#e74c3c', fontSize: 12 }}>
              {stream.error}
            </div>
          )}
        </div>
      )}

      <div ref={bottomRef} />
      <style>{`
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
      `}</style>
    </div>
  )
}
