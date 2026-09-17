import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import http from 'node:http';
import net from 'node:net';

const modulePath = new URL('../scripts/entry-contract.mjs', import.meta.url);

test('canonical source, build and occupied-port contract', async t => {
  assert.ok(fs.existsSync(modulePath), 'canonical identity implementation must exist');
  const c = await import(modulePath.href);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-entry-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const put = (name, value) => {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), value);
  };
  for (const name of c.SOURCE_INPUTS) {
    if (name.endsWith('/')) put(`${name}fixture.ts`, 'source');
    else put(name, name.endsWith('.json') ? '{}' : 'source');
  }
  put('apps/chatos-console/src/App.tsx', 'version one');
  put('apps/gateway/src/fixture.py', '# synthetic gateway source');
  put('apps/gateway/config/settings.json', JSON.stringify({ libraryDb: 'data/test.sqlite', frontendUrl: c.UI_URL }));
  put('apps/chatos-console/dist/index.html', '<div id="root"></div>');
  put('apps/chatos-console/dist/server.cjs', 'server version one');
  execFileSync('git', ['init', '-q', root]);
  const stage = () => execFileSync('git', ['-C', root, 'add', '.']);
  stage();
  const first = c.sourceIdentity(root);
  assert.equal(first.sourceRelative, 'apps/chatos-console');
  assert.equal(first.originCommit, 'e9d78e9c3f1c216c668a4810fe890d764131b88a');
  assert.equal(first.buildId, c.sourceIdentity(root).buildId, 'time must not change identity');
  const stamp = () => {
    const build = { ...c.sourceIdentity(root), outputHash: c.outputIdentity(path.join(root, 'apps/chatos-console/dist')) };
    put('apps/chatos-console/dist/build.json', JSON.stringify(build));
    return build;
  };
  let build = stamp();
  assert.equal(c.verifyBuild(root).buildId, build.buildId, 'staged source works before first commit');
  assert.equal(typeof c.buildProvenance, 'function', 'build must record repository revision and source dirty state');
  const commit = () => execFileSync('git', ['-C', root, '-c', 'user.name=Entry Test', '-c', 'user.email=entry@example.invalid', 'commit', '-qm', 'fixture']);
  commit();
  const provenance = c.buildProvenance(root);
  assert.match(provenance.repositoryCommit, /^[0-9a-f]{40}$/);
  assert.equal(provenance.sourceDirty, false);
  put('README.md', 'unrelated documentation');
  assert.equal(c.buildProvenance(root).sourceDirty, false);
  stage();
  commit();
  assert.notEqual(c.buildProvenance(root).repositoryCommit, provenance.repositoryCommit);
  assert.equal(c.verifyBuild(root).buildId, build.buildId, 'a docs-only commit does not stale a build');
  put('apps/chatos-console/src/App.tsx', 'version two');
  assert.equal(c.buildProvenance(root).sourceDirty, true);
  assert.notEqual(first.buildId, c.sourceIdentity(root).buildId);
  assert.throws(() => c.verifyBuild(root), /stale|fresh/i);
  build = stamp();
  put('apps/chatos-console/dist/server.cjs', 'tampered server');
  assert.throws(() => c.verifyBuild(root), /output/i);
  build = stamp();
  put('apps/chatos-console/src/untracked.ts', 'new runtime code');
  stamp();
  assert.throws(() => c.verifyBuild(root), /track/i);
  stage();
  build = stamp();
  assert.equal(c.verifyBuild(root).buildId, build.buildId);
  const db = c.resolveLibraryDb(root);
  assert.equal(db, path.join(root, 'apps/gateway/data/test.sqlite'));
  const startup = c.captureUiRuntime(root, build, path.join(root, 'apps/chatos-console/dist/server.cjs'));
  c.assertRuntime(startup, startup);
  put('apps/chatos-console/dist/server.cjs', 'server version three');
  const next = stamp();
  const expected = c.captureUiRuntime(root, next, path.join(root, 'apps/chatos-console/dist/server.cjs'));
  assert.throws(() => c.assertRuntime(startup, expected), /identity|stale/i);
  assert.equal(startup.buildId, build.buildId, 'startup identity must not follow a replaced manifest');
  put('apps/gateway/config/settings.json', JSON.stringify({ libraryDb: 'data/other.sqlite', frontendUrl: c.UI_URL }));
  assert.throws(() => c.assertRuntime(expected, c.captureUiRuntime(root, next, path.join(root, 'apps/chatos-console/dist/server.cjs'))), /identity|stale/i);

  let payload = {};
  const server = http.createServer((_req, res) => {
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify(payload));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const url = `http://127.0.0.1:${server.address().port}`;
  await assert.rejects(c.inspectService(url, expected), /occupied|identity/i);
  payload = startup;
  await assert.rejects(c.inspectService(url, expected), /occupied|stale|identity/i);
  payload = expected;
  assert.equal(await c.inspectService(url, expected), 'reuse');
  await new Promise(resolve => server.close(resolve));
  assert.equal(await c.inspectService(url, expected), 'absent');
  const sockets = new Set();
  const silent = net.createServer(socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => silent.listen(0, '127.0.0.1', resolve));
  t.after(() => { for (const socket of sockets) socket.destroy(); silent.close(); });
  await assert.rejects(c.inspectService(`http://127.0.0.1:${silent.address().port}`, expected), /occupied/i);
});

test('formal gate validates an existing library before any service spawn', async t => {
  const c = await import(modulePath.href);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-library-gate-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const put = (name, value) => {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), value);
  };
  for (const name of c.SOURCE_INPUTS) put(name.endsWith('/') ? `${name}fixture.ts` : name, 'fixture');
  for (const name of ['entry-contract.mjs', 'verify-entry.mjs', 'start_chatos.mjs']) {
    // Exercise the real gate/launcher with an unreachable test port, never production ports.
    put(`scripts/${name}`, fs.readFileSync(new URL(`../scripts/${name}`, import.meta.url), 'utf8')
      .replaceAll('http://127.0.0.1:3108', 'http://127.0.0.1:0')
      .replaceAll('http://127.0.0.1:8912', 'http://127.0.0.1:0'));
  }
  put('apps/gateway/src/fixture.py', '# synthetic gateway source');
  put('apps/gateway/.venv/Scripts/python.exe', 'not an executable; no service can run in this fixture');
  put('apps/chatos-console/dist/index.html', '<div id="root"></div>');
  put('apps/chatos-console/dist/server.cjs', 'throw Error("fixture must never be spawned")');
  put('apps/chatos-console/dist/build.json', JSON.stringify({
    ...c.sourceIdentity(root), outputHash: c.outputIdentity(path.join(root, 'apps/chatos-console/dist')),
  }));
  execFileSync('git', ['init', '-q', root]);
  execFileSync('git', ['-C', root, 'add', '.']);
  const configure = libraryDb => put('apps/gateway/config/settings.json', JSON.stringify({ libraryDb, frontendUrl: 'http://127.0.0.1:0' }));
  const run = (script, args = []) => spawnSync(process.execPath, [path.join(root, 'scripts', script), ...args], {
    cwd: root, encoding: 'utf8', windowsHide: true, timeout: 10000,
  });
  const invalid = (name, libraryDb) => t.test(name, () => {
    configure(libraryDb);
    for (const args of [[], ['--preflight'], ['--live']]) {
      const result = run('verify-entry.mjs', args);
      assert.equal(result.status, 1, `invalid database must fail before port checks: ${result.stdout} ${result.stderr}`);
      assert.match(result.stderr, /Invalid libraryDb/);
    }
    const launch = run('start_chatos.mjs');
    assert.equal(launch.status, 1);
    assert.match(launch.stderr, /Invalid libraryDb/);
    assert.ok(!fs.existsSync(path.join(root, 'var/logs')), 'launcher must fail before creating logs/spawning either service');
  });
  const missing = path.join(root, 'missing-parent/library.sqlite');
  await invalid('missing DB is rejected without creating its file or parent', missing);
  assert.ok(!fs.existsSync(path.dirname(missing)));
  const incomplete = path.join(root, 'incomplete.sqlite');
  const incompleteDb = new DatabaseSync(incomplete);
  incompleteDb.exec('CREATE TABLE conversations (id TEXT)');
  incompleteDb.close();
  const before = fs.readFileSync(incomplete);
  await invalid('incomplete library schema is rejected without modifying it', incomplete);
  assert.deepEqual(fs.readFileSync(incomplete), before);
  put('not-sqlite.txt', 'not a SQLite database');
  await invalid('non-SQLite file is rejected', path.join(root, 'not-sqlite.txt'));
  await invalid('directory is rejected', root);
  await t.test('existing valid library passes and remains unchanged', () => {
    const file = path.join(root, 'valid.sqlite');
    const db = new DatabaseSync(file);
    db.exec(`
      CREATE TABLE app_meta (key TEXT, value TEXT);
      CREATE TABLE conversations (id TEXT, title TEXT, kind TEXT, message_count INTEGER,
        last_message_at INTEGER, is_favorite INTEGER, is_indexing_enabled INTEGER,
        domain TEXT, project_id TEXT, project_role TEXT);
      CREATE TABLE messages (id TEXT, conversation_id TEXT, sender_id TEXT, sender_name TEXT,
        sent_at INTEGER, type TEXT, content TEXT);
      CREATE TABLE message_parts (id TEXT);
      CREATE TABLE assets (id TEXT);
      CREATE TABLE tasks (task_id TEXT, conversation_id TEXT, content TEXT, sender_name TEXT,
        sent_at INTEGER, due_date TEXT, priority TEXT, status TEXT, updated_at INTEGER);
    `);
    db.close();
    configure(file);
    const bytes = fs.readFileSync(file);
    const result = run('verify-entry.mjs');
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /PASS source\/build/);
    assert.deepEqual(fs.readFileSync(file), bytes);
    assert.ok(!fs.existsSync(`${file}-journal`));
  });
  for (const name of ['apps/gateway/src/untracked.py', 'apps/gateway/src/migrations/999_fixture.sql']) {
    await t.test(`gateway source must be tracked: ${name}`, () => {
      const buildId = c.sourceIdentity(root).buildId;
      const gatewayHash = c.gatewayIdentity(root).startupCodeIdentity;
      put(name, name.endsWith('.py') ? '# new gateway module' : 'SELECT 1;');
      try {
        for (const args of [[], ['--preflight'], ['--live']]) {
          const result = run('verify-entry.mjs', args);
          assert.equal(result.status, 1, `untracked gateway source must fail: ${result.stdout} ${result.stderr}`);
          assert.match(result.stderr, /Source not Git tracked/);
        }
        const launch = run('start_chatos.mjs');
        assert.equal(launch.status, 1);
        assert.match(launch.stderr, /Source not Git tracked/);
        assert.ok(!fs.existsSync(path.join(root, 'var/logs')), 'untracked source must fail before either service is spawned');
      } finally {
        execFileSync('git', ['-C', root, 'add', '--', name]);
      }
      assert.notEqual(c.gatewayIdentity(root).startupCodeIdentity, gatewayHash);
      assert.equal(c.sourceIdentity(root).buildId, buildId, 'backend-only source changes must not invalidate the UI build');
      const accepted = run('verify-entry.mjs');
      assert.equal(accepted.status, 0, accepted.stderr);
      assert.equal(c.verifyBuild(root).buildId, buildId, 'staged gateway source passes without rebuilding UI');
    });
  }
});
