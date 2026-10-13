import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import {
  verifyBuild, validateLibraryDb, gatewayIdentity, inspectService,
  GATEWAY_URL, UI_URL,
} from './entry-contract.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const gate = path.join(root, 'scripts/verify-entry.mjs');
const gateway = path.join(root, 'apps/gateway');
const consoleRoot = path.join(root, 'apps/chatos-console');
const python = path.join(gateway, '.venv/Scripts/python.exe');
const logs = path.join(root, 'var/logs');
const args = process.argv.slice(2);
const watchMode = args.includes('--watch');
const statusMode = args.includes('--status');
const withUi = process.env.CHATOS_UI === '1' || args.includes('--with-ui');

const RULE = '='.repeat(68);
const SUB = '-'.repeat(68);

// 清理 execFileSync 子进程 stderr 里的 ExperimentalWarning 噪音行，只留业务信息
function cleanText(text) {
  return String(text || '').split('\n')
    .filter(line => line.trim() && !line.startsWith('(node:') && !line.includes('--trace-warnings'))
    .join('\n').trim();
}

// 显眼错误块：杜绝"看起来成功"。任何预检/启动失败都走这里。
function failBlock(bad) {
  const reason = cleanText(bad) || '(无详情)';
  let next = '';
  if (reason.includes('Source not Git tracked')) {
    next = '新增/改动的源码文件必须先 `git add` 暂存，再启动（预检拒绝未入 Git 的源码）。';
  } else if (reason.includes('Occupied port')) {
    next = `先跑 \`node scripts\\start_chatos.mjs --status\` 查端口占用；确认占用进程是旧 ChatOS 再手动停止，然后重试。本启动器不杀身份不明的进程。`;
  } else if (reason.includes('Stale build')) {
    next = `cd apps\\chatos-console && npm run build，然后重试。`;
  } else if (reason.includes('Startup not verified')) {
    next = `查看日志 var\\logs\\gateway.stderr.log；确认 llama-swap（LOCAL-MODELS.md）可用后重试。`;
  } else {
    next = '根据上方原因修正后重试；仍异常时看 var\\logs\\ 日志。';
  }
  console.error(RULE);
  console.error('⚠️  ChatOS 启动失败（非 0 退出，服务未启动）');
  console.error(SUB);
  console.error(`原因: ${reason}`);
  console.error(`下一步: ${next}`);
  console.error(RULE);
}

function fail(bad) {
  failBlock(bad);
  process.exitCode = 1;
}

// 预检门控：源码/构建/库一致性（--status 诊断模式不卡在这里，见 status()）
function preflightGates() {
  verifyBuild(root);
  validateLibraryDb(root);
  fs.mkdirSync(logs, { recursive: true });
  if (!fs.existsSync(python)) throw new Error('Missing gateway .venv; run uv sync in apps/gateway');
}

// 一次性拉起（detached + unref，退出后服务继续跑）。仅在 one-shot 模式用。
async function startOnce(name, executable, spawnArgs, cwd) {
  const out = fs.openSync(path.join(logs, `${name}.stdout.log`), 'a');
  const err = fs.openSync(path.join(logs, `${name}.stderr.log`), 'a');
  try {
    const child = spawn(executable, spawnArgs, { cwd, windowsHide: true, detached: true, stdio: ['ignore', out, err] });
    await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
    child.unref();
    console.log(`Started ${name} PID ${child.pid}`);
  } finally { fs.closeSync(out); fs.closeSync(err); }
}

// 查端口被哪个 PID/可执行文件占用（--status / occupied 提示用）
function portOwner(port) {
  try {
    const pid = Number(execFileSync('powershell', ['-NoProfile', '-Command',
      `(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)`],
      { encoding: 'utf8', windowsHide: true }).trim());
    if (!pid) return null;
    const pathName = execFileSync('powershell', ['-NoProfile', '-Command',
      `(Get-Process -Id ${pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path)`],
      { encoding: 'utf8', windowsHide: true }).trim();
    return { pid, path: pathName || '(路径未知)' };
  } catch { return null; }
}

// 统一的服务状态分类：'absent' | 'reuse' | 'occupied'（inspectService  occupied 时会 throw）
async function classify(url, expected) {
  try {
    const state = await inspectService(url, expected);
    return { state };
  } catch (error) {
    return { state: 'occupied', error: cleanText(error.message) };
  }
}

async function gatewayHealth() {
  try {
    const health = await fetch(`${GATEWAY_URL}/api/health`, { signal: AbortSignal.timeout(3000) }).then(r => r.json());
    return health;
  } catch { return null; }
}

// ---------------- --status：一眼区分 进程在 / 端口在 / 服务健康 ----------------
async function status() {
  const expected = gatewayIdentity(root);
  const g = await classify(GATEWAY_URL, expected);
  const port = new URL(GATEWAY_URL).port;
  let line;
  if (g.state === 'absent') {
    line = `网关 8912: 端口无监听 → 服务未启动（跑 scripts\\start_chatos.bat 或 --watch 拉起）`;
  } else if (g.state === 'reuse') {
    const health = await gatewayHealth();
    if (health?.app === 'ready') {
      line = `网关 8912: ✅ 进程在 + 端口在 + 健康（app=ready），可正常消费 /api/*`;
    } else {
      line = `网关 8912: ⚠️ 端口在、运行时身份匹配，但 /api/health 未就绪（启动中或异常，看 var\\logs\\gateway.stderr.log）`;
    }
  } else {
    const owner = portOwner(port);
    line = `网关 8912: ❌ 端口被占用但身份不兼容${owner ? `（PID ${owner.pid} → ${owner.path}）` : ''}\n         ${g.error}\n         → 先确认占用进程归属；只在确认是旧 ChatOS 时手动停止，再启动。本工具不杀进程。`;
  }
  const u = await classify(UI_URL, expected);
  const uiLine = u.state === 'absent'
    ? `UI 3108: 无监听（正常，演示层默认关闭；CHATOS_UI=1 才需要）`
    : u.state === 'reuse'
      ? `UI 3108: 运行中（演示层）`
      : `UI 3108: 被占用但身份不兼容（${u.error}）`;
  console.log(`ChatOS 状态（${new Date().toLocaleString()}）`);
  console.log(SUB);
  console.log(line);
  console.log(uiLine);
  console.log(SUB);
  process.exitCode = g.state === 'occupied' ? 2 : 0;
}

// ---------------- --watch：本进程为守护者，子进程退出自动重启 ----------------
function spawnSupervised(name, executable, spawnArgs, cwd) {
  const out = fs.openSync(path.join(logs, `${name}.stdout.log`), 'a');
  const err = fs.openSync(path.join(logs, `${name}.stderr.log`), 'a');
  const child = spawn(executable, spawnArgs, {
    cwd, windowsHide: true, detached: true, stdio: ['ignore', out, err],
  });
  child.once('spawn', () => {
    console.log(`[watch] ${name} 拉起 PID ${child.pid}`);
    if (name === 'gateway') waitReady();
  });
  child.once('error', error => {
    fs.closeSync(out); fs.closeSync(err);
    console.error(`[watch] ${name} spawn 失败: ${error.message}`);
    onExit(name, child);
  });
  child.once('exit', (code, signal) => {
    fs.closeSync(out); fs.closeSync(err);
    console.error(`[watch] ${name} 退出 code=${code} signal=${signal ?? '-'}，将按退避策略重启`);
    onExit(name, child);
  });
  return child;
}

const crashWindowMs = 30_000;
const crashLog = []; // 近期退出时间戳（守护视角的崩溃循环判定）
let gatewayChild = null;
let uiChild = null;
let restartTimer = null;
let stopping = false;

function onExit(name, child) {
  if (stopping) return;
  if (child === gatewayChild) gatewayChild = null;
  if (child === uiChild) uiChild = null;
  const now = Date.now();
  while (crashLog.length && now - crashLog[0] > crashWindowMs) crashLog.shift();
  crashLog.push(now);
  let waitMs = 2000;
  if (crashLog.length >= 3) {
    waitMs = Math.min(30_000 * (crashLog.length - 2), 120_000);
    console.error(`⚠️ [watch] ${crashWindowMs / 1000} 秒内 ${name} 已退出 ${crashLog.length} 次（疑似崩溃循环），退避 ${waitMs / 1000}s。请查 var\\logs\\gateway.stderr.log。`);
  }
  restartTimer = setTimeout(() => { if (!stopping) bootOnce(); }, waitMs);
}

async function waitReady() {
  for (const deadline = Date.now() + 45_000; Date.now() < deadline;) {
    await delay(1000);
    const health = await gatewayHealth();
    if (health?.app === 'ready') {
      crashLog.length = 0; // 稳定运行后清零崩溃计数
      console.log(`[watch] 网关就绪 ${GATEWAY_URL} (app=ready)`);
      return true;
    }
  }
  console.error('[watch] 网关 45s 内未就绪；继续观察，退出后将自动重启。');
  return false;
}

function bootOnce() {
  if (stopping) return;
  if (!gatewayChild) {
    gatewayChild = spawnSupervised('gateway', python,
      ['-m', 'wechat_ai', 'serve', '--port', new URL(GATEWAY_URL).port, '--no-browser'], gateway);
  }
  if (withUi && !uiChild) {
    uiChild = spawnSupervised('console', process.execPath,
      [path.join(consoleRoot, 'dist/server.cjs'), '--production'], consoleRoot);
  }
}

async function watch() {
  preflightGates();
  const g = await classify(GATEWAY_URL, gatewayIdentity(root));
  if (g.state === 'occupied') {
    const owner = portOwner(new URL(GATEWAY_URL).port);
    fail(`端口 ${new URL(GATEWAY_URL).port} 被身份不兼容的进程占用${owner ? `（PID ${owner.pid} → ${owner.path}）` : ''}。\n${g.error}`);
    return;
  }
  if (g.state === 'reuse') {
    console.log(`网关 ${GATEWAY_URL} 已在运行且身份匹配——但它不是本守护者拉起的，我不接管。`);
    console.log('如需由 --watch 守护：先手动停止现有网关，再重跑本命令。');
    process.exit(0);
    return;
  }
  console.log(`[watch] 守护模式启动：日志 var\\logs\\，Ctrl+C 停止守护与子进程。`);
  bootOnce();
  // 不 unref：必须持续感知子进程退出
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

function shutdown(sig) {
  if (stopping) return;
  stopping = true;
  clearTimeout(restartTimer);
  console.log(`[watch] 收到 ${sig}，停止子进程...`);
  for (const child of [gatewayChild, uiChild]) {
    if (!child) continue;
    try { child.kill(); } catch { /* 已退出 */ }
  }
  setTimeout(() => process.exit(0), 1500);
}

// ---------------- one-shot（默认）：保持原行为，但失败走显眼错误块 ----------------
async function oneshot() {
  let state;
  try {
    state = JSON.parse(execFileSync(process.execPath, [gate, '--preflight'], {
      cwd: root, encoding: 'utf8', windowsHide: true, stdio: 'pipe',
    }));
  } catch (error) {
    fail(error.stderr || error.message);
    return;
  }
  if (state.gateway === 'absent') await startOnce('gateway', python, ['-m', 'wechat_ai', 'serve', '--port', new URL(GATEWAY_URL).port, '--no-browser'], gateway);
  if (state.ui === 'absent' && withUi) await startOnce('console', process.execPath, [path.join(consoleRoot, 'dist/server.cjs'), '--production'], consoleRoot);
  let lastError;
  let warnedSlow = false;
  for (const deadline = Date.now() + 120_000; Date.now() < deadline;) {
    await delay(750);
    if (!warnedSlow && Date.now() > deadline - 75_000) {
      warnedSlow = true;
      console.error('⏳ 服务仍在启动中（迁移/WAL 恢复/模型预热较慢），继续等待…');
    }
    try {
      const verified = execFileSync(process.execPath, [gate, '--live'], { cwd: root, encoding: 'utf8', windowsHide: true, stdio: 'pipe' });
      console.log(cleanText(verified));
      console.log(`ChatOS: ${state.gatewayUrl}${withUi ? ` (UI ${state.uiUrl})` : ' (UI off)'}`);
      process.exit(0);
    } catch (error) { lastError = error.stderr?.toString() || error.message; }
  }
  fail(`Startup not verified: ${cleanText(lastError)} Logs: ${logs}. No processes were killed.`);
}

if (statusMode) {
  try { await status(); } catch (error) { fail(error.stderr?.toString() || error.message); }
} else if (watchMode) {
  try { await watch(); } catch (error) { fail(error.stderr?.toString() || error.message); }
} else {
  try {
    await oneshot();
  } catch (error) {
    fail(error.stderr?.toString() || error.message);
  }
}
