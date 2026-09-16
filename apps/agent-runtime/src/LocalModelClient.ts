/**
 * LocalModelClient - 直连 LM Studio OpenAI 兼容 API 的 Agent Runtime
 *
 * 不依赖 Codex CLI，直接调用 http://localhost:1234/v1/chat/completions
 * 支持流式输出 + tool-calling agent loop
 */
import { EventEmitter } from 'node:events';
import type { ToolRegistry } from './ToolRegistry.js';

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

export interface ToolDefinition {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

export interface AgentEvent {
  type: 'text-delta' | 'tool-call' | 'tool-result' | 'turn-complete' | 'error' | 'thinking';
  text?: string;
  toolName?: string;
  toolArgs?: string;
  toolResult?: string;
  error?: string;
}

export interface LocalModelConfig {
  baseUrl: string;
  model: string;
  apiKey: string;
  maxTokens: number;
  temperature: number;
  maxToolIterations: number;
  requestTimeoutMs: number;
  systemPrompt: string;
}

const DEFAULT_SYSTEM_PROMPT = `你是 ChatOS 的本地 Agent 运行时，运行在用户的 Windows 机器上。
所有推理本地完成，不调用外部 API。
你可以使用提供的工具来完成任务。执行工具后根据结果继续推理，直到任务完成。
回复简洁直接，中文优先。`;

export class LocalModelClient extends EventEmitter {
  private config: LocalModelConfig;
  private tools: ToolDefinition[] = [];
  private registry: ToolRegistry;
  private abortController: AbortController | null = null;
  private _isRunning = false;

  constructor(config: Partial<LocalModelConfig>, registry: ToolRegistry) {
    super();
    this.config = {
      baseUrl: config.baseUrl ?? 'http://localhost:1234/v1',
      model: config.model ?? 'qwen/qwen3-vl-8b',
      apiKey: config.apiKey ?? 'not-needed',
      maxTokens: config.maxTokens ?? 4096,
      temperature: config.temperature ?? 0.3,
      maxToolIterations: config.maxToolIterations ?? 20,
      requestTimeoutMs: config.requestTimeoutMs ?? 120000,
      systemPrompt: config.systemPrompt ?? DEFAULT_SYSTEM_PROMPT,
    };
    this.registry = registry;
    this.tools = this.registry.getToolDefinitions();
  }

  get isRunning() {
    return this._isRunning;
  }

  /** 健康检查：LM Studio 是否可达 */
  async healthCheck(): Promise<{ ok: boolean; model?: string; error?: string }> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      const res = await fetch(`${this.config.baseUrl}/models`, {
        headers: { Authorization: `Bearer ${this.config.apiKey}` },
        signal: controller.signal,
      });
      clearTimeout(timeout);
      if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
      const data = (await res.json()) as { data?: Array<{ id: string }> };
      const model = data.data?.find((m) => m.id === this.config.model)?.id;
      return { ok: true, model };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }

  /** 中断当前推理 */
  interrupt() {
    this.abortController?.abort();
    this._isRunning = false;
  }

  /**
   * 执行一轮 agent 推理（含 tool loop）
   * @param messages 完整对话历史
   * @returns 最终 assistant 消息
   */
  async runTurn(messages: ChatMessage[]): Promise<ChatMessage> {
    this._isRunning = true;
    let currentMessages: ChatMessage[] = [
      { role: 'system', content: this.config.systemPrompt },
      ...messages,
    ];
    let finalContent = '';

    try {
      for (let iteration = 0; iteration < this.config.maxToolIterations; iteration++) {
        const response = await this.streamChatCompletion(currentMessages);

        if (response.tool_calls && response.tool_calls.length > 0) {
          // 执行所有 tool calls
          currentMessages.push(response);

          for (const toolCall of response.tool_calls) {
            this.emit('event', {
              type: 'tool-call',
              toolName: toolCall.function.name,
              toolArgs: toolCall.function.arguments,
            } as AgentEvent);

            const result = await this.executeTool(toolCall);
            this.emit('event', {
              type: 'tool-result',
              toolName: toolCall.function.name,
              toolResult: result,
            } as AgentEvent);

            currentMessages.push({
              role: 'tool',
              tool_call_id: toolCall.id,
              name: toolCall.function.name,
              content: result,
            });
          }
          // 继续下一轮推理
          continue;
        }

        // 无 tool calls，本轮结束
        finalContent = response.content ?? '';
        this.emit('event', { type: 'turn-complete', text: finalContent } as AgentEvent);
        this._isRunning = false;
        return { role: 'assistant', content: finalContent };
      }

      // 达到最大迭代次数
      const msg = `达到最大工具调用次数 (${this.config.maxToolIterations})，停止推理。`;
      this.emit('event', { type: 'error', error: msg } as AgentEvent);
      this._isRunning = false;
      return { role: 'assistant', content: finalContent || msg };
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.emit('event', { type: 'error', error: errorMsg } as AgentEvent);
      this._isRunning = false;
      throw err;
    }
  }

  /** 执行单个工具 */
  private async executeTool(toolCall: ToolCall): Promise<string> {
    try {
      const args = JSON.parse(toolCall.function.arguments || '{}');
      const result = await this.registry.execute(toolCall.function.name, args);
      return typeof result === 'string' ? result : JSON.stringify(result, null, 2);
    } catch (err) {
      return `工具执行失败: ${err instanceof Error ? err.message : String(err)}`;
    }
  }

  /**
   * 流式调用 chat/completions
   * 解析 SSE，emit text-delta 事件，返回完整消息
   */
  private async streamChatCompletion(messages: ChatMessage[]): Promise<ChatMessage> {
    this.abortController = new AbortController();
    const timeout = setTimeout(() => this.abortController?.abort(), this.config.requestTimeoutMs);

    try {
      const res = await fetch(`${this.config.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.config.apiKey}`,
        },
        body: JSON.stringify({
          model: this.config.model,
          messages,
          tools: this.tools.length > 0 ? this.tools : undefined,
          tool_choice: this.tools.length > 0 ? 'auto' : undefined,
          stream: true,
          max_tokens: this.config.maxTokens,
          temperature: this.config.temperature,
        }),
        signal: this.abortController.signal,
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => '');
        throw new Error(`LM Studio API ${res.status}: ${errText.slice(0, 500)}`);
      }

      if (!res.body) throw new Error('Response body is null');

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let content = '';
      let reasoning = '';
      const toolCalls: Map<number, ToolCall> = new Map();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data:')) continue;
          const dataStr = trimmed.slice(5).trim();
          if (dataStr === '[DONE]') continue;

          try {
            const chunk = JSON.parse(dataStr) as {
              choices?: Array<{
                delta?: {
                  content?: string;
                  reasoning_content?: string;
                  tool_calls?: Array<{
                    index?: number;
                    id?: string;
                    type?: string;
                    function?: { name?: string; arguments?: string };
                  }>;
                };
              }>;
            };

            const delta = chunk.choices?.[0]?.delta;
            if (!delta) continue;

            if (delta.content) {
              content += delta.content;
              this.emit('event', { type: 'text-delta', text: delta.content } as AgentEvent);
            }

            if (delta.reasoning_content) {
              reasoning += delta.reasoning_content;
              this.emit('event', { type: 'thinking', text: delta.reasoning_content } as AgentEvent);
            }

            if (delta.tool_calls) {
              for (const tc of delta.tool_calls) {
                const idx = tc.index ?? 0;
                const existing = toolCalls.get(idx) ?? {
                  id: tc.id ?? '',
                  type: 'function' as const,
                  function: { name: '', arguments: '' },
                };
                if (tc.id) existing.id = tc.id;
                if (tc.function?.name) existing.function.name += tc.function.name;
                if (tc.function?.arguments) existing.function.arguments += tc.function.arguments;
                toolCalls.set(idx, existing);
              }
            }
          } catch {
            // 忽略解析错误的 chunk
          }
        }
      }

      const toolCallArray = Array.from(toolCalls.values()).filter((tc) => tc.function.name);
      return {
        role: 'assistant',
        content,
        tool_calls: toolCallArray.length > 0 ? toolCallArray : undefined,
      };
    } finally {
      clearTimeout(timeout);
      this.abortController = null;
    }
  }
}
