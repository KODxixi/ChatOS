// Agent Runtime WebSocket 客户端 — 单例，自动重连，事件按 sessionId 路由
import type {
  AgentEvent,
  AgentHealth,
  AgentSession,
  ConnectionState,
  WsRequest,
} from '../types/agent'

const WS_URL = 'ws://127.0.0.1:8913'
const HTTP_BASE = '/agent-api'
const RECONNECT_BASE_MS = 500
const RECONNECT_MAX_MS = 15_000
const MAX_RECONNECTS = 8

type EventHandler = (event: AgentEvent) => void

class AgentClient {
  private ws: WebSocket | null = null
  private state: ConnectionState = 'disconnected'
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private handlers = new Set<EventHandler>()
  private queue: WsRequest[] = []
  private shouldReconnect = true

  get connectionState(): ConnectionState {
    return this.state
  }

  /** 订阅所有事件 */
  on(handler: EventHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  /** 订阅指定会话的事件 */
  onSession(sessionId: string, handler: EventHandler): () => void {
    return this.on((event) => {
      if (event.sessionId === sessionId || event.type === 'connected') handler(event)
    })
  }

  private emit(event: AgentEvent): void {
    for (const h of this.handlers) h(event)
  }

  connect(): void {
    if (this.state === 'connecting' || this.state === 'connected') return
    this.shouldReconnect = true
    this.open()
  }

  private open(): void {
    this.state = 'connecting'
    try {
      this.ws = new WebSocket(WS_URL)
    } catch {
      this.scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      this.state = 'connected'
      this.reconnectAttempts = 0
      this.emit({ type: 'connected' })
      // flush queued messages
      while (this.queue.length > 0) {
        const msg = this.queue.shift()
        if (msg) this.send(msg)
      }
    }

    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data as string) as AgentEvent
        this.emit(data)
      } catch {
        // ignore malformed frames
      }
    }

    this.ws.onerror = () => {
      // error event precedes close; handled in onclose
    }

    this.ws.onclose = () => {
      this.state = 'disconnected'
      this.ws = null
      if (this.shouldReconnect) this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= MAX_RECONNECTS) {
      this.emit({ type: 'error', message: '连接失败次数过多，请检查 Agent Runtime 是否启动' })
      return
    }
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.reconnectAttempts, RECONNECT_MAX_MS)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => this.open(), delay)
  }

  disconnect(): void {
    this.shouldReconnect = false
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
    this.state = 'disconnected'
  }

  send(msg: WsRequest): void {
    if (this.state !== 'connected' || !this.ws) {
      this.queue.push(msg)
      if (this.state === 'disconnected') this.connect()
      return
    }
    try {
      this.ws.send(JSON.stringify(msg))
    } catch {
      this.queue.push(msg)
    }
  }

  // ---- 便捷方法 ----

  startSession(title?: string): void {
    this.send({ type: 'start', title })
  }

  sendMessage(sessionId: string, text: string): void {
    this.send({ type: 'message', sessionId, text })
  }

  interrupt(sessionId: string): void {
    this.send({ type: 'interrupt', sessionId })
  }

  listSessions(): void {
    this.send({ type: 'list' })
  }

  getHistory(sessionId: string): void {
    this.send({ type: 'history', sessionId })
  }

  renameSession(sessionId: string, title: string): void {
    this.send({ type: 'rename', sessionId, title })
  }

  deleteSession(sessionId: string): void {
    this.send({ type: 'delete', sessionId })
  }

  // ---- HTTP ----

  async health(): Promise<AgentHealth | null> {
    try {
      const res = await fetch(`${HTTP_BASE}/health`, { signal: AbortSignal.timeout(5000) })
      if (!res.ok) return null
      return (await res.json()) as AgentHealth
    } catch {
      return null
    }
  }

  async fetchSessions(): Promise<AgentSession[]> {
    try {
      const res = await fetch(`${HTTP_BASE}/sessions`, { signal: AbortSignal.timeout(5000) })
      if (!res.ok) return []
      return (await res.json()) as AgentSession[]
    } catch {
      return []
    }
  }
}

export const agentClient = new AgentClient()
