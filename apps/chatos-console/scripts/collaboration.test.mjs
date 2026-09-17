import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

// Execute the real Context callbacks. Only React hooks/network are substituted;
// this is a state regression check, not a browser-rendering claim.
const source = ts.createSourceFile('ChatOSContext.tsx', readFileSync(
  new URL('../src/context/ChatOSContext.tsx', import.meta.url), 'utf8',
), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const provider = source.statements.flatMap(s => ts.isVariableStatement(s) ? [...s.declarationList.declarations] : [])
  .find(d => d.name.getText(source) === 'ChatOSProvider').initializer;
const names = ['toggleCompleteSharedTask', 'toggleCommentReaction', 'addProjectComment', 'deleteProjectComment'];
const statements = provider.body.statements.filter(s => ts.isVariableStatement(s) &&
  s.declarationList.declarations.some(d => names.includes(d.name.getText(source)) ||
    (ts.isCallExpression(d.initializer) && d.initializer.expression.getText(source) === 'useRef')));
const compiled = ts.transpileModule(`${statements.map(s => s.getText(source)).join('\n')}
return { ${names.join(', ')} };`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

function harness({ deferState = false, reactions = [] } = {}) {
  const owner = { id: 'owner', name: 'Original owner' };
  let sharedLists = [{ id: 'l', tasks: [{ id: 't', title: 'Task', isCompleted: true,
    completedAt: '2026-09-17 09:00', completedBy: owner }] }];
  let projectComments = { p: [{ id: 'c', projectId: 'p', content: 'Comment', reactions }] };
  let projects = [{ id: 'p', name: 'Project', commentsCount: 1 }];
  const requests = [], refs = [], queued = [];
  let cursor;
  const update = fn => deferState ? queued.push(fn) : fn();
  return {
    get lists() { return sharedLists; },
    get comments() { return projectComments.p; },
    get projects() { return projects; },
    requests,
    flush() { for (const fn of queued.splice(0)) fn(); },
    actions() {
      cursor = 0;
      const env = {
        useCallback: fn => fn, useRef: initial => refs[cursor++] ??= { current: initial },
        CURRENT_USER: { id: 'u', name: 'Tester' }, teamMembers: [],
        sharedLists, projectComments, projects,
        setSharedLists: fn => update(() => { sharedLists = fn(sharedLists); }),
        setProjectComments: fn => update(() => { projectComments = fn(projectComments); }),
        setProjects: fn => update(() => { projects = fn(projects); }),
        setAuditLogs: () => {}, showToast: () => {},
        fetch: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, reject,
          reply: (data = {}, status = 200) => resolve(Response.json(data, { status })),
        })),
      };
      return new Function(...Object.keys(env), compiled)(...Object.values(env));
    },
  };
}

test('older failed emoji cannot erase a different emoji that succeeded later', async () => {
  const h = harness();
  const first = h.actions().toggleCommentReaction('p', 'c', 'like');
  const second = h.actions().toggleCommentReaction('p', 'c', 'heart');
  h.requests[1].reply(); await second;
  h.requests[0].reply({}, 503); await first;
  assert.deepEqual(h.comments[0].reactions, [{ emoji: 'heart', count: 1, users: ['u'] }]);
});

test('failed first emoji restores an absent reactions field to an empty list', async () => {
  const h = harness({ reactions: null });
  const work = h.actions().toggleCommentReaction('p', 'c', 'like');
  h.requests[0].reply({}, 503); await work;
  assert.deepEqual(h.comments[0].reactions, []);
});

test('same emoji is single-flight and can be retried after a network failure', async () => {
  const h = harness();
  const first = h.actions().toggleCommentReaction('p', 'c', 'like');
  const duplicate = h.actions().toggleCommentReaction('p', 'c', 'like');
  assert.equal(h.requests.length, 1);
  await duplicate;
  h.requests[0].reject(new Error('offline')); await first;
  const retry = h.actions().toggleCommentReaction('p', 'c', 'like');
  assert.equal(h.requests.length, 2);
  h.requests[1].reply(); await retry;
  assert.equal(h.comments[0].reactions[0].count, 1);
});

test('failed reopening restores completion time and owner as well as the flag', async () => {
  const h = harness();
  const before = structuredClone(h.lists[0].tasks[0]);
  const work = h.actions().toggleCompleteSharedTask('l', 't');
  h.requests[0].reply({}, 503); await work;
  assert.deepEqual(h.lists[0].tasks[0], before);
});

test('same shared task is single-flight and may be retried after failure', async () => {
  const h = harness();
  const first = h.actions().toggleCompleteSharedTask('l', 't');
  const duplicate = h.actions().toggleCompleteSharedTask('l', 't');
  assert.equal(h.requests.length, 1);
  await duplicate;
  h.requests[0].reply({}, 503); await first;
  const retry = h.actions().toggleCompleteSharedTask('l', 't');
  assert.equal(h.requests.length, 2);
  h.requests[1].reply({ ...h.lists[0].tasks[0], isCompleted: false }); await retry;
  assert.equal(h.lists[0].tasks[0].isCompleted, false);
});

test('request decision does not depend on when React runs the state updater', async () => {
  const h = harness({ deferState: true });
  // Reopen, then complete; the second request must not default to false while
  // React is batching its functional state updater.
  const reopen = h.actions().toggleCompleteSharedTask('l', 't');
  h.flush(); h.requests[0].reply({ id: 't', isCompleted: false }); await reopen; h.flush();
  const complete = h.actions().toggleCompleteSharedTask('l', 't');
  assert.equal(JSON.parse(h.requests[1].options.body).isCompleted, true);
  h.flush(); h.requests[1].reply({ id: 't', isCompleted: true,
    completedAt: 'server-time', completedBy: { id: 'server-owner' } }); await complete; h.flush();
  assert.equal(h.lists[0].tasks[0].completedAt, 'server-time');
});

test('comment creation stores and returns the complete server entity', async () => {
  const h = harness();
  const work = h.actions().addProjectComment('p', ' new ');
  const saved = { id: 'server-id', projectId: 'p', content: 'new', author: { id: 'u' },
    timestamp: 'server-time', reactions: [], isPinned: true };
  h.requests[0].reply(saved, 201);
  assert.deepEqual(await work, saved);
  assert.deepEqual(h.comments[0], saved);
  const removal = h.actions().deleteProjectComment('p', saved.id);
  assert.match(h.requests[1].url, /comments\/server-id$/);
  h.requests[1].reply({}, 503); await removal;
  assert.deepEqual(h.comments[0], saved);
  assert.equal(h.projects[0].commentsCount, 2);
});
