import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { mapGatewayStatus, isOrganizeTerminalStatus, summarizeOrganizeOutcome } from '../src/types.ts';

const source = ts.createSourceFile('Context.tsx', readFileSync(
  new URL('../src/context/ChatOSContext.tsx', import.meta.url), 'utf8',
), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'runIncrementalOrganize') {
    callback = node.initializer.getText(source);
  }
  ts.forEachChild(node, visit);
}
visit(source);
assert.ok(callback, 'test must execute the real production callback');
const compiled = ts.transpileModule(`return ${callback};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

function harness(statuses, { failStart = false, failPoll = false } = {}) {
  let engine = {}, syncing = false, todos = [], conversations = [], polls = 0;
  const requests = [], toasts = [];
  const env = {
    useCallback: fn => fn, modelDiagnosticStatus: 'normal',
    setIsSyncing: v => { syncing = v; }, setEngineState: f => { engine = f(engine); },
    setTodos: v => { todos = v; }, setConversations: v => { conversations = v; },
    conversationRevisionRef: { current: 'old' }, showToast: v => toasts.push(v),
    mapGatewayStatus, isOrganizeTerminalStatus, summarizeOrganizeOutcome,
    setTimeout: fn => { fn(); return 0; },
    fetch: async url => {
      requests.push(url);
      if (url.endsWith('/organize/incremental')) return Response.json({ id: 'job-test' }, { status: failStart ? 503 : 202 });
      if (url.includes('/jobs/')) return Response.json({
        status: statuses[Math.min(polls++, statuses.length - 1)], result: { newTasks: 4 },
      }, { status: failPoll ? 503 : 200 });
      if (url === '/api/conversations/live') return Response.json({ items: [{ id: 'refreshed' }] });
      if (url === '/api/gateway/tasks?limit=300') return Response.json({ items:
        ['draft', 'in_progress', 'completed', 'ignored'].map((status, i) => ({
          taskId: `t${i}`, status, sentAt: 1, sourceMessageId: i + 100, conversationId: 'c',
        })),
      });
      throw new Error(`Unexpected request ${url}`);
    },
  };
  return {
    run: new Function(...Object.keys(env), compiled)(...Object.values(env)),
    requests, toasts,
    get state() { return { engine, syncing, todos, conversations, polls }; },
  };
}

for (const terminal of ['completed', 'degraded', 'failed', 'cancelled', 'interrupted']) {
  test(`queued/running/retrying continue, ${terminal} stops and refreshes actual rows`, async () => {
    const h = harness(['queued', 'running', 'retrying', terminal, 'running']);
    await h.run();
    assert.equal(h.state.polls, 4);
    assert.equal(h.state.syncing, false);
    assert.equal(h.state.engine.status, ['completed', 'degraded'].includes(terminal) ? 'idle' : 'error_fallback');
    assert.deepEqual(h.state.conversations, [{ id: 'refreshed' }]);
    assert.deepEqual(h.state.todos.map(t => t.status), ['draft', 'in_progress', 'completed', 'pending']);
    assert.equal(h.state.todos[3].isIgnored, true);
    assert.equal(h.state.todos[0].sourceMessageId, '100');
    assert.equal(h.requests.filter(url => url.includes('/tasks?')).length, 1);
  });
}

test('exhausted polling reports timeout and releases syncing, never idle success', async () => {
  const h = harness(['running']);
  await h.run();
  assert.equal(h.state.polls, 600);
  assert.equal(h.state.engine.status, 'error_fallback');
  assert.equal(h.state.syncing, false);
  assert.match(h.toasts.at(-1), /超时/);
});

for (const fault of ['failStart', 'failPoll']) {
  test(`${fault} exits with honest failure and releases syncing`, async () => {
    const h = harness(['running'], { [fault]: true });
    await h.run();
    assert.equal(h.state.engine.status, 'error_fallback');
    assert.equal(h.state.syncing, false);
    assert.equal(h.state.polls, fault === 'failStart' ? 0 : 1);
    assert.equal(h.state.todos.length, 0);
  });
}

test('npm test includes the actual polling, collaboration and status checks', () => {
  const { scripts } = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  for (const file of ['collaboration.test.mjs', 'organize-polling.test.mjs', 'verify-round3-todos.ts']) {
    assert.ok(scripts.test.includes(file), `${file} must be in the normal test gate`);
  }
});
