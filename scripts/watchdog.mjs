// ChatOS 进程外看门狗（由计划任务每 5 分钟拉起一次）：
// 探测网关 8912 /api/health 与 llama-swap 9123 /running；任一死亡则自动拉起并留痕 var/logs/watchdog.log。
// 与 --watch（进程内守护）互补：本脚本是进程外兜底，二者可并存。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const logs = path.join(root, 'var/logs');
fs.mkdirSync(logs, { recursive: true });
const logFile = path.join(logs, 'watchdog.log');
const stateFile = path.join(logs, '.watchdog-state.json');

const GATEWAY_URL = 'http://127.0.0.1:8912';
const LLAMA_URL = 'http://127.0.0.1:9123/running';
const LLAMA_TASK = '\\LlamaSwap';
const COOLDOWN_MS = 4 * 60 * 1000; // 刚拉起过就等下一轮，避免与启动器自己的 45s 验收窗口撞车

function log(line) {
  const text = `${new Date().toISOString()} ${line}\n`;
  fs.appendFileSync(logFile, text);
  console.log(text.trim());
}

async function alive(url, expectReady) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000), redirect: 'error' });
    if (!expectReady) return res.ok || res.status > 0; // 有响应即进程活着
    const body = await res.json();
    return body?.app === 'ready';
  } catch {
    return false;
  }
}

function readState() {
  try { return JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch { return {}; }
}
function writeState(patch) {
  fs.writeFileSync(stateFile, JSON.stringify({ ...readState(), ...patch }));
}

async function main() {
  const state = readState();
  const gatewayOk = await alive(`${GATEWAY_URL}/api/health`, true);
  const llamaOk = await alive(LLAMA_URL, false);
  log(`probe gateway=${gatewayOk ? 'up' : 'DOWN'} llama=${llamaOk ? 'up' : 'DOWN'}`);

  const now = Date.now();
  if (!gatewayOk) {
    if (now - (state.lastGatewayStartAt || 0) < COOLDOWN_MS) {
      log('gateway DOWN but just restarted <4min ago; skip this round');
    } else {
      log('gateway DOWN -> launching node scripts\\start_chatos.mjs (detached)');
      try {
        const child = spawn(process.execPath, [path.join(root, 'scripts/start_chatos.mjs')], {
          cwd: root, windowsHide: true, detached: true, stdio: 'ignore',
        });
        child.unref();
        writeState({ lastGatewayStartAt: now });
        log(`start_chatos.mjs spawned PID ${child.pid}`);
      } catch (error) {
        log(`FATAL start_chatos spawn failed: ${error.message}`);
      }
    }
  }

  if (!llamaOk) {
    if (now - (state.lastLlamaStartAt || 0) < COOLDOWN_MS) {
      log('llama-swap DOWN but just restarted <4min ago; skip this round');
    } else {
      log('llama-swap DOWN -> schtasks /run \\LlamaSwap');
      try {
        const out = execFileSync('schtasks', ['/run', '/tn', LLAMA_TASK], { encoding: 'utf8', windowsHide: true });
        writeState({ lastLlamaStartAt: now });
        log(`schtasks /run ok: ${out.trim()}`);
      } catch (error) {
        log(`FATAL schtasks /run failed: ${error.message}`);
      }
    }
  }
}

main().catch(error => log(`FATAL watchdog: ${error.message}`));
