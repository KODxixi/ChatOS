import { WebSocketServer, WebSocket } from 'ws';
import http from 'node:http';
import { EventEmitter } from 'node:events';
import { LocalModelClient, type AgentEvent } from './LocalModelClient.js';
import { SessionManager } from './SessionManager.js';
import { ToolRegistry } from './ToolRegistry.js';
import type { AgentRuntimeConfig } from './config.js';

interface WsClientMessage {
  type: 'start' | 'message' | 'interrupt' | 'list' | 'delete' | 'history' | 'rename';
  sessionId?: string;
  text?: string;
  title?: string;
}

interface ActiveTurn {
  sessionId: string;
  owner: WebSocket;
  promise: Promise<void>;
}

const MAX_WS_PAYLOAD_BYTES = 64 * 1024;
const MAX_MESSAGE_CHARS = 32_000;
const MAX_TITLE_CHARS = 200;
const MAX_HISTORY_CHARS = 48_000;

function isAllowedOrigin(origin?: string): boolean {
  if (!origin) return true;
  try {
    const { hostname } = new URL(origin);
    return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
  } catch {
    return false;
  }
}

export class AgentRuntimeServer extends EventEmitter {
  private wss: WebSocketServer;
  private httpServer: http.Server;
  private model: LocalModelClient;
  private sessions: SessionManager;
  private tools: ToolRegistry;
  private config: AgentRuntimeConfig;
  private activeTurn: ActiveTurn | null = null;

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
    this.wss = new WebSocketServer({
      server: this.httpServer,
      maxPayload: MAX_WS_PAYLOAD_BYTES,
      verifyClient: ({ origin }: { origin: string }) => isAllowedOrigin(origin),
    });
    this.wss.on('error', (err) => {
      if (this.httpServer.listening) console.error('[AgentRuntime] WebSocket error:', err);
    });
  }

  async start(): Promise<void> {
    this.wss.on('connection', (ws) => {
      ws.on('message', (data) => { void this.handleWsMessage(ws, data.toString()); });
      ws.on('error', () => {
        if (this.activeTurn?.owner === ws) this.model.interrupt();
      });
      ws.on('close', () => {
        if (this.activeTurn?.owner === ws) this.model.interrupt();
      });
      this.send(ws, {
        type: 'connected',
        tools: this.tools.list().map((t) => t.name),
        model: this.config.model,
      });
    });

    await new Promise<void>((resolve, reject) => {
      const onError = (err: Error) => {
        this.httpServer.off('listening', onListening);
        reject(err);
      };
      const onListening = () => {
        this.httpServer.off('error', onError);
        resolve();
      };
      this.httpServer.once('error', onError);
      this.httpServer.once('listening', onListening);
      this.httpServer.listen(this.config.wsPort, '127.0.0.1');
    });
    const address = this.httpServer.address();
    const port = typeof address === 'object' && address ? address.port : this.config.wsPort;
    console.log(`[AgentRuntime] WebSocket+HTTP on 127.0.0.1:${port}`);
    console.log(`[AgentRuntime] Model=${this.config.model} @ ${this.config.lmStudioUrl}`);
    console.log(`[AgentRuntime] Tools: ${this.tools.list().map((t) => t.name).join(', ')}`);
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
        activeSession: this.activeTurn?.sessionId ?? null,
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
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      this.sendError(ws, 'invalid JSON');
      return;
    }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)
      || typeof (parsed as { type?: unknown }).type !== 'string') {
      this.sendError(ws, 'invalid message');
      return;
    }
    const msg = parsed as WsClientMessage;

    try {
      switch (msg.type) {
        case 'start':
          if (msg.title !== undefined && (typeof msg.title !== 'string' || msg.title.length > MAX_TITLE_CHARS)) {
            this.sendError(ws, `title must be a string up to ${MAX_TITLE_CHARS} characters`);
            break;
          }
          this.handleStart(ws, msg.title);
          break;
        case 'message':
          if (typeof msg.sessionId !== 'string' || !msg.sessionId || msg.sessionId.length > 128) {
            this.sendError(ws, 'sessionId is required');
            break;
          }
          if (typeof msg.text !== 'string' || !msg.text.trim() || msg.text.length > MAX_MESSAGE_CHARS) {
            this.sendError(ws, `text must be a non-empty string up to ${MAX_MESSAGE_CHARS} characters`, msg.sessionId);
            break;
          }
          await this.handleMessage(ws, msg.sessionId, msg.text);
          break;
        case 'interrupt':
          this.handleInterrupt(ws, msg.sessionId);
          break;
        case 'list':
          this.send(ws, { type: 'sessions', data: this.sessions.listSessions() });
          break;
        case 'delete':
          if (!msg.sessionId || !this.sessions.getSession(msg.sessionId)) {
            this.sendError(ws, 'session not found', msg.sessionId);
          } else if (this.activeTurn?.sessionId === msg.sessionId) {
            this.sendError(ws, 'cannot delete a running session', msg.sessionId);
          } else {
            this.sessions.deleteSession(msg.sessionId);
            this.send(ws, { type: 'deleted', sessionId: msg.sessionId });
          }
          break;
        case 'rename':
          if (!msg.sessionId || !this.sessions.getSession(msg.sessionId)) {
            this.sendError(ws, 'session not found', msg.sessionId);
          } else if (typeof msg.title !== 'string' || !msg.title.trim() || msg.title.length > MAX_TITLE_CHARS) {
            this.sendError(ws, `title must be a non-empty string up to ${MAX_TITLE_CHARS} characters`, msg.sessionId);
          } else {
            this.sessions.renameSession(msg.sessionId, msg.title);
            this.send(ws, { type: 'renamed', sessionId: msg.sessionId, title: msg.title });
          }
          break;
        case 'history':
          if (!msg.sessionId || !this.sessions.getSession(msg.sessionId)) {
            this.sendError(ws, 'session not found', msg.sessionId);
          } else {
            this.send(ws, {
              type: 'history',
              sessionId: msg.sessionId,
              messages: this.sessions.getMessages(msg.sessionId),
            });
          }
          break;
        default:
          this.sendError(ws, 'unknown message type');
      }
    } catch (err) {
      this.sendError(ws, err instanceof Error ? err.message : String(err), msg.sessionId);
    }
  }

  private handleStart(ws: WebSocket, title?: string): void {
    const session = this.sessions.createSession(title || 'New Session');
    this.send(ws, { type: 'session-started', session });
  }

  private async handleMessage(ws: WebSocket, sessionId: string, text: string): Promise<void> {
    const session = this.sessions.getSession(sessionId);
    if (!session) {
      this.sendError(ws, 'session not found', sessionId);
      return;
    }

    if (this.activeTurn || this.model.isRunning) {
      this.sendError(ws, 'another turn is in progress', sessionId);
      return;
    }

    const promise = this.runSessionTurn(ws, sessionId, text);
    const turn: ActiveTurn = { sessionId, owner: ws, promise };
    this.activeTurn = turn;
    try {
      await promise;
    } finally {
      if (this.activeTurn === turn) this.activeTurn = null;
    }
  }

  private async runSessionTurn(ws: WebSocket, sessionId: string, text: string): Promise<void> {
    this.sessions.addMessage(sessionId, 'user', text);
    this.send(ws, { type: 'user-message', sessionId, text });
    const history = this.sessions.getChatMessages(sessionId, 100, MAX_HISTORY_CHARS);
    let errorSent = false;
    const onEvent = (event: AgentEvent) => {
      if (event.type === 'error') errorSent = true;
      this.sendAgentEvent(ws, sessionId, event);
    };
    this.model.on('event', onEvent);
    try {
      await this.model.runTurn(history, (message) => this.sessions.addChatMessage(sessionId, message));
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (!errorSent) this.sendError(ws, err instanceof Error ? err.message : String(err), sessionId);
    } finally {
      this.model.off('event', onEvent);
    }
  }

  private handleInterrupt(ws: WebSocket, sessionId?: string): void {
    const turn = this.activeTurn;
    if (!turn) {
      this.sendError(ws, 'no turn is in progress', sessionId);
      return;
    }
    if (turn.owner !== ws || (sessionId !== undefined && sessionId !== turn.sessionId)) {
      this.sendError(ws, 'cannot interrupt another session', sessionId);
      return;
    }
    this.model.interrupt();
    this.send(ws, { type: 'interrupted', sessionId: turn.sessionId });
  }

  private sendAgentEvent(ws: WebSocket, sessionId: string, event: AgentEvent): void {
    if (event.type === 'error') {
      this.sendError(ws, event.error || 'agent error', sessionId);
      return;
    }
    this.send(ws, { ...event, sessionId });
  }

  private sendError(ws: WebSocket, message: string, sessionId?: string): void {
    this.send(ws, { type: 'error', message, ...(sessionId ? { sessionId } : {}) });
  }

  private send(ws: WebSocket, event: Record<string, unknown>): void {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(event));
    }
  }

  async stop(): Promise<void> {
    this.model.interrupt();
    await this.activeTurn?.promise.catch(() => undefined);
    for (const client of this.wss.clients) client.close();
    await new Promise<void>((resolve) => {
      this.wss.close(() => resolve());
    });
    if (this.httpServer.listening) {
      await new Promise<void>((resolve) => {
        this.httpServer.close(() => resolve());
      });
    }
    this.sessions.close();
  }
}
