import { WebSocketServer, WebSocket } from 'ws';
import http from 'node:http';
import { EventEmitter } from 'node:events';
import { LocalModelClient, type AgentEvent, type ChatMessage } from './LocalModelClient.js';
import { SessionManager } from './SessionManager.js';
import { ToolRegistry } from './ToolRegistry.js';
import type { AgentRuntimeConfig } from './config.js';

interface WsClientMessage {
  type: 'start' | 'message' | 'interrupt' | 'list' | 'delete' | 'history' | 'rename';
  sessionId?: string;
  text?: string;
  title?: string;
}

export class AgentRuntimeServer extends EventEmitter {
  private wss: WebSocketServer;
  private httpServer: http.Server;
  private model: LocalModelClient;
  private sessions: SessionManager;
  private tools: ToolRegistry;
  private config: AgentRuntimeConfig;
  private activeSessionId: string | null = null;

  constructor(config: AgentRuntimeConfig) {
    super();
    this.config = config;
    this.tools = new ToolRegistry(config);
    this.model = new LocalModelClient({
      baseUrl: config.lmStudioUrl,
      model: config.model,
      apiKey: config.apiKey,
      maxTokens: config.maxTokens,
      temperature: config.temperature,
      maxToolIterations: config.maxToolIterations,
      requestTimeoutMs: config.modelTimeoutMs,
      systemPrompt: config.systemPrompt,
    }, this.tools);
    this.sessions = new SessionManager(config.dbPath);

    this.httpServer = http.createServer((req, res) => this.handleHttp(req, res));
    this.wss = new WebSocketServer({ server: this.httpServer });

    this.model.on('event', (event: AgentEvent) => this.broadcast(event as unknown as Record<string, unknown>));
  }

  async start(): Promise<void> {
    this.wss.on('connection', (ws) => {
      ws.on('message', (data) => this.handleWsMessage(ws, data.toString()));
      ws.send(JSON.stringify({
        type: 'connected',
        tools: this.tools.list().map((t) => t.name),
        model: this.config.model,
      }));
    });

    this.httpServer.listen(this.config.wsPort, () => {
      console.log(`[AgentRuntime] WebSocket+HTTP on :${this.config.wsPort}`);
      console.log(`[AgentRuntime] Model=${this.config.model} @ ${this.config.lmStudioUrl}`);
      console.log(`[AgentRuntime] Tools: ${this.tools.list().map((t) => t.name).join(', ')}`);
    });
  }

  private async handleHttp(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    const url = new URL(req.url!, `http://localhost`);

    if (url.pathname === '/health') {
      const health = await this.model.healthCheck();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: health.ok ? 'ok' : 'degraded',
        model: health.model || this.config.model,
        modelReachable: health.ok,
        modelError: health.error,
        activeSession: this.activeSessionId,
        isRunning: this.model.isRunning,
        tools: this.tools.list().map((t) => t.name),
      }));
      return;
    }

    if (url.pathname === '/sessions' && req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(this.sessions.listSessions()));
      return;
    }

    res.writeHead(404);
    res.end('not found');
  }

  private async handleWsMessage(ws: WebSocket, raw: string): Promise<void> {
    let msg: WsClientMessage;
    try {
      msg = JSON.parse(raw);
    } catch {
      ws.send(JSON.stringify({ type: 'error', message: 'invalid JSON' }));
      return;
    }

    try {
      switch (msg.type) {
        case 'start':
          this.handleStart(ws, msg.title);
          break;
        case 'message':
          await this.handleMessage(ws, msg.sessionId!, msg.text!);
          break;
        case 'interrupt':
          this.model.interrupt();
          this.broadcast({ type: 'interrupted' });
          break;
        case 'list':
          ws.send(JSON.stringify({ type: 'sessions', data: this.sessions.listSessions() }));
          break;
        case 'delete':
          if (msg.sessionId) {
            this.sessions.deleteSession(msg.sessionId);
            ws.send(JSON.stringify({ type: 'deleted', sessionId: msg.sessionId }));
          }
          break;
        case 'rename':
          if (msg.sessionId && msg.title) {
            this.sessions.renameSession(msg.sessionId, msg.title);
            ws.send(JSON.stringify({ type: 'renamed', sessionId: msg.sessionId, title: msg.title }));
          }
          break;
        case 'history':
          if (msg.sessionId) {
            ws.send(JSON.stringify({
              type: 'history',
              sessionId: msg.sessionId,
              messages: this.sessions.getMessages(msg.sessionId),
            }));
          }
          break;
      }
    } catch (err) {
      ws.send(JSON.stringify({ type: 'error', message: err instanceof Error ? err.message : String(err) }));
    }
  }

  private handleStart(ws: WebSocket, title?: string): void {
    const session = this.sessions.createSession(title || 'New Session');
    this.activeSessionId = session.id;
    ws.send(JSON.stringify({ type: 'session-started', session }));
  }

  private async handleMessage(ws: WebSocket, sessionId: string, text: string): Promise<void> {
    const session = this.sessions.getSession(sessionId);
    if (!session) {
      ws.send(JSON.stringify({ type: 'error', message: 'session not found' }));
      return;
    }

    if (this.model.isRunning) {
      ws.send(JSON.stringify({ type: 'error', message: 'another turn is in progress' }));
      return;
    }

    this.activeSessionId = sessionId;
    this.sessions.addMessage(sessionId, 'user', text);
    this.broadcast({ type: 'user-message', sessionId, text });

    // 构建对话历史
    const history = this.sessions.getMessages(sessionId) as ChatMessage[];

    let assistantText = '';
    const onEvent = (event: AgentEvent) => {
      if (event.type === 'text-delta' && event.text) {
        assistantText += event.text;
      }
      if (event.type === 'turn-complete') {
        this.sessions.addMessage(sessionId, 'assistant', assistantText);
        this.model.off('event', onEvent);
      }
      if (event.type === 'error') {
        this.sessions.addMessage(sessionId, 'assistant', assistantText || `[error] ${event.error}`);
        this.model.off('event', onEvent);
      }
    };
    this.model.on('event', onEvent);

    try {
      await this.model.runTurn(history);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.broadcast({ type: 'error', message: msg });
    }
  }

  private broadcast(event: Record<string, unknown>): void {
    const payload = JSON.stringify(event);
    for (const client of this.wss.clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(payload);
      }
    }
  }

  stop(): void {
    this.model.interrupt();
    this.sessions.close();
    this.wss.close();
    this.httpServer.close();
  }
}
