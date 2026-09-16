import { EventEmitter } from 'node:events';
import type { AgentRuntimeConfig } from './config.js';
import type { ToolDefinition as OpenAIToolDef } from './LocalModelClient.js';

export interface ToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  handler: (args: Record<string, unknown>) => Promise<string>;
}

export class ToolRegistry extends EventEmitter {
  private tools = new Map<string, ToolDefinition>();
  private config: AgentRuntimeConfig;

  constructor(config: AgentRuntimeConfig) {
    super();
    this.config = config;
    this.registerDefaults();
  }

  register(tool: ToolDefinition): void {
    this.tools.set(tool.name, tool);
  }

  get(name: string): ToolDefinition | undefined {
    return this.tools.get(name);
  }

  list(): ToolDefinition[] {
    return Array.from(this.tools.values());
  }

  /** 返回 OpenAI 格式的 tool definitions */
  getToolDefinitions(): OpenAIToolDef[] {
    return Array.from(this.tools.values()).map((t) => ({
      type: 'function' as const,
      function: {
        name: t.name,
        description: t.description,
        parameters: t.parameters,
      },
    }));
  }

  async execute(name: string, args: Record<string, unknown>): Promise<string> {
    const tool = this.tools.get(name);
    if (!tool) throw new Error(`tool not found: ${name}`);
    this.emit('tool-call', { name, args });
    try {
      const result = await tool.handler(args);
      this.emit('tool-result', { name, result });
      return result;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.emit('tool-error', { name, error: msg });
      return `Error: ${msg}`;
    }
  }

  private registerDefaults(): void {
    // Embedding tool - calls llama-swap
    this.register({
      name: 'generate_embedding',
      description: 'Generate text embedding via local llama-swap. Returns vector dimension and storage path.',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: 'Text to embed' },
          outputPath: { type: 'string', description: 'Optional path to save embedding JSON' },
        },
        required: ['text'],
      },
      handler: async (args) => {
        const text = String(args.text);
        const resp = await fetch(`${this.config.embeddingUrl}/embeddings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: 'text-embedding-qwen3-embedding-8b', input: text }),
        });
        if (!resp.ok) throw new Error(`embedding API ${resp.status}`);
        const data = await resp.json() as any;
        const vec = data.data?.[0]?.embedding || [];
        return `Embedding generated: dim=${vec.length}, first5=[${vec.slice(0, 5).join(', ')}]`;
      },
    });

    // File read tool
    this.register({
      name: 'read_file',
      description: 'Read a text file from workspace. Returns file content.',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Relative path within workspace' },
        },
        required: ['path'],
      },
      handler: async (args) => {
        const fs = await import('node:fs/promises');
        const path = await import('node:path');
        const full = path.resolve(this.config.workspaceDir, String(args.path));
        if (!full.startsWith(this.config.workspaceDir)) throw new Error('path outside workspace');
        return fs.readFile(full, 'utf-8');
      },
    });

    // File write tool
    this.register({
      name: 'write_file',
      description: 'Write text content to a file in workspace. Creates directories if needed.',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Relative path within workspace' },
          content: { type: 'string', description: 'File content to write' },
        },
        required: ['path', 'content'],
      },
      handler: async (args) => {
        const fs = await import('node:fs/promises');
        const path = await import('node:path');
        const full = path.resolve(this.config.workspaceDir, String(args.path));
        if (!full.startsWith(this.config.workspaceDir)) throw new Error('path outside workspace');
        await fs.mkdir(path.dirname(full), { recursive: true });
        await fs.writeFile(full, String(args.content), 'utf-8');
        return `Written ${String(args.content).length} bytes to ${args.path}`;
      },
    });

    // File list tool
    this.register({
      name: 'list_files',
      description: 'List files in a workspace directory.',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Relative directory path (default: root)' },
        },
      },
      handler: async (args) => {
        const fs = await import('node:fs/promises');
        const path = await import('node:path');
        const dir = String(args.path || '.');
        const full = path.resolve(this.config.workspaceDir, dir);
        if (!full.startsWith(this.config.workspaceDir)) throw new Error('path outside workspace');
        const entries = await fs.readdir(full, { withFileTypes: true });
        return entries.map(e => `${e.isDirectory() ? '[DIR] ' : ''}${e.name}`).join('\n');
      },
    });

    // Quality check tool
    this.register({
      name: 'quality_check',
      description: 'Run quality check on a data file (missing values, format, duplicates).',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Path to CSV/JSON file' },
        },
        required: ['path'],
      },
      handler: async (args) => {
        const fs = await import('node:fs/promises');
        const path = await import('node:path');
        const full = path.resolve(this.config.workspaceDir, String(args.path));
        if (!full.startsWith(this.config.workspaceDir)) throw new Error('path outside workspace');
        const content = await fs.readFile(full, 'utf-8');
        const lines = content.split('\n').filter(l => l.trim());
        const emptyLines = content.split('\n').filter(l => !l.trim()).length;
        const uniqueLines = new Set(lines).size;
        return `Quality check: lines=${lines.length}, empty=${emptyLines}, duplicates=${lines.length - uniqueLines}, unique=${uniqueLines}`;
      },
    });
  }
}
