// ChatOS 进程外看门狗（由计划任务每 30 分钟拉起一次）：
// 探测网关 8912 /api/health 与 llama-swap 9123 /running；任一死亡则自动拉起并留痕 var/logs/watchdog.log。
// 与 --watch（进程内守护）互补：本脚本是进程外兜底，二者可并存。
//
// 30 分钟足够：agent 侧查询走只读直连（不依赖网关），后台同步自己会 ensure 网关，
// 本脚本只负责兜底恢复。连续拉起仍起不来时熔断并告警，不再无脑重试。
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
// 必须小于计划任务间隔（PT30M），否则冷却永远来不及生效。
const COOLDOWN_MS = 25 * 60 * 1000;
// 连续这么多轮"拉起后仍是 DOWN"就熔断，避免整夜空转。
const MAX_CONSECUTIVE_STARTS = 3;
// /api/health 会跑 operational 探针，依赖不在时实测要 4-6 秒；超时给太短会把健康网关判死、
// 触发无谓重启（这是历史上重复拉起的一个成因）。身份探针 /api/runtime 很快，不需要放宽。
const HEALTH_TIMEOUT_MS = 20_000;
const FAST_TIMEOUT_MS = 3_000;

function log(line) {
  const text = `${new Date().toISOString()} ${line}\n`;
  fs.appendFileSync(logFile, text);
  console.log(text.trim());
}

async function alive(url, expectReady, timeoutMs = FAST_TIMEOUT_MS) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), redirect: 'error' });
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
  const gatewayOk = await alive(`${GATEWAY_URL}/api/health`, true, HEALTH_TIMEOUT_MS);
  const llamaOk = await alive(LLAMA_URL, false, FAST_TIMEOUT_MS);
  log(`probe gateway=${gatewayOk ? 'up' : 'DOWN'} llama=${llamaOk ? 'up' : 'DOWN'}`);

  const now = Date.now();
  if (gatewayOk) {
    if (state.gatewayFailStreak || state.gatewayAlerted) {
      log('gateway 已恢复，清除熔断计数');
      writeState({ gatewayFailStreak: 0, gatewayAlerted: false });
    }
  } else {
    const streak = state.gatewayFailStreak || 0;
    if (streak >= MAX_CONSECUTIVE_STARTS) {
      if (!state.gatewayAlerted) {
        log(`ALERT gateway 连续 ${streak} 轮拉起后仍未就绪，停止自动重启。`);
        log('ALERT 查 var/logs/gateway.stderr.log；修好后等它自然恢复，或删 var/logs/.watchdog-state.json 重置熔断。');
        writeState({ gatewayAlerted: true });
      } else {
        log(`gateway DOWN；熔断中（连续 ${streak} 轮失败），本轮不再拉起。`);
      }
    } else if (now - (state.lastGatewayStartAt || 0) < COOLDOWN_MS) {
      log(`gateway DOWN，${Math.round(COOLDOWN_MS / 60000)} 分钟内刚拉起过，跳过本轮`);
    } else {
      log('gateway DOWN -> launching node scripts\\start_chatos.mjs (detached)');
      try {
        const child = spawn(process.execPath, [path.join(root, 'scripts/start_chatos.mjs')], {
          cwd: root, windowsHide: true, detached: true, stdio: 'ignore',
        });
        child.unref();
        writeState({ lastGatewayStartAt: now, gatewayFailStreak: streak + 1 });
        log(`start_chatos.mjs spawned PID ${child.pid}（连续第 ${streak + 1} 次）`);
      } catch (error) {
        log(`FATAL start_chatos spawn failed: ${error.message}`);
      }
    }
  }

  if (!llamaOk) {
    if (now - (state.lastLlamaStartAt || 0) < COOLDOWN_MS) {
      log(`llama-swap DOWN，${Math.round(COOLDOWN_MS / 60000)} 分钟内刚拉起过，跳过本轮`);
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
