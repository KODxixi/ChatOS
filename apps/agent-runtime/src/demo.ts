/**
 * 最小可运行 demo：直连 LM Studio，发送消息并展示流式输出 + tool calling
 */
import { LocalModelClient, type AgentEvent } from './LocalModelClient.js';
import { ToolRegistry } from './ToolRegistry.js';
import { loadConfig } from './config.js';
import fs from 'node:fs';
import path from 'node:path';

async function main() {
  const config = loadConfig();

  // 确保 workspace 存在
  fs.mkdirSync(config.workspaceDir, { recursive: true });

  const registry = new ToolRegistry(config);
  const client = new LocalModelClient({
    baseUrl: config.lmStudioUrl,
    model: config.model,
    apiKey: config.apiKey,
    maxTokens: config.maxTokens,
    temperature: config.temperature,
    maxToolIterations: config.maxToolIterations,
    requestTimeoutMs: config.modelTimeoutMs,
    systemPrompt: config.systemPrompt,
  }, registry);

  // 健康检查
  console.log('[demo] checking LM Studio health...');
  const health = await client.healthCheck();
  console.log(`[demo] health: ok=${health.ok} model=${health.model} error=${health.error || 'none'}`);
  if (!health.ok) {
    console.error('[demo] LM Studio not reachable. Is it running on :1234?');
    process.exit(1);
  }

  // 监听事件
  client.on('event', (event: AgentEvent) => {
    switch (event.type) {
      case 'text-delta':
        process.stdout.write(event.text || '');
        break;
      case 'thinking':
        // 不显示推理过程，保持干净
        break;
      case 'tool-call':
        console.log(`\n[tool] ${event.toolName}(${event.toolArgs})`);
        break;
      case 'tool-result':
        console.log(`[tool] → ${String(event.toolResult || '').slice(0, 200)}`);
        break;
      case 'turn-complete':
        console.log('\n[demo] turn complete');
        break;
      case 'error':
        console.error(`\n[error] ${event.error}`);
        break;
    }
  });

  // 测试消息
  const testMessage = process.argv[2] || '列出当前工作目录的文件，然后告诉我有哪些文件';
  console.log(`[demo] sending: "${testMessage}"\n`);

  const messages = [{ role: 'user' as const, content: testMessage }];
  const result = await client.runTurn(messages);
  console.log(`\n[demo] final: ${result.content.slice(0, 300)}`);
  console.log(`[demo] done.`);
  process.exit(0);
}

main().catch((err) => {
  console.error('[demo] fatal:', err);
  process.exit(1);
});
