import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setImmediate } from 'node:timers/promises';
import { test } from 'node:test';
import ts from 'typescript';

// Execute the production statements, not a copy of the request logic. There
// is no DOM/test renderer installed; only hooks and the network are substituted.
const source = ts.createSourceFile('ChatOSContext.tsx', readFileSync(
  new URL('../src/context/ChatOSContext.tsx', import.meta.url), 'utf8',
), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const provider = source.statements.flatMap(s => ts.isVariableStatement(s) ? [...s.declarationList.declarations] : [])
  .find(d => d.name.getText(source) === 'ChatOSProvider').initializer;
const stateNames = [
  'activeWorkspace', 'conversations', 'conversationDataSource', 'messages',
  'selectedConversation', 'selectedProject', 'selectedTodo', 'selectedKnowledgeItem',
  'highlightedMessageId', 'isInspectorOpen', 'toastMessage',
];
const statements = provider.body.statements.filter(s => {
  if (ts.isVariableStatement(s)) {
    return s.declarationList.declarations.some(d =>
      (ts.isArrayBindingPattern(d.name) && stateNames.includes(d.name.elements[0].getText(source))) ||
      ['showToast', 'jumpToMessage'].includes(d.name.getText(source)) ||
      (ts.isCallExpression(d.initializer) && d.initializer.expression.getText(source) === 'useRef'));
  }
  return ts.isExpressionStatement(s) && ts.isCallExpression(s.expression) &&
    s.expression.expression.getText(source) === 'useEffect' &&
    /messages\?|[Mm]essage\w*Ref/.test(s.getText(source));
});
assert.ok(statements.some(s => s.getText(source).includes('/messages?limit=100')));
const exposed = [...stateNames, ...stateNames.map(n => `set${n[0].toUpperCase()}${n.slice(1)}`), 'jumpToMessage'];
const compiled = ts.transpileModule(`return function MessageHooks() {
  ${statements.map(s => s.getText(source)).join('\n')}
  return { ${exposed.join(', ')} };
}`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

const conversations = ['A', 'B', 'C'].map(id => ({
  id, name: `Conversation ${id}`, isGroup: false, avatar: '', isFollowed: false,
  interactionDays90d: 1, joyBoyParticipationScore: 0, lastActiveTime: '',
  twoLineSummary: '', unreadCount: 0, priorityScore: 0, priorityReason: '',
  categoryTag: 'general', linkedProjectIds: [],
}));
const payload = (conversationId, id, anchored = false) => ({
  source: 'sqlite-readonly', total: 1, anchored,
  ...(anchored ? { anchorMessageId: id } : {}),
  items: [{ id, conversationId, senderId: 'sender', senderName: 'Sender',
    senderAvatar: '', content: id, timestamp: '10:00', hasAttachment: false }],
});

function harness(t, { selectDatabaseConversation = true } = {}) {
  const slots = [], requests = [];
  let cursor = 0, pending = [], dirty = true, mounted = true, value, updatesAfterUnmount = 0;
  const changed = (a, b) => !a || a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) {
        slots[index] = { value: typeof initial === 'function' ? initial() : initial,
          set(next) {
            if (!mounted) updatesAfterUnmount++;
            const s = slots[index];
            const updated = typeof next === 'function' ? next(s.value) : next;
            if (!Object.is(updated, s.value)) { s.value = updated; dirty = true; }
          } };
      }
      return [slots[index].value, slots[index].set];
    },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useCallback(fn, deps) {
      const index = cursor++;
      if (changed(slots[index]?.deps, deps)) slots[index] = { value: fn, deps };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (changed(slots[index]?.deps, deps)) pending.push({ index, effect, deps });
    },
  };
  const fetch = (url, options = {}) => new Promise((resolve, reject) => {
    requests.push({ url, signal: options.signal, reject,
      reply(data, status = 200) { resolve(Response.json(data, { status })); },
      headers() {
        let body;
        resolve(new Response(new ReadableStream({ start(controller) { body = controller; } })));
        return data => { body.enqueue(new TextEncoder().encode(JSON.stringify(data))); body.close(); };
      },
    });
    // Deliberately permit late responses even after abort: guards must protect
    // state when headers/body parsing have already completed or are in flight.
  });
  const renderHooks = new Function(...Object.keys(hooks), 'fetch', 'setTimeout',
    'INITIAL_CONVERSATIONS', 'INITIAL_MESSAGES', compiled)(
    ...Object.values(hooks), fetch, () => 0, conversations, {},
  );
  const render = () => {
    for (let i = 0; dirty && mounted; i++) {
      assert.ok(i < 20, 'hook updates must settle');
      dirty = false; cursor = 0; pending = [];
      value = renderHooks();
      // React cleans up changed effects before running their new setups.
      for (const { index } of pending) slots[index]?.cleanup?.();
      for (const { index, effect, deps } of pending) slots[index] = { deps, cleanup: effect() };
    }
  };
  const unmount = () => {
    if (!mounted) return;
    mounted = false;
    for (const slot of slots) slot?.cleanup?.();
  };
  t.after(unmount);
  render();
  if (selectDatabaseConversation) value.setSelectedConversation(conversations[0]);
  value.setConversationDataSource('database');
  render();
  return {
    get value() { return value; }, requests, unmount,
    get updatesAfterUnmount() { return updatesAfterUnmount; },
    act(fn) { const result = fn(value); render(); return result; },
    async flush() { await setImmediate(); render(); await setImmediate(); render(); },
    select(id) { this.act(v => v.setSelectedConversation(conversations.find(c => c.id === id) ?? null)); },
    jump(id, messageId) { return this.act(v => v.jumpToMessage(id, messageId)); },
  };
}

test('database loading waits for a real conversation selection before requesting messages', t => {
  const h = harness(t, { selectDatabaseConversation: false });
  assert.equal(h.value.selectedConversation, null);
  assert.equal(h.requests.length, 0);
  h.select('A');
  assert.match(h.requests.at(-1).url, /\/A\/messages\?limit=100$/);
});

test('ordinary selection loads the selected conversation and cancels its predecessor', async t => {
  const h = harness(t);
  h.select('B');
  assert.match(h.requests.at(-1).url, /\/B\/messages\?limit=100$/);
  h.requests.at(-1).reply(payload('B', 'recent-B'));
  h.requests[0].reply(payload('A', 'stale-A'));
  await h.flush();
  assert.equal(h.value.messages.B[0].id, 'recent-B');
  assert.equal(h.value.messages.A, undefined);
  assert.equal(h.requests[0].signal.aborted, true);
});

test('same-conversation jump never suppresses the next ordinary selection', async t => {
  const h = harness(t);
  const jump = h.jump('A', '101');
  h.requests.at(-1).reply(payload('A', '101', true));
  await jump; await h.flush();
  h.select('B');
  assert.match(h.requests.at(-1).url, /\/B\/messages\?limit=100$/);
  h.requests.at(-1).reply(payload('B', 'recent-B'));
  await h.flush();
  assert.equal(h.value.messages.B[0].id, 'recent-B');
  assert.equal(h.value.highlightedMessageId, null);
});

test('a stale ordinary body cannot overwrite a newer anchor window', async t => {
  const h = harness(t);
  const finishBody = h.requests[0].headers();
  await h.flush();
  const jump = h.jump('A', '101');
  h.requests.at(-1).reply(payload('A', '101', true));
  await jump; await h.flush();
  finishBody(payload('A', 'recent-A'));
  await h.flush();
  assert.equal(h.value.messages.A[0].id, '101');
  assert.equal(h.value.highlightedMessageId, '101');
  assert.equal(h.requests[0].signal.aborted, true);
});

test('closing the inspector still allows an ordinary window to finish loading', async t => {
  const h = harness(t);
  h.act(v => v.setIsInspectorOpen(true));
  h.act(v => v.setIsInspectorOpen(false));
  h.requests[0].reply(payload('A', 'recent-A'));
  await h.flush();
  h.act(v => v.setIsInspectorOpen(true));
  assert.equal(h.value.messages.A?.[0].id, 'recent-A');
});

test('a stale anchor body cannot overwrite a later anchor after headers arrive', async t => {
  const h = harness(t);
  const oldJump = h.jump('A', '101');
  const finishBody = h.requests.at(-1).headers();
  await h.flush();
  const newJump = h.jump('A', '102');
  h.requests.at(-1).reply(payload('A', '102', true));
  await newJump; await h.flush();
  const toast = h.value.toastMessage;
  finishBody(payload('A', '101', true));
  await oldJump; await h.flush();
  assert.equal(h.value.messages.A[0].id, '102');
  assert.equal(h.value.highlightedMessageId, '102');
  assert.equal(h.value.toastMessage, toast);
});

test('a cross-conversation jump survives the selection effect without a recent-window fetch', async t => {
  const h = harness(t);
  const jump = h.jump('B', '201');
  assert.equal(h.requests.length, 2);
  h.requests[1].reply(payload('B', '201', true));
  await jump; await h.flush();
  assert.equal(h.value.selectedConversation.id, 'B');
  assert.equal(h.value.messages.B[0].id, '201');
  assert.equal(h.value.highlightedMessageId, '201');
  assert.equal(h.value.isInspectorOpen, true);
});

for (const outcome of ['success', '404', '503', 'empty', 'rejection']) {
  test(`a stale anchor ${outcome} cannot change the newest window, highlight or toast`, async t => {
    const h = harness(t);
    const oldJump = h.jump('A', '101');
    const oldRequest = h.requests.at(-1);
    const newJump = h.jump('A', '102');
    h.requests.at(-1).reply(payload('A', '102', true));
    await newJump; await h.flush();
    const toast = h.value.toastMessage;
    if (outcome === 'rejection') oldRequest.reject(new Error('network failed'));
    else oldRequest.reply(outcome === 'empty' ? { ...payload('A', '101', true), total: 0, items: [] }
      : outcome === 'success' ? payload('A', '101', true)
      : { error: 'message_not_found', message: 'not found' }, Number(outcome) || 200);
    await h.flush();
    assert.equal(h.requests.length, 3, 'stale failure must not launch a fallback');
    await oldJump;
    assert.equal(h.value.messages.A[0].id, '102');
    assert.equal(h.value.highlightedMessageId, '102');
    assert.equal(h.value.toastMessage, toast);
    assert.equal(oldRequest.signal?.aborted, true);
  });
}

for (const destination of ['B', null, 'workspace', 'close']) {
  test(`leaving an anchor via ${destination} prevents late highlight or inspector reopening`, async t => {
    const h = harness(t);
    if (destination === 'close') h.act(v => v.setIsInspectorOpen(true));
    const jump = h.jump('A', '101');
    const request = h.requests.at(-1);
    if (destination === 'workspace') h.act(v => v.setActiveWorkspace('projects'));
    else if (destination === 'close') h.act(v => v.setIsInspectorOpen(false));
    else h.select(destination);
    const messages = h.value.messages;
    request.reply(payload('A', '101', true));
    await jump; await h.flush();
    assert.equal(h.value.messages, messages);
    assert.equal(h.value.highlightedMessageId, null);
    assert.equal(h.value.isInspectorOpen, false);
    assert.equal(h.value.toastMessage, null);
    assert.equal(request.signal?.aborted, true);
  });
}

test('current 404 is explicit, clears highlight and does not claim success or open inspector', async t => {
  const h = harness(t);
  h.act(v => v.setHighlightedMessageId('previous'));
  const jump = h.jump('A', '404');
  h.requests.at(-1).reply({ error: 'message_not_found', message: 'not found' }, 404);
  await jump; await h.flush();
  assert.match(h.value.toastMessage, /未找到指定来源消息/);
  assert.equal(h.value.highlightedMessageId, null);
  assert.equal(h.value.isInspectorOpen, false);
  assert.deepEqual(h.value.messages, {});
  assert.equal(h.requests.length, 2);
});

test('current anchor failure falls back, but its late fallback cannot overwrite a later jump', async t => {
  const h = harness(t);
  const failedJump = h.jump('A', '101');
  h.requests.at(-1).reply({ error: 'unavailable', message: 'unavailable' }, 503);
  await h.flush();
  assert.match(h.value.toastMessage, /定位原始消息失败/);
  const fallback = h.requests.at(-1);
  assert.match(fallback.url, /\/A\/messages\?limit=100$/);
  const finishBody = fallback.headers();
  await h.flush();
  const jump = h.jump('A', '102');
  h.requests.at(-1).reply(payload('A', '102', true));
  await jump; await h.flush();
  finishBody(payload('A', 'recent-A'));
  await failedJump; await h.flush();
  assert.equal(h.value.messages.A[0].id, '102');
  assert.equal(h.value.highlightedMessageId, '102');
  assert.equal(fallback.signal?.aborted, true);
});

test('unmount cancels a same-conversation anchor and ignores its eventual completion', async t => {
  const h = harness(t);
  const jump = h.jump('A', '101');
  const request = h.requests.at(-1);
  const messages = h.value.messages;
  h.unmount();
  request.reply(payload('A', '101', true));
  await jump;
  assert.equal(request.signal?.aborted, true);
  assert.equal(h.value.messages, messages);
  assert.equal(h.updatesAfterUnmount, 0);
});
