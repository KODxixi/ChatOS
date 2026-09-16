import path from 'node:path';
import os from 'node:os';

export interface AgentRuntimeConfig {
  /** Codex CLI path (default: resolve from PATH) */
  codexPath: string;
  /** Model name for LM Studio */
  model: string;
  /** Local provider: lmstudio or ollama */
  localProvider: 'lmstudio' | 'ollama';
  /** LM Studio base URL */
  lmStudioUrl: string;
  /** llama-swap embedding endpoint */
  embeddingUrl: string;
  /** Sandbox mode */
  sandbox: 'read-only' | 'workspace-write' | 'danger-full-access';
  /** Approval policy */
  approvalPolicy: 'never' | 'on-request' | 'untrusted';
  /** Working directory for agent */
  workspaceDir: string;
  /** WebSocket server port */
  wsPort: number;
  /** HTTP API port */
  httpPort: number;
  /** SQLite DB path for session persistence */
  dbPath: string;
  /** Log directory */
  logDir: string;
  /** Codex home directory (config, sessions, plugins) */
  codexHome: string;
  /** Max tool calls per turn */
  maxToolCalls: number;
  /** Model call timeout (ms) */
  modelTimeoutMs: number;
  /** Max restart attempts on crash */
  maxRestarts: number;
}

export const defaultConfig: AgentRuntimeConfig = {
  codexPath: 'codex',
  model: 'qwen/qwen3-vl-8b',
  localProvider: 'lmstudio',
  lmStudioUrl: 'http://localhost:1234/v1',
  embeddingUrl: 'http://localhost:9123/v1',
  sandbox: 'workspace-write',
  approvalPolicy: 'never',
  workspaceDir: path.join(os.homedir(), 'ChatOS', 'workspace'),
  wsPort: 8913,
  httpPort: 8914,
  dbPath: path.join(os.homedir(), 'ChatOS', 'data', 'agent-runtime.sqlite'),
  logDir: path.join(os.homedir(), 'ChatOS', 'logs'),
  codexHome: 'C:\\ChatOS\\var\\codex-home',
  maxToolCalls: 20,
  modelTimeoutMs: 120000,
  maxRestarts: 3,
};

export function loadConfig(overrides?: Partial<AgentRuntimeConfig>): AgentRuntimeConfig {
  return { ...defaultConfig, ...overrides };
}
