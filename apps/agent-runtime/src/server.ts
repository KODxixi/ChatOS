import { WebSocketServer, WebSocket } from 'ws';
import http from 'node:http';
import { EventEmitter } from 'node:events';
import { CodexClient, type CodexEvent } from './CodexClient.js';
import { SessionManager } from './SessionManager.js';
import { ToolRegistry } from './ToolRegistry.js';
import type { AgentRuntimeConfig } from './config.js';

interface WsClientMessage {
  type: 'start' | 'message' | 'interrupt' | 'list' | 'delete' | 'history';
  sessionId?: string;
  text?: string;
  title?: string;
}

export class AgentRuntimeServer extends EventEmitter {
  private wss: WebSocketServer;
  private httpServer: http.Server;
  private codex: CodexClient;
  private sessions: SessionManager;
  private tools: ToolRegistry;
  private config: AgentRuntimeConfig;
  private activeSessionId: string | null = null;

  constructor(config: AgentRuntimeConfig) {
    super();
    this.config = config;
    this.codex = new CodexClient(config);
    this.sessions = new SessionManager(config.dbPath);
    this.tools = new ToolRegistry(config);

    this.httpServer = http.createServer((req, res) => this.handleHttp(req, res));
    this.wss = new WebSocketServer({ server: this.httpServer });

    this.codex.on('event', (event: CodexEvent) => this.broadcast(event));
    this.tools.on('tool-call', (t) => this.broadcast({ type: 'tool-call', tool: t.name, args: t.args }));
    this.tools.on('tool-result', (t) => this.broadcast({ type: 'tool-result', tool: t.name, result: t.result }));
  }

  async start(): Promise<void> {
    await this.codex.start();
    await this.codex.initialize();

    this.wss.on('connection', (ws) => {
      ws.on('message', (data) => this.handleWsMessage(ws, data.toString()));
      ws.send(JSON.stringify({ type: 'connected', tools: this.tools.list().map(t => t.name) }));
    });

    this.httpServer.listen(this.config.wsPort, () => {
      console.log(`[AgentRuntime] WebSocket+HTTP on :${this.config.wsPort}`);
      console.log(`[AgentRuntime] Codex initialized, model=${this.config.model}`);
    });
  }

  private handleHttp(req: http.IncomingMessage, res: http.ServerResponse): void {
    const url = new URL(req.url!, `http://localhost`);

    if (url.pathname === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'ok',
        codexRunning: this.codex.isRunning,
        codexInitialized: this.codex.isInitialized,
        activeSession: this.activeSessionId,
        tools: this.tools.list().map(t => t.name),
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
          await this.handleStart(ws, msg.title);
          break;
        case 'message':
          await this.handleMessage(ws, msg.sessionId!, msg.text!);
          break;
        case 'interrupt':
          if (this.activeSessionId) {
            const sess = this.sessions.getSession(this.activeSessionId);
            if (sess) await this.codex.interruptTurn(sess.threadId);
          }
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

  private async handleStart(ws: WebSocket, title?: string): Promise<void> {
    const { threadId } = await this.codex.startThread({ cwd: this.config.workspaceDir });
    const session = this.sessions.createSession(threadId, title || 'New Session');
    this.activeSessionId = session.id;
    ws.send(JSON.stringify({ type: 'session-started', session }));
    this.broadcast({ type: 'log', level: 'info', message: `session ${session.id} thread=${threadId}` });
  }

  private async handleMessage(ws: WebSocket, sessionId: string, text: string): Promise<void> {
    const session = this.sessions.getSession(sessionId);
    if (!session) {
      ws.send(JSON.stringify({ type: 'error', message: 'session not found' }));
      return;
    }

    this.activeSessionId = sessionId;
    this.sessions.addMessage(sessionId, 'user', text);
    this.broadcast({ type: 'user-message', sessionId, text });

    let assistantText = '';
    const onEvent = (event: CodexEvent) => {
      if (event.type === 'text-delta') {
        assistantText += event.text;
      }
      if (event.type === 'turn-complete') {
        this.sessions.addMessage(sessionId, 'assistant', assistantText);
        this.codex.off('event', onEvent);
      }
    };
    this.codex.on('event', onEvent);

    await this.codex.startTurn(session.threadId, text);
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
    this.codex.stop();
    this.sessions.close();
    this.wss.close();
    this.httpServer.close();
  }
}
