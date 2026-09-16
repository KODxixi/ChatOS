import { spawn, ChildProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';
import path from 'node:path';
import type { AgentRuntimeConfig } from './config.js';

function resolveCodexPath(configPath: string): string {
  // On Windows, use shell to resolve from PATH; full paths with spaces break spawn
  if (process.platform === 'win32') {
    return configPath.endsWith('.cmd') ? configPath : `${configPath}.cmd`;
  }
  return configPath;
}

export interface JsonRpcRequest {
  jsonrpc: '2.0';
  id: number | string;
  method: string;
  params?: unknown;
}

export interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: number | string;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface JsonRpcNotification {
  jsonrpc: '2.0';
  method: string;
  params?: unknown;
}

export type CodexEvent =
  | { type: 'text-delta'; text: string }
  | { type: 'tool-call'; tool: string; args: unknown }
  | { type: 'tool-result'; tool: string; result: unknown }
  | { type: 'turn-complete'; turnId: string }
  | { type: 'error'; message: string }
  | { type: 'log'; level: string; message: string }
  | { type: 'raw'; method: string; params: unknown };

export class CodexClient extends EventEmitter {
  private proc: ChildProcess | null = null;
  private requestId = 0;
  private pending = new Map<number | string, { resolve: (r: unknown) => void; reject: (e: Error) => void }>();
  private buffer = '';
  private initialized = false;
  private restartCount = 0;
  private config: AgentRuntimeConfig;

  constructor(config: AgentRuntimeConfig) {
    super();
    this.config = config;
  }

  get isRunning(): boolean {
    return this.proc !== null && !this.proc.killed;
  }

  get isInitialized(): boolean {
    return this.initialized;
  }

  async start(): Promise<void> {
    if (this.isRunning) return;

    const args = [
      'app-server',
      '--listen', 'stdio://',
      '-c', `model="${this.config.model}"`,
      '-c', `approval_policy="${this.config.approvalPolicy}"`,
      '-c', `sandbox_mode="${this.config.sandbox}"`,
      '-c', 'oss=true',
      '-c', `local_provider="${this.config.localProvider}"`,
    ];

    const codexPath = resolveCodexPath(this.config.codexPath);
    this.proc = spawn(codexPath, args, {
      cwd: this.config.workspaceDir,
      stdio: ['pipe', 'pipe', 'pipe'],
      shell: process.platform === 'win32',
      env: {
        ...process.env,
        CODEX_HOME: this.config.codexHome,
        OPENAI_BASE_URL: this.config.lmStudioUrl,
        OPENAI_API_KEY: 'not-needed',
      },
    });

    this.proc.stdout!.on('data', (data: Buffer) => this.handleStdout(data));
    this.proc.stderr!.on('data', (data: Buffer) => {
      const msg = data.toString().trim();
      if (msg) this.emit('event', { type: 'log', level: 'stderr', message: msg } as CodexEvent);
    });
    this.proc.on('exit', (code, signal) => {
      this.emit('event', { type: 'log', level: 'info', message: `codex exited code=${code} signal=${signal}` } as CodexEvent);
      this.initialized = false;
      this.proc = null;
      this.handleCrash(code);
    });
    this.proc.on('error', (err) => {
      this.emit('event', { type: 'error', message: `spawn error: ${err.message}` } as CodexEvent);
    });
  }

  private handleCrash(code: number | null): void {
    if (code === 0) return;
    if (this.restartCount >= this.config.maxRestarts) {
      this.emit('event', { type: 'error', message: `max restarts (${this.config.maxRestarts}) reached` } as CodexEvent);
      return;
    }
    this.restartCount++;
    this.emit('event', { type: 'log', level: 'warn', message: `restarting (attempt ${this.restartCount})` } as CodexEvent);
    setTimeout(() => this.start().catch(() => {}), 1000 * this.restartCount);
  }

  private handleStdout(data: Buffer): void {
    this.buffer += data.toString();
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const msg = JSON.parse(trimmed);
        this.handleMessage(msg);
      } catch {
        // non-JSON line, treat as log
        this.emit('event', { type: 'log', level: 'stdout', message: trimmed } as CodexEvent);
      }
    }
  }

  private handleMessage(msg: JsonRpcResponse | JsonRpcNotification | JsonRpcRequest): void {
    if ('id' in msg && 'result' in msg) {
      // Response
      const pending = this.pending.get(msg.id);
      if (pending) {
        this.pending.delete(msg.id);
        if (msg.error) {
          pending.reject(new Error(`${msg.error.code}: ${msg.error.message}`));
        } else {
          pending.resolve(msg.result);
        }
      }
    } else if ('id' in msg && 'method' in msg) {
      // Request from server (e.g. tool calls)
      this.handleServerRequest(msg as JsonRpcRequest);
    } else if ('method' in msg && !('id' in msg)) {
      // Notification
      this.handleNotification(msg as JsonRpcNotification);
    }
  }

  private handleServerRequest(req: JsonRpcRequest): void {
    // Server-initiated requests (tool calls, approvals, etc.)
    this.emit('event', { type: 'raw', method: req.method, params: req.params } as CodexEvent);

    // Auto-approve everything (approvalPolicy=never should prevent these, but handle gracefully)
    if (req.method.includes('approval') || req.method.includes('Approval')) {
      this.sendResponse(req.id, { approved: true });
      return;
    }

    // Default: acknowledge
    this.sendResponse(req.id, { ok: true });
  }

  private handleNotification(notif: JsonRpcNotification): void {
    const method = notif.method;
    const params = notif.params as Record<string, unknown> | undefined;

    // Text delta
    if (method.includes('delta') && params?.['text']) {
      this.emit('event', { type: 'text-delta', text: String(params['text']) } as CodexEvent);
      return;
    }

    // Tool call
    if (method.includes('tool') && method.includes('start')) {
      this.emit('event', { type: 'tool-call', tool: method, args: params } as CodexEvent);
      return;
    }

    // Turn complete
    if (method.includes('complete') && method.includes('turn')) {
      this.emit('event', { type: 'turn-complete', turnId: String(params?.['turnId'] || '') } as CodexEvent);
      return;
    }

    // Error
    if (method.includes('error') || params?.['error']) {
      const errMsg = params?.['error']
        ? JSON.stringify(params['error'])
        : method;
      this.emit('event', { type: 'error', message: errMsg } as CodexEvent);
      return;
    }

    // Raw passthrough
    this.emit('event', { type: 'raw', method, params } as CodexEvent);
  }

  private sendResponse(id: number | string, result: unknown): void {
    const msg: JsonRpcResponse = { jsonrpc: '2.0', id, result };
    this.proc?.stdin?.write(JSON.stringify(msg) + '\n');
  }

  private request<T>(method: string, params?: unknown): Promise<T> {
    if (!this.proc) return Promise.reject(new Error('not running'));
    const id = ++this.requestId;
    const msg: JsonRpcRequest = { jsonrpc: '2.0', id, method, params };

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`request ${method} timed out after ${this.config.modelTimeoutMs}ms`));
      }, this.config.modelTimeoutMs);

      this.pending.set(id, {
        resolve: (r) => { clearTimeout(timeout); resolve(r as T); },
        reject: (e) => { clearTimeout(timeout); reject(e); },
      });

      this.proc!.stdin!.write(JSON.stringify(msg) + '\n');
    });
  }

  async initialize(): Promise<{ codexHome: string; platform: string }> {
    const result = await this.request<{ codexHome: string; platformFamily: string; platformOs: string }>('initialize', {
      clientInfo: { name: 'chatos-agent-runtime', version: '0.1.0' },
      capabilities: null,
    });
    this.initialized = true;
    this.restartCount = 0;
    return { codexHome: result.codexHome, platform: result.platformOs };
  }

  async startThread(options?: { cwd?: string; instructions?: string }): Promise<{ threadId: string }> {
    const params: Record<string, unknown> = {
      approvalPolicy: this.config.approvalPolicy,
      sandbox: this.config.sandbox,
      model: this.config.model,
      cwd: options?.cwd || this.config.workspaceDir,
    };
    if (options?.instructions) {
      params.baseInstructions = options.instructions;
    }
    const result = await this.request<{ thread: { id: string } }>('thread/start', params);
    return { threadId: result.thread.id };
  }

  async startTurn(threadId: string, text: string): Promise<{ turnId: string }> {
    return this.request<{ turnId: string }>('turn/start', {
      threadId,
      input: [{ type: 'text', text, text_elements: [] }],
    });
  }

  async interruptTurn(threadId: string): Promise<void> {
    await this.request('turn/interrupt', { threadId });
  }

  async listThreads(): Promise<unknown> {
    return this.request('thread/list', {});
  }

  stop(): void {
    if (this.proc) {
      this.proc.kill('SIGTERM');
      this.proc = null;
    }
    this.initialized = false;
  }
}
