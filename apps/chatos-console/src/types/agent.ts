// Agent Runtime 类型定义 — 与后端 apps/agent-runtime 契约一致

export interface AgentSession {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messageCount: number
  metadata: string
}

export interface AgentMessage {
  id: number
  sessionId: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  createdAt: number
  metadata: string
}

export type AgentEventType =
  | 'connected'
  | 'session-started'
  | 'user-message'
  | 'text-delta'
  | 'thinking'
  | 'tool-call'
  | 'tool-result'
  | 'turn-complete'
  | 'error'
  | 'interrupted'
  | 'sessions'
  | 'history'
  | 'renamed'
  | 'deleted'

export interface AgentEvent {
  type: AgentEventType
  sessionId?: string
  text?: string
  toolName?: string
  toolArgs?: string
  toolResult?: string
  error?: string
  message?: string
  session?: AgentSession
  data?: AgentSession[]
  messages?: AgentMessage[]
  title?: string
  tools?: string[]
  model?: string
}

export type WsRequestType = 'start' | 'message' | 'interrupt' | 'list' | 'delete' | 'history' | 'rename'

export interface WsRequest {
  type: WsRequestType
  sessionId?: string
  text?: string
  title?: string
}

export interface AgentHealth {
  status: 'ok' | 'degraded'
  model: string
  modelReachable: boolean
  modelError?: string
  activeSession: string | null
  isRunning: boolean
  tools: string[]
}

export type ConnectionState = 'connecting' | 'connected' | 'disconnected'

/** 前端流式消息状态（用于渲染） */
export interface StreamTurn {
  sessionId: string
  content: string
  thinking: string
  toolCalls: Array<{ name: string; args: string; result?: string; status: 'running' | 'done' }>
  isComplete: boolean
  error?: string
}
