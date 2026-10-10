import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import type { AgentRuntimeConfig } from './config.js';
import type { ToolDefinition as OpenAIToolDef } from './LocalModelClient.js';

const MAX_READ_FILE_BYTES = 64 * 1024;
const MAX_TOOL_FILE_BYTES = 1024 * 1024;
const MAX_LIST_ENTRIES = 1000;

export interface ToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  handler: (args: Record<string, unknown>, signal?: AbortSignal) => Promise<string>;
}

export class ToolRegistry extends EventEmitter {
  private tools = new Map<string, ToolDefinition>();
  private config: AgentRuntimeConfig;
  private workspaceRoot: string;
  private workspaceReal: string;

  constructor(config: AgentRuntimeConfig) {
    super();
    this.config = config;
    this.workspaceRoot = path.resolve(config.workspaceDir);
    fs.mkdirSync(this.workspaceRoot, { recursive: true });
    this.workspaceReal = fs.realpathSync.native(this.workspaceRoot);
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

  async execute(name: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
    const tool = this.tools.get(name);
    if (!tool) throw new Error(`tool not found: ${name}`);
    signal?.throwIfAborted();
    this.emit('tool-call', { name, args });
    try {
      const result = await tool.handler(args, signal);
      this.emit('tool-result', { name, result });
      return result;
    } catch (err) {
      if (signal?.aborted) throw err;
      const msg = this.publicErrorMessage(err);
      this.emit('tool-error', { name, error: msg });
      return `Error: ${msg}`;
    }
  }

  private publicErrorMessage(err: unknown): string {
    const code = err !== null && typeof err === 'object' && 'code' in err
      ? String(err.code)
      : '';
    if (code === 'ENOENT') return 'file not found';
    if (code === 'EACCES' || code === 'EPERM') return 'permission denied';
    if (code) return `operation failed (${code})`;
    return err instanceof Error ? err.message : 'tool execution failed';
  }

  private stringArg(args: Record<string, unknown>, name: string, allowEmpty = false): string {
    const value = args[name];
    if (typeof value !== 'string') throw new Error(`${name} must be a string`);
    if (!allowEmpty && !value.trim()) throw new Error(`${name} must be a non-empty string`);
    return value;
  }

  private resolveWorkspacePath(input: unknown): string {
    const full = path.resolve(this.workspaceRoot, String(input));
    const relative = path.relative(this.workspaceRoot, full);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new Error('path outside workspace');
    }
    return full;
  }

  private assertRealPathInsideWorkspace(realPath: string): void {
    const relative = path.relative(this.workspaceReal, realPath);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new Error('path outside workspace');
    }
  }

  private async resolveExistingWorkspacePath(input: unknown): Promise<string> {
    const full = this.resolveWorkspacePath(input);
    const real = await fsp.realpath(full);
    this.assertRealPathInsideWorkspace(real);
    return real;
  }

  private async resolveWorkspaceWritePath(input: unknown): Promise<string> {
    const full = this.resolveWorkspacePath(input);
    const parent = path.dirname(full);
    let existingParent = parent;
    while (!fs.existsSync(existingParent)) existingParent = path.dirname(existingParent);
    this.assertRealPathInsideWorkspace(await fsp.realpath(existingParent));
    await fsp.mkdir(parent, { recursive: true });
    this.assertRealPathInsideWorkspace(await fsp.realpath(parent));
    try {
      const target = await fsp.lstat(full);
      if (target.isSymbolicLink()) throw new Error('path outside workspace');
      this.assertRealPathInsideWorkspace(await fsp.realpath(full));
    } catch (err) {
      const code = err !== null && typeof err === 'object' && 'code' in err ? String(err.code) : '';
      if (code !== 'ENOENT') throw err;
    }
    return full;
  }

  private registerDefaults(): void {
    // Embedding tool - calls llama-swap
    this.register({
      name: 'generate_embedding',
      description: 'Generate text embedding via local llama-swap. Returns vector dimension and preview.',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: 'Text to embed' },
        },
        required: ['text'],
      },
      handler: async (args, turnSignal) => {
        const text = this.stringArg(args, 'text');
        let resp: Response;
        try {
          const timeoutSignal = AbortSignal.timeout(this.config.modelTimeoutMs);
          resp = await fetch(`${this.config.embeddingUrl}/embeddings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: this.config.embeddingModel, input: text }),
            redirect: 'error',
            signal: turnSignal ? AbortSignal.any([turnSignal, timeoutSignal]) : timeoutSignal,
          });
        } catch (err) {
          if (err instanceof DOMException && err.name === 'TimeoutError') {
            throw new Error('embedding request timed out');
          }
          throw err;
        }
        if (!resp.ok) throw new Error(`embedding API ${resp.status}`);
        const data = await resp.json() as { data?: Array<{ embedding?: unknown }> };
        const vec = data.data?.[0]?.embedding;
        if (!Array.isArray(vec) || vec.length !== 1024
          || !vec.every((value) => typeof value === 'number' && Number.isFinite(value))
          || !vec.some((value) => value !== 0)) {
          throw new Error('embedding API returned an invalid vector');
        }
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
        const full = await this.resolveExistingWorkspacePath(this.stringArg(args, 'path'));
        const stat = await fsp.stat(full);
        if (stat.size > MAX_READ_FILE_BYTES) {
          throw new Error(`file exceeds ${MAX_READ_FILE_BYTES} byte limit`);
        }
        return fsp.readFile(full, 'utf-8');
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
      handler: async (args, signal) => {
        const content = this.stringArg(args, 'content', true);
        const contentBytes = Buffer.byteLength(content);
        if (contentBytes > MAX_TOOL_FILE_BYTES) {
          throw new Error(`content exceeds ${MAX_TOOL_FILE_BYTES} byte limit`);
        }
        const filePath = this.stringArg(args, 'path');
        const full = await this.resolveWorkspaceWritePath(filePath);
        signal?.throwIfAborted();
        await fsp.writeFile(full, content, 'utf-8');
        return `Written ${contentBytes} bytes to ${filePath}`;
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
        const dir = args.path === undefined ? '.' : this.stringArg(args, 'path');
        const full = await this.resolveExistingWorkspacePath(dir);
        const formatted: string[] = [];
        for await (const entry of await fsp.opendir(full)) {
          formatted.push(`${entry.isDirectory() ? '[DIR] ' : ''}${entry.name}`);
          if (formatted.length > MAX_LIST_ENTRIES) break;
        }
        const truncated = formatted.length > MAX_LIST_ENTRIES;
        if (truncated) formatted.length = MAX_LIST_ENTRIES;
        formatted.sort();
        if (truncated) formatted.push('[truncated: more entries]');
        return formatted.join('\n');
      },
    });

    // Quality check tool
    this.register({
      name: 'quality_check',
      description: 'Report non-empty, empty, duplicate, and unique line counts for a text data file.',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Path to text file' },
        },
        required: ['path'],
      },
      handler: async (args) => {
        const full = await this.resolveExistingWorkspacePath(this.stringArg(args, 'path'));
        const stat = await fsp.stat(full);
        if (stat.size > MAX_TOOL_FILE_BYTES) {
          throw new Error(`file exceeds ${MAX_TOOL_FILE_BYTES} byte limit`);
        }
        const content = await fsp.readFile(full, 'utf-8');
        const allLines = content.split(/\r?\n/);
        const lines = allLines.filter(l => l.trim());
        const emptyLines = allLines.length - lines.length;
        const uniqueLines = new Set(lines).size;
        return `Quality check: lines=${lines.length}, empty=${emptyLines}, duplicates=${lines.length - uniqueLines}, unique=${uniqueLines}`;
      },
    });
  }
}
