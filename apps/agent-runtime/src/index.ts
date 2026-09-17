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

let shuttingDown = false;
const shutdown = async () => {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log('\n[AgentRuntime] shutting down...');
  await server.stop();
};
const requestShutdown = () => {
  void shutdown().catch((err) => {
    console.error('[AgentRuntime] shutdown failed:', err);
    process.exitCode = 1;
  });
};

process.on('SIGINT', requestShutdown);
process.on('SIGTERM', requestShutdown);

server.start().catch((err) => {
  console.error('[AgentRuntime] fatal:', err);
  process.exit(1);
});
