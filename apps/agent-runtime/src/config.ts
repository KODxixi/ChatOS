import path from 'node:path';
import os from 'node:os';

export interface AgentRuntimeConfig {
  /** LM Studio base URL (OpenAI 兼容 API) */
  lmStudioUrl: string;
  /** Model name */
  model: string;
  /** API key (本地模型不需要，占位) */
  apiKey: string;
  /** llama-swap embedding endpoint */
  embeddingUrl: string;
  /** Embedding model name */
  embeddingModel: string;
  /** Working directory for agent (sandbox root) */
  workspaceDir: string;
  /** WebSocket server port */
  wsPort: number;
  /** HTTP API port (same as wsPort, REST endpoints) */
  httpPort: number;
  /** SQLite DB path for session persistence */
  dbPath: string;
  /** Log directory */
  logDir: string;
  /** Max tool iterations per turn */
  maxToolIterations: number;
  /** Model call timeout (ms) */
  modelTimeoutMs: number;
  /** Max tokens per response */
  maxTokens: number;
  /** Temperature */
  temperature: number;
  /** System prompt */
  systemPrompt: string;
}

const DEFAULT_SYSTEM_PROMPT = `你是 ChatOS 的本地 Agent 运行时，运行在用户 Gary 的 Windows 机器上。
所有推理本地完成（LM Studio qwen3-vl-8b），不调用外部 API。
你可以使用提供的工具完成任务：读写文件、生成 embedding、数据质检。
执行工具后根据结果继续推理，直到任务完成。
回复简洁直接，中文优先，术语用英文。`;

export const defaultConfig: AgentRuntimeConfig = {
  lmStudioUrl: 'http://localhost:1234/v1',
  model: 'qwen/qwen3-vl-8b',
  apiKey: 'not-needed',
  embeddingUrl: 'http://localhost:9123/v1',
  embeddingModel: 'text-embedding-qwen3-embedding-8b',
  workspaceDir: path.join(os.homedir(), 'ChatOS', 'workspace'),
  wsPort: 8913,
  httpPort: 8913,
  dbPath: path.join(os.homedir(), 'ChatOS', 'data', 'agent-runtime.sqlite'),
  logDir: path.join(os.homedir(), 'ChatOS', 'logs'),
  maxToolIterations: 20,
  modelTimeoutMs: 120000,
  maxTokens: 4096,
  temperature: 0.3,
  systemPrompt: DEFAULT_SYSTEM_PROMPT,
};

export function loadConfig(overrides?: Partial<AgentRuntimeConfig>): AgentRuntimeConfig {
  return { ...defaultConfig, ...overrides };
}
