import { loadConfig } from './config.js';
import { AgentRuntimeServer } from './server.js';
import fs from 'node:fs';
import path from 'node:path';

const config = loadConfig();

// Ensure directories exist
fs.mkdirSync(config.workspaceDir, { recursive: true });
fs.mkdirSync(path.dirname(config.dbPath), { recursive: true });
fs.mkdirSync(config.logDir, { recursive: true });

const server = new AgentRuntimeServer(config);

process.on('SIGINT', () => {
  console.log('\n[AgentRuntime] shutting down...');
  server.stop();
  process.exit(0);
});

process.on('SIGTERM', () => {
  server.stop();
  process.exit(0);
});

server.start().catch((err) => {
  console.error('[AgentRuntime] fatal:', err);
  process.exit(1);
});
