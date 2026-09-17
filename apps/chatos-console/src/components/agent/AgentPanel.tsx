import { useCallback, useEffect, useRef, useState } from 'react'
import { agentClient } from '../../api/agent'
import type { AgentEvent, AgentMessage, AgentSession, ConnectionState, StreamTurn } from '../../types/agent'
import SessionList from './SessionList'
import ChatView from './ChatView'
import ChatInput from './ChatInput'

interface Props {
  onClose: () => void
}

export default function AgentPanel({ onClose }: Props) {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [history, setHistory] = useState<AgentMessage[]>([])
  const [stream, setStream] = useState<StreamTurn | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const historyMapRef = useRef<Map<string, AgentMessage[]>>(new Map())

  // 连接 + 事件订阅
  useEffect(() => {
    agentClient.connect()
    agentClient.listSessions()

    const unsub = agentClient.on((event: AgentEvent) => {
      if (event.type === 'connected') {
        setConnectionState('connected')
        setConnectionError(null)
        agentClient.listSessions()
        return
      }
      if (event.type === 'error' && !event.sessionId) {
        setConnectionError(event.message || event.error || '连接错误')
        return
      }
      if (event.type === 'sessions' && event.data) {
        setSessions(event.data)
        return
      }
      if (event.type === 'session-started' && event.session) {
        setSessions((prev) => [event.session!, ...prev])
        setActiveId(event.session.id)
        setHistory([])
        historyMapRef.current.set(event.session.id, [])
        return
      }
      if (event.type === 'history' && event.sessionId && event.messages) {
        historyMapRef.current.set(event.sessionId, event.messages)
        if (event.sessionId === activeId) setHistory(event.messages)
        return
      }
      if (event.type === 'renamed' && event.sessionId && event.title) {
        setSessions((prev) => prev.map((s) => s.id === event.sessionId ? { ...s, title: event.title! } : s))
        return
      }
      if (event.type === 'deleted' && event.sessionId) {
        setSessions((prev) => prev.filter((s) => s.id !== event.sessionId))
        historyMapRef.current.delete(event.sessionId)
        if (activeId === event.sessionId) {
          setActiveId(null)
          setHistory([])
          setStream(null)
        }
        return
      }

      // 会话级事件
      const sid = event.sessionId
      if (!sid) return

      if (event.type === 'user-message' && event.text) {
        const msg: AgentMessage = { id: Date.now(), sessionId: sid, role: 'user', content: event.text, createdAt: Date.now(), metadata: '{}' }
        const prev = historyMapRef.current.get(sid) || []
        const next = [...prev, msg]
        historyMapRef.current.set(sid, next)
        if (sid === activeId) setHistory(next)
        setSessions((prev) => prev.map((s) => s.id === sid ? { ...s, messageCount: s.messageCount + 1, updatedAt: Date.now() } : s))
        return
      }

      if (event.type === 'text-delta' && event.text) {
        setStream((prev) => {
          if (prev && prev.sessionId === sid) return { ...prev, content: prev.content + event.text! }
          return { sessionId: sid, content: event.text!, thinking: '', toolCalls: [], isComplete: false }
        })
        setIsRunning(true)
        return
      }

      if (event.type === 'thinking' && event.text) {
        setStream((prev) => {
          if (prev && prev.sessionId === sid) return { ...prev, thinking: prev.thinking + event.text! }
          return { sessionId: sid, content: '', thinking: event.text!, toolCalls: [], isComplete: false }
        })
        return
      }

      if (event.type === 'tool-call' && event.toolName) {
        setStream((prev) => {
          const base = prev && prev.sessionId === sid ? prev : { sessionId: sid, content: '', thinking: '', toolCalls: [], isComplete: false }
          return { ...base, toolCalls: [...base.toolCalls, { name: event.toolName!, args: event.toolArgs || '', status: 'running' as const }] }
        })
        return
      }

      if (event.type === 'tool-result' && event.toolName) {
        setStream((prev) => {
          if (!prev || prev.sessionId !== sid) return prev
          const tcs = [...prev.toolCalls]
          const idx = tcs.findIndex((t) => t.name === event.toolName && t.status === 'running')
          if (idx >= 0) tcs[idx] = { ...tcs[idx], result: event.toolResult || '', status: 'done' as const }
          return { ...prev, toolCalls: tcs }
        })
        return
      }

      if (event.type === 'turn-complete') {
        setStream((prev) => {
          if (prev && prev.sessionId === sid) {
            const msg: AgentMessage = { id: Date.now(), sessionId: sid, role: 'assistant', content: prev.content, createdAt: Date.now(), metadata: '{}' }
            const h = historyMapRef.current.get(sid) || []
            const next = [...h, msg]
            historyMapRef.current.set(sid, next)
            if (sid === activeId) setHistory(next)
            return { ...prev, isComplete: true }
          }
          return prev
        })
        setIsRunning(false)
        setSessions((prev) => prev.map((s) => s.id === sid ? { ...s, messageCount: s.messageCount + 1, updatedAt: Date.now() } : s))
        return
      }

      if (event.type === 'error' && sid) {
        setStream((prev) => prev && prev.sessionId === sid ? { ...prev, error: event.message || event.error, isComplete: true } : prev)
        setIsRunning(false)
        return
      }

      if (event.type === 'interrupted') {
        setStream((prev) => prev && prev.sessionId === sid ? { ...prev, isComplete: true } : prev)
        setIsRunning(false)
        return
      }
    })

    const pollState = setInterval(() => {
      setConnectionState(agentClient.connectionState)
    }, 2000)

    return () => {
      unsub()
      clearInterval(pollState)
    }
  }, [activeId])

  const handleSelect = useCallback((id: string) => {
    setActiveId(id)
    setStream(null)
    const h = historyMapRef.current.get(id)
    if (h) {
      setHistory(h)
    } else {
      setHistory([])
      agentClient.getHistory(id)
    }
  }, [])

  const handleSend = useCallback((text: string) => {
    if (!activeId) return
    setStream(null)
    agentClient.sendMessage(activeId, text)
  }, [activeId])

  const handleInterrupt = useCallback(() => {
    if (activeId) agentClient.interrupt(activeId)
  }, [activeId])

  const handleNew = useCallback(() => {
    agentClient.startSession()
  }, [])

  const handleRename = useCallback((id: string, title: string) => {
    agentClient.renameSession(id, title)
  }, [])

  const handleDelete = useCallback((id: string) => {
    agentClient.deleteSession(id)
  }, [])

  const runningSessionId = isRunning ? activeId : null

  return (
    <>
      <div className="side-panel__backdrop" onClick={onClose} style={{ zIndex: 90 }} />
      <aside className="side-panel" style={{
        zIndex: 91,
        width: 'min(900px, 95vw)',
        display: 'flex',
        flexDirection: 'column',
        padding: 0,
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', padding: '10px 16px',
          borderBottom: '1px solid var(--demo-border, rgba(255,255,255,0.08))',
          background: 'var(--demo-panel, rgba(15,15,18,0.8))',
        }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--demo-ink, #fff)' }}>Agent Runtime</span>
          <span style={{
            marginLeft: 10, fontSize: 10, padding: '2px 8px', borderRadius: 10,
            background: connectionState === 'connected' ? 'rgba(46,204,113,0.2)' : 'rgba(231,76,60,0.2)',
            color: connectionState === 'connected' ? '#2ecc71' : '#e74c3c',
          }}>
            {connectionState === 'connected' ? '已连接' : connectionState === 'connecting' ? '连接中…' : '未连接'}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--quaternary, #666)' }}>
            本地模型 · qwen3-vl-8b
          </span>
          <button className="icon-btn" onClick={onClose} style={{ marginLeft: 8 }} aria-label="关闭">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          <SessionList
            sessions={sessions}
            activeId={activeId}
            runningSessionId={runningSessionId}
            onSelect={handleSelect}
            onNew={handleNew}
            onRename={handleRename}
            onDelete={handleDelete}
          />
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <ChatView
              sessionId={activeId}
              history={history}
              stream={stream}
              connectionError={connectionError}
            />
            <ChatInput
              onSend={handleSend}
              onInterrupt={handleInterrupt}
              isRunning={isRunning}
              disabled={connectionState !== 'connected' || !activeId}
            />
          </div>
        </div>
      </aside>
    </>
  )
}
