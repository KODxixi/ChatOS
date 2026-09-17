import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const gate = path.join(root, 'scripts/verify-entry.mjs');
try {
  const state = JSON.parse(execFileSync(process.execPath, [gate, '--preflight'], { cwd: root, encoding: 'utf8', windowsHide: true }));
  const gateway = path.join(root, 'apps/gateway');
  const consoleRoot = path.join(root, 'apps/chatos-console');
  const python = path.join(gateway, '.venv/Scripts/python.exe');
  if (!fs.existsSync(python)) throw new Error('Missing gateway .venv; run uv sync in apps/gateway');
  const logs = path.join(root, 'var/logs');
  fs.mkdirSync(logs, { recursive: true });
  const start = async (name, executable, args, cwd) => {
    const out = fs.openSync(path.join(logs, `${name}.stdout.log`), 'a');
    const err = fs.openSync(path.join(logs, `${name}.stderr.log`), 'a');
    try {
      const child = spawn(executable, args, { cwd, windowsHide: true, detached: true, stdio: ['ignore', out, err] });
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      child.unref();
      console.log(`Started ${name} PID ${child.pid}`);
    } finally { fs.closeSync(out); fs.closeSync(err); }
  };
  if (state.gateway === 'absent') await start('gateway', python, ['-m', 'wechat_ai', 'serve', '--port', new URL(state.gatewayUrl).port, '--no-browser'], gateway);
  if (state.ui === 'absent') await start('console', process.execPath, [path.join(consoleRoot, 'dist/server.cjs'), '--production'], consoleRoot);
  let lastError;
  for (const deadline = Date.now() + 45000; Date.now() < deadline;) {
    await delay(750);
    try {
      const verified = execFileSync(process.execPath, [gate, '--live'], { cwd: root, encoding: 'utf8', windowsHide: true, stdio: 'pipe' });
      console.log(verified.trim());
      console.log(`ChatOS: ${state.uiUrl}`);
      process.exit(0);
    } catch (error) { lastError = error.stderr?.toString() || error.message; }
  }
  throw new Error(`Startup not verified: ${lastError} Logs: ${logs}. No processes were killed.`);
} catch (error) {
  console.error(error.stderr?.toString() || error.message);
  process.exitCode = 1;
}
