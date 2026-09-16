/**
 * Minimal demo: start codex, initialize, create thread, send "list files in current dir"
 * Run: npx tsx src/demo.ts
 */
import { loadConfig } from './config.js';
import { CodexClient } from './CodexClient.js';
import fs from 'node:fs';

const config = loadConfig();
fs.mkdirSync(config.workspaceDir, { recursive: true });

const client = new CodexClient(config);

client.on('event', (event) => {
  if (event.type === 'text-delta') {
    process.stdout.write(event.text);
  } else if (event.type === 'tool-call') {
    console.log(`\n[tool-call] ${event.tool}`);
  } else if (event.type === 'tool-result') {
    console.log(`[tool-result] ${event.tool}: ${String(event.result).slice(0, 100)}`);
  } else if (event.type === 'turn-complete') {
    console.log('\n[turn-complete]');
  } else if (event.type === 'error') {
    console.error(`\n[error] ${JSON.stringify(event)}`);
  } else if (event.type === 'raw') {
    console.error(`[raw] ${event.method}: ${JSON.stringify(event.params).slice(0, 200)}`);
  } else if (event.type === 'log') {
    console.error(`[log:${event.level}] ${String(event.message).slice(0, 200)}`);
  }
});

async function main() {
  console.log('[demo] starting codex app-server...');
  await client.start();

  console.log('[demo] initializing...');
  const init = await client.initialize();
  console.log(`[demo] initialized: codexHome=${init.codexHome} platform=${init.platform}`);

  console.log('[demo] starting thread...');
  const { threadId } = await client.startThread({ cwd: config.workspaceDir });
  console.log(`[demo] threadId=${threadId}`);

  console.log('[demo] sending message: "列出当前目录的文件"...');
  await client.startTurn(threadId, '列出当前目录的文件');

  // Wait for turn to complete
  await new Promise<void>((resolve) => {
    const onEvent = (event: any) => {
      if (event.type === 'turn-complete' || event.type === 'error') {
        client.off('event', onEvent);
        resolve();
      }
    };
    client.on('event', onEvent);
    // Safety timeout
    setTimeout(() => { client.off('event', onEvent); resolve(); }, 60000);
  });

  console.log('\n[demo] done. stopping...');
  client.stop();
  process.exit(0);
}

main().catch((err) => {
  console.error('[demo] fatal:', err);
  client.stop();
  process.exit(1);
});
