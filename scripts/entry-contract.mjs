import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import net from 'node:net';
import { DatabaseSync } from 'node:sqlite';
import { execFileSync } from 'node:child_process';

export const UI_URL = 'http://127.0.0.1:3108';
export const GATEWAY_URL = 'http://127.0.0.1:8912';
export const SOURCE_RELATIVE = 'apps/chatos-console';
export const ORIGIN_COMMIT = 'e9d78e9c3f1c216c668a4810fe890d764131b88a';
export const SOURCE_INPUTS = [
  ...['src/', 'public/', 'scripts/', 'server.ts', 'index.html', 'package.json',
    'package-lock.json', 'tsconfig.json', 'vite.config.ts'].map(name => `${SOURCE_RELATIVE}/${name}`),
  'scripts/entry-contract.mjs', 'scripts/verify-entry.mjs',
  'scripts/start_chatos.mjs', 'scripts/start_chatos.bat',
];

export function filesUnder(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    if (entry.isSymbolicLink()) throw new Error(`Linked source/output is not canonical: ${entry.name}`);
    if (entry.name === '__pycache__') return [];
    const target = path.join(root, entry.name);
    return entry.isDirectory() ? filesUnder(target) : [target];
  });
}

export function hashFiles(root, files) {
  const hash = crypto.createHash('sha256');
  const entries = files.map(file => [path.relative(root, file).replaceAll('\\', '/'), file]);
  entries.sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  for (const [name, file] of entries) hash.update(name).update('\0').update(fs.readFileSync(file)).update('\0');
  return hash.digest('hex');
}

export function sourceFiles(root) {
  return SOURCE_INPUTS.flatMap(name => name.endsWith('/') ? filesUnder(path.join(root, name)) : [path.join(root, name)]);
}

export function sourceIdentity(root) {
  const sourceHash = hashFiles(root, sourceFiles(root));
  return { contractVersion: 1, sourceRelative: SOURCE_RELATIVE, originCommit: ORIGIN_COMMIT, sourceHash, buildId: sourceHash };
}

export function buildProvenance(root) {
  const git = args => execFileSync('git', args, { cwd: root, encoding: 'utf8', windowsHide: true }).trim();
  return {
    repositoryCommit: git(['rev-parse', 'HEAD']),
    sourceDirty: git(['status', '--porcelain', '--untracked-files=all', '--', ...SOURCE_INPUTS]) !== '',
  };
}

export function outputIdentity(dist) {
  return hashFiles(dist, filesUnder(dist).filter(file => path.relative(dist, file) !== 'build.json'));
}

export function verifyBuild(root) {
  const tracked = new Set(execFileSync('git', ['ls-files', '-z', '--cached'], { cwd: root, encoding: 'utf8' }).split('\0'));
  for (const file of sourceFiles(root)) {
    const relative = path.relative(root, file).replaceAll('\\', '/');
    if (!tracked.has(relative)) throw new Error(`Source not Git tracked (stage it first): ${relative}`);
  }
  const dist = path.join(root, SOURCE_RELATIVE, 'dist');
  const build = JSON.parse(fs.readFileSync(path.join(dist, 'build.json'), 'utf8'));
  for (const [key, value] of Object.entries(sourceIdentity(root))) {
    if (build[key] !== value) throw new Error(`Stale build ${key}; run npm run build in ${SOURCE_RELATIVE}`);
  }
  for (const file of ['index.html', 'server.cjs']) {
    if (!fs.statSync(path.join(dist, file)).isFile()) throw new Error(`Missing build output: ${file}`);
  }
  if (build.outputHash !== outputIdentity(dist)) throw new Error('Build output identity mismatch; rebuild');
  return build;
}

export function settingsPath(root) {
  return path.join(root, 'apps/gateway/config/settings.json');
}

export function resolveLibraryDb(root) {
  const settings = JSON.parse(fs.readFileSync(settingsPath(root), 'utf8'));
  if (typeof settings.libraryDb !== 'string' || !settings.libraryDb.trim()) throw new Error('libraryDb is missing from gateway settings');
  return path.resolve(root, 'apps/gateway', settings.libraryDb);
}

export function validateLibraryDb(root) {
  const file = resolveLibraryDb(root);
  let database;
  try {
    if (!fs.lstatSync(file).isFile()) throw new Error('expected an existing ordinary file');
    database = new DatabaseSync(file, { readOnly: true });
    const tables = new Set(database.prepare("SELECT name FROM sqlite_schema WHERE type = 'table'").all().map(row => row.name));
    const required = {
      app_meta: 'key, value',
      conversations: 'id, title, kind, message_count, last_message_at, is_favorite, is_indexing_enabled, domain, project_id, project_role',
      messages: 'id, conversation_id, sender_id, sender_name, sent_at, type, content',
      message_parts: 'id',
      assets: 'id',
      tasks: 'task_id, conversation_id, content, sender_name, sent_at, due_date, priority, status, updated_at',
    };
    for (const [table, columns] of Object.entries(required)) {
      if (!tables.has(table)) throw new Error(`missing library table: ${table}`);
      database.prepare(`SELECT ${columns} FROM ${table} LIMIT 0`).all();
    }
    return file;
  } catch (error) {
    throw new Error(`Invalid libraryDb "${file}": ${error.message}. Point gateway settings at the existing ChatOS library; no database was created.`);
  } finally {
    database?.close();
  }
}

export function captureUiRuntime(root, embeddedBuild, codePath) {
  return {
    ...embeddedBuild,
    service: 'chatos-console',
    sourceRoot: path.resolve(root, SOURCE_RELATIVE),
    configPath: settingsPath(root),
    configIdentity: crypto.createHash('sha256').update(fs.readFileSync(settingsPath(root))).digest('hex'),
    libraryDb: resolveLibraryDb(root),
    startupCodeIdentity: crypto.createHash('sha256').update(fs.readFileSync(codePath)).digest('hex'),
    outputHash: outputIdentity(path.join(root, SOURCE_RELATIVE, 'dist')),
  };
}

export function gatewayIdentity(root) {
  const gateway = path.join(root, 'apps/gateway');
  const files = filesUnder(path.join(gateway, 'src')).filter(file => /\.(py|sql)$/.test(file));
  for (const name of ['pyproject.toml', 'uv.lock']) if (fs.existsSync(path.join(gateway, name))) files.push(path.join(gateway, name));
  return {
    service: 'chatos-gateway', sourceRoot: gateway,
    startupCodeIdentity: hashFiles(gateway, files),
    libraryDb: resolveLibraryDb(root), frontendUrl: UI_URL,
    configPath: settingsPath(root),
    configIdentity: crypto.createHash('sha256').update(fs.readFileSync(settingsPath(root))).digest('hex'),
  };
}

export function assertRuntime(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    if (actual?.[key] !== value) throw new Error(`Stale or unrelated runtime identity: ${key}`);
  }
}

export async function inspectService(url, expected) {
  const address = new URL(url);
  const occupied = await new Promise((resolve, reject) => {
    const socket = net.connect({ host: address.hostname, port: Number(address.port) });
    socket.setTimeout(1500);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('timeout', () => { socket.destroy(); reject(new Error(`Port state unknown: ${url}`)); });
    socket.once('error', error => error.code === 'ECONNREFUSED' ? resolve(false) : reject(error));
  });
  if (!occupied) return 'absent';
  try {
    const response = await fetch(`${url}/api/runtime`, { signal: AbortSignal.timeout(2000), redirect: 'error' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    assertRuntime(await response.json(), expected);
    return 'reuse';
  } catch (error) {
    throw new Error(`Occupied port ${url}: ${error.message}. Identify its PID/source; stop only a proven ChatOS process, then rerun scripts/start_chatos.bat. No process was killed.`);
  }
}
