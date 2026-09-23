import assert from 'node:assert/strict';
import { once } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { WebSocket } from 'ws';

import { AgentRuntimeServer } from '../dist/server.js';
import { SessionManager } from '../dist/SessionManager.js';
import { ToolRegistry } from '../dist/ToolRegistry.js';
import { LocalModelClient } from '../dist/LocalModelClient.js';
import { loadConfig } from '../dist/config.js';

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return `http://127.0.0.1:${address.port}`;
}

async function close(server) {
  server.close();
  server.closeAllConnections?.();
  await once(server, 'close');
}

function waitForMessage(ws, predicate, timeoutMs = 2_000) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      ws.off('message', onMessage);
      reject(new Error('timed out waiting for WebSocket message'));
    }, timeoutMs);
    const onMessage = (data) => {
      const message = JSON.parse(data.toString());
      if (!predicate(message)) return;
      clearTimeout(timeout);
      ws.off('message', onMessage);
      resolve(message);
    };
    ws.on('message', onMessage);
  });
}

async function connectWebSocket(url, origin = 'http://127.0.0.1:8912') {
  const ws = new WebSocket(url, { headers: { Origin: origin } });
  await once(ws, 'open');
  return ws;
}

test('SessionManager returns the latest messages in chronological public shape', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-session-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('test');
    sessions.addMessage(session.id, 'user', 'one');
    sessions.addMessage(session.id, 'assistant', 'two');
    sessions.addMessage(session.id, 'user', 'three');

    const messages = sessions.getMessages(session.id, 2);

    assert.deepEqual(messages.map(({ sessionId, role, content }) => ({ sessionId, role, content })), [
      { sessionId: session.id, role: 'assistant', content: 'two' },
      { sessionId: session.id, role: 'user', content: 'three' },
    ]);
    assert.equal('session_id' in messages[0], false);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('loadConfig rejects non-loopback model endpoints and invalid numeric limits', () => {
  assert.throws(
    () => loadConfig({ lmStudioUrl: 'https://example.com/v1' }),
    /lmStudioUrl must use local http/i,
  );
  assert.throws(
    () => loadConfig({ embeddingUrl: 'http://192.168.1.20:9123/v1' }),
    /embeddingUrl must use local http/i,
  );
  assert.throws(() => loadConfig({ wsPort: 70_000 }), /wsPort/);
  assert.throws(() => loadConfig({ maxToolIterations: 0 }), /maxToolIterations/);
});

test('local model requests never follow redirects', async () => {
  let leakedRequests = 0;
  const sinkServer = http.createServer((req, res) => {
    leakedRequests += 1;
    if (req.url?.endsWith('/models')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ data: [{ id: 'muse-glimmer-30b' }] }));
    } else if (req.url?.endsWith('/embeddings')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ data: [{ embedding: [1] }] }));
    } else {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.end('data: {"choices":[{"delta":{"content":"leaked"}}]}\n\ndata: [DONE]\n\n');
    }
  });
  const sinkBaseUrl = await listen(sinkServer);
  const redirectServer = http.createServer((req, res) => {
    res.writeHead(307, { Location: `${sinkBaseUrl}${req.url}` });
    res.end();
  });
  const redirectBaseUrl = await listen(redirectServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-no-redirect-'));
  const config = loadConfig({
    lmStudioUrl: `${redirectBaseUrl}/v1`,
    embeddingUrl: `${redirectBaseUrl}/v1`,
    workspaceDir: tempDir,
  });
  const registry = new ToolRegistry(config);
  const client = new LocalModelClient({
    baseUrl: config.lmStudioUrl,
    model: config.model,
  }, registry);

  try {
    const health = await client.healthCheck();
    const turn = await client.runTurn([{ role: 'user', content: 'private chat' }])
      .then(() => 'resolved', () => 'rejected');
    const embedding = await registry.execute('generate_embedding', { text: 'private embedding' });

    assert.equal(health.ok, false);
    assert.equal(turn, 'rejected');
    assert.match(embedding, /^Error:/);
    assert.equal(leakedRequests, 0);
  } finally {
    await close(redirectServer);
    await close(sinkServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('SessionManager returns only OpenAI chat fields and restores tool metadata', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-chat-history-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('tools');
    const toolCalls = [{
      id: 'call_1',
      type: 'function',
      function: { name: 'read_file', arguments: '{"path":"a.txt"}' },
    }];
    sessions.addMessage(session.id, 'user', 'read it');
    sessions.addMessage(session.id, 'assistant', '', JSON.stringify({ tool_calls: toolCalls }));
    sessions.addMessage(session.id, 'tool', 'ok', JSON.stringify({
      tool_call_id: 'call_1',
      name: 'read_file',
    }));

    assert.equal(typeof sessions.getChatMessages, 'function');
    assert.deepEqual(sessions.getChatMessages(session.id), [
      { role: 'user', content: 'read it' },
      { role: 'assistant', content: '', tool_calls: toolCalls },
      { role: 'tool', content: 'ok', tool_call_id: 'call_1', name: 'read_file' },
    ]);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('SessionManager trims oldest chat messages to a character budget', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-chat-budget-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('budget');
    sessions.addMessage(session.id, 'user', '1111111111');
    sessions.addMessage(session.id, 'assistant', '2222222222');
    sessions.addMessage(session.id, 'user', '3333333333');

    assert.deepEqual(sessions.getChatMessages(session.id, 100, 15), [
      { role: 'user', content: '3333333333' },
    ]);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('SessionManager persists protocol tool messages for replay', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-replay-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('replay');
    const assistant = {
      role: 'assistant',
      content: '',
      tool_calls: [{
        id: 'call_1',
        type: 'function',
        function: { name: 'list_files', arguments: '{}' },
      }],
    };
    const tool = {
      role: 'tool',
      content: 'a.txt',
      tool_call_id: 'call_1',
      name: 'list_files',
    };

    assert.equal(typeof sessions.addChatMessage, 'function');
    sessions.addChatMessage(session.id, assistant);
    sessions.addChatMessage(session.id, tool);
    assert.deepEqual(sessions.getChatMessages(session.id), [assistant, tool]);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('SessionManager excludes incomplete tool-call groups from model replay', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-incomplete-tool-history-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('interrupted tools');
    const toolCalls = [
      { id: 'call_1', type: 'function', function: { name: 'first', arguments: '{}' } },
      { id: 'call_2', type: 'function', function: { name: 'second', arguments: '{}' } },
    ];
    sessions.addMessage(session.id, 'user', 'run both');
    sessions.addChatMessage(session.id, { role: 'assistant', content: '', tool_calls: toolCalls });
    sessions.addChatMessage(session.id, {
      role: 'tool', content: 'first done', tool_call_id: 'call_1', name: 'first',
    });

    assert.equal(sessions.getMessages(session.id).length, 3);
    assert.deepEqual(sessions.getChatMessages(session.id), [
      { role: 'user', content: 'run both' },
    ]);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('SessionManager rolls back a message when its session counter update fails', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-message-atomic-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('atomic');
    sessions.db.exec(`
      CREATE TRIGGER fail_session_update
      BEFORE UPDATE ON sessions
      BEGIN
        SELECT RAISE(ABORT, 'forced update failure');
      END;
    `);
    assert.throws(() => sessions.addMessage(session.id, 'user', 'must rollback'), /forced update failure/);
    assert.deepEqual(sessions.getMessages(session.id), []);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('SessionManager rolls back message deletion when session deletion fails', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-delete-atomic-'));
  const sessions = new SessionManager(path.join(tempDir, 'runtime.sqlite'));

  try {
    const session = sessions.createSession('atomic delete');
    sessions.addMessage(session.id, 'user', 'keep me');
    sessions.db.exec(`
      CREATE TRIGGER fail_session_delete
      BEFORE DELETE ON sessions
      BEGIN
        SELECT RAISE(ABORT, 'forced delete failure');
      END;
    `);
    assert.throws(() => sessions.deleteSession(session.id), /forced delete failure/);
    assert.equal(sessions.getSession(session.id)?.messageCount, 1);
    assert.deepEqual(sessions.getMessages(session.id).map((message) => message.content), ['keep me']);
  } finally {
    sessions.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('file tools reject a sibling path that shares the workspace prefix', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tools-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  const siblingDir = path.join(tempDir, 'workspace-escape');
  fs.mkdirSync(workspaceDir);
  fs.mkdirSync(siblingDir);
  fs.writeFileSync(path.join(siblingDir, 'secret.txt'), 'do-not-read');
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));

  try {
    const result = await registry.execute('read_file', { path: '../workspace-escape/secret.txt' });

    assert.match(result, /^Error: path outside workspace$/);
    assert.doesNotMatch(result, /do-not-read|workspace-escape/i);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('file tools reject a junction that escapes the workspace', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-link-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  const outsideDir = path.join(tempDir, 'outside');
  fs.mkdirSync(workspaceDir);
  fs.mkdirSync(outsideDir);
  fs.writeFileSync(path.join(outsideDir, 'secret.txt'), 'outside');
  fs.symlinkSync(outsideDir, path.join(workspaceDir, 'link'), 'junction');
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));

  try {
    const readResult = await registry.execute('read_file', { path: 'link/secret.txt' });
    const writeResult = await registry.execute('write_file', { path: 'link/new.txt', content: 'bad' });

    assert.match(readResult, /^Error: path outside workspace$/);
    assert.match(writeResult, /^Error: path outside workspace$/);
    assert.equal(fs.existsSync(path.join(outsideDir, 'new.txt')), false);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('write_file rejects a dangling final symlink that targets outside the workspace', async (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-dangling-link-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  const outsideFile = path.join(tempDir, 'outside.txt');
  fs.mkdirSync(workspaceDir);
  try {
    fs.symlinkSync(outsideFile, path.join(workspaceDir, 'link.txt'), 'file');
  } catch (error) {
    fs.rmSync(tempDir, { recursive: true, force: true });
    if (error?.code === 'EPERM') return t.skip('file symlink creation is not permitted');
    throw error;
  }
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));

  try {
    assert.equal(
      await registry.execute('write_file', { path: 'link.txt', content: 'escaped' }),
      'Error: path outside workspace',
    );
    assert.equal(fs.existsSync(outsideFile), false);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('read_file rejects files larger than its model-context budget', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-large-file-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  fs.mkdirSync(workspaceDir);
  fs.writeFileSync(path.join(workspaceDir, 'large.txt'), 'x'.repeat(65_537));
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));

  try {
    const result = await registry.execute('read_file', { path: 'large.txt' });
    assert.equal(result, 'Error: file exceeds 65536 byte limit');
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('write_file and quality_check reject files larger than their memory budget', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-limits-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  fs.mkdirSync(workspaceDir);
  fs.writeFileSync(path.join(workspaceDir, 'large.csv'), 'x'.repeat(1_048_577));
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));

  try {
    assert.equal(
      await registry.execute('write_file', { path: 'new.txt', content: 'x'.repeat(1_048_577) }),
      'Error: content exceeds 1048576 byte limit',
    );
    assert.equal(
      await registry.execute('quality_check', { path: 'large.csv' }),
      'Error: file exceeds 1048576 byte limit',
    );
    assert.equal(fs.existsSync(path.join(workspaceDir, 'new.txt')), false);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('file tool errors do not expose the absolute workspace path', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-error-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  fs.mkdirSync(workspaceDir);
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));

  try {
    const result = await registry.execute('read_file', { path: 'missing.txt' });
    assert.equal(result, 'Error: file not found');
    assert.doesNotMatch(result, new RegExp(tempDir.replaceAll('\\', '\\\\'), 'i'));
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('list_files caps directory output before it reaches the model context', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-list-limit-'));
  for (let index = 0; index <= 1_000; index++) {
    fs.writeFileSync(path.join(tempDir, `${String(index).padStart(4, '0')}.txt`), '');
  }
  const registry = new ToolRegistry(loadConfig({ workspaceDir: tempDir }));

  try {
    const result = await registry.execute('list_files', {});
    assert.match(result, /\[truncated: more entries\]$/);
    assert.equal(result.split('\n').length, 1_001);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('tool handlers reject missing required string arguments', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-args-'));
  const registry = new ToolRegistry(loadConfig({ workspaceDir: tempDir }));

  try {
    assert.equal(await registry.execute('read_file', {}), 'Error: path must be a string');
    assert.equal(
      await registry.execute('write_file', { path: 'file.txt' }),
      'Error: content must be a string',
    );
    assert.equal(
      await registry.execute('generate_embedding', {}),
      'Error: text must be a string',
    );
    assert.equal(fs.existsSync(path.join(tempDir, 'file.txt')), false);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('generate_embedding sends the configured embedding model', async () => {
  let requestBody;
  const embeddingServer = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      requestBody = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ data: [{ embedding: Array(1024).fill(0.25) }] }));
    });
  });
  const baseUrl = await listen(embeddingServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-embedding-'));
  const registry = new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
    embeddingUrl: `${baseUrl}/v1`,
    embeddingModel: 'text-embedding-qwen3-embedding-0.6b',
  }));

  try {
    const result = await registry.execute('generate_embedding', { text: 'hello' });
    assert.equal(requestBody.model, 'text-embedding-qwen3-embedding-0.6b');
    assert.match(result, /dim=1024/);
    assert.throws(() => loadConfig({ embeddingModel: 'old-model' }), /1024/);
  } finally {
    await close(embeddingServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('generate_embedding times out a stalled local endpoint', async () => {
  const sockets = new Set();
  const embeddingServer = http.createServer(() => {});
  embeddingServer.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  const baseUrl = await listen(embeddingServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-embedding-timeout-'));
  const registry = new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
    embeddingUrl: `${baseUrl}/v1`,
    modelTimeoutMs: 30,
  }));

  try {
    const result = await Promise.race([
      registry.execute('generate_embedding', { text: 'hello' }),
      new Promise((resolve) => setTimeout(() => resolve('test timed out'), 300)),
    ]);
    assert.equal(result, 'Error: embedding request timed out');
  } finally {
    for (const socket of sockets) socket.destroy();
    await close(embeddingServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('generate_embedding rejects a malformed vector response', async () => {
  let responseVector = [1, 'bad'];
  const embeddingServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ data: [{ embedding: responseVector }] }));
  });
  const baseUrl = await listen(embeddingServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-embedding-shape-'));
  const registry = new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
    embeddingUrl: `${baseUrl}/v1`,
  }));

  try {
    assert.equal(
      await registry.execute('generate_embedding', { text: 'hello' }),
      'Error: embedding API returned an invalid vector',
    );
    responseVector = Array(1024).fill(0);
    assert.equal(await registry.execute('generate_embedding', { text: 'hello' }),
      'Error: embedding API returned an invalid vector');
  } finally {
    await close(embeddingServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('generate_embedding schema advertises only implemented behavior', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-schema-'));
  const registry = new ToolRegistry(loadConfig({ workspaceDir: tempDir }));

  try {
    const embedding = registry.getToolDefinitions().find((tool) => tool.function.name === 'generate_embedding');
    assert.ok(embedding);
    assert.deepEqual(Object.keys(embedding.function.parameters.properties), ['text']);
    assert.doesNotMatch(embedding.function.description, /storage path/i);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient rejects a concurrent turn until the active turn settles', async () => {
  let firstRequestStarted;
  const firstRequest = new Promise((resolve) => { firstRequestStarted = resolve; });
  let requestCount = 0;
  const modelServer = http.createServer((_req, res) => {
    requestCount += 1;
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (requestCount === 1) {
      res.flushHeaders();
      firstRequestStarted();
      return;
    }
    res.end('data: {"choices":[{"delta":{"content":"second"}}]}\n\ndata: [DONE]\n\n');
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-concurrency-'));
  const config = loadConfig({ workspaceDir: tempDir });
  const client = new LocalModelClient({
    baseUrl: `${baseUrl}/v1`,
    requestTimeoutMs: 5_000,
  }, new ToolRegistry(config));
  const activeTurn = client.runTurn([{ role: 'user', content: 'first' }]);

  try {
    await firstRequest;
    await assert.rejects(
      client.runTurn([{ role: 'user', content: 'second' }]),
      /turn already in progress/i,
    );
  } finally {
    client.interrupt();
    modelServer.closeAllConnections();
    await activeTurn.catch(() => undefined);
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient parses one SSE event split across data lines', async () => {
  const modelServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end([
      'data: {"choices":[',
      'data: {"delta":{"content":"multi"}}]}',
      '',
      'data: [DONE]',
      '',
    ].join('\n'));
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-sse-multiline-'));
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
  })));

  try {
    const result = await client.runTurn([{ role: 'user', content: 'hello' }]);
    assert.equal(result.content, 'multi');
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient cancels the response reader after malformed SSE', async () => {
  let responseClosed;
  const closed = new Promise((resolve) => { responseClosed = resolve; });
  const modelServer = http.createServer((_req, res) => {
    res.on('close', responseClosed);
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: not-json\n\n');
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-malformed-sse-'));
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
  })));

  try {
    await assert.rejects(
      client.runTurn([{ role: 'user', content: 'hello' }]),
      /malformed SSE data/i,
    );
    assert.equal(await Promise.race([
      closed.then(() => 'closed'),
      new Promise((resolve) => setTimeout(() => resolve('timed out'), 500)),
    ]), 'closed');
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient stops at DONE and flushes a final unterminated SSE event', async () => {
  let requestCount = 0;
  const modelServer = http.createServer((_req, res) => {
    requestCount += 1;
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (requestCount === 1) {
      res.end([
        'data: {"choices":[{"delta":{"content":"before"}}]}',
        '',
        'data: [DONE]',
        '',
        'data: {"choices":[{"delta":{"content":"after"}}]}',
        '',
      ].join('\n'));
      return;
    }
    res.end('data: {"choices":[{"delta":{"content":"tail"}}]}');
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-sse-end-'));
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
  })));

  try {
    assert.equal((await client.runTurn([{ role: 'user', content: 'first' }])).content, 'before');
    assert.equal((await client.runTurn([{ role: 'user', content: 'second' }])).content, 'tail');
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient merges fragmented tool-call ids and arguments', async () => {
  const requests = [];
  const modelServer = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      requests.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      if (requests.length === 1) {
        res.end([
          'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_","function":{"name":"read_","arguments":"{\\"path\\":"}}]}}]}',
          '',
          'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"123","function":{"name":"file","arguments":"\\"missing.txt\\"}"}}]}}]}',
          '',
          'data: [DONE]',
          '',
        ].join('\n'));
        return;
      }
      res.end('data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n');
    });
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-stream-'));
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
  })));
  const persisted = [];

  try {
    const result = await client.runTurn(
      [{ role: 'user', content: 'read missing.txt' }],
      (message) => persisted.push(message),
    );
    const replay = requests[1].messages.slice(-2);

    assert.equal(result.content, 'done');
    assert.equal(replay[0].tool_calls[0].id, 'call_123');
    assert.equal(replay[0].tool_calls[0].function.name, 'read_file');
    assert.equal(replay[0].tool_calls[0].function.arguments, '{"path":"missing.txt"}');
    assert.equal(replay[1].tool_call_id, 'call_123');
    assert.deepEqual(persisted.map((message) => message.role), ['assistant', 'tool', 'assistant']);
    assert.equal(persisted[0].tool_calls[0].id, 'call_123');
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient bounds tool results before replaying them to the model', async () => {
  const requests = [];
  const modelServer = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      requests.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      if (requests.length === 1) {
        res.end('data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_big","function":{"name":"big_result","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n');
        return;
      }
      res.end('data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n');
    });
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-result-limit-'));
  const registry = new ToolRegistry(loadConfig({ workspaceDir: tempDir }));
  registry.register({
    name: 'big_result',
    description: 'Return oversized test data.',
    parameters: { type: 'object', properties: {} },
    handler: async () => 'x'.repeat(100_000),
  });
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, registry);

  try {
    await client.runTurn([{ role: 'user', content: 'run it' }]);
    const toolMessage = requests[1].messages.find((message) => message.role === 'tool');
    assert.ok(toolMessage.content.length <= 32_000);
    assert.match(toolMessage.content, /\n\[truncated\]$/);
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient rejects an incomplete streamed tool call', async () => {
  let requestCount = 0;
  const modelServer = http.createServer((_req, res) => {
    requestCount += 1;
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"list_files","arguments":"{}"}}]}}]}',
      '',
      'data: [DONE]',
      '',
    ].join('\n'));
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-incomplete-'));
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
  })));

  try {
    await assert.rejects(
      client.runTurn([{ role: 'user', content: 'list files' }]),
      /incomplete tool call/i,
    );
    assert.equal(requestCount, 1);
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient rejects an empty completion', async () => {
  const modelServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end('data: [DONE]\n\n');
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-empty-completion-'));
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, new ToolRegistry(loadConfig({
    workspaceDir: tempDir,
  })));

  try {
    await assert.rejects(
      client.runTurn([{ role: 'user', content: 'hello' }]),
      /empty completion/i,
    );
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('healthCheck is degraded when the configured model is not loaded', async () => {
  const modelServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ data: [{ id: 'another-model' }] }));
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-health-model-'));
  const client = new LocalModelClient({
    baseUrl: `${baseUrl}/v1`,
    model: 'required-model',
  }, new ToolRegistry(loadConfig({ workspaceDir: tempDir })));

  try {
    assert.deepEqual(await client.healthCheck(), {
      ok: false,
      error: 'configured model not loaded: required-model',
    });
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('LocalModelClient sends exactly one runtime system prompt', async () => {
  let requestBody;
  const modelServer = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      requestBody = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.end('data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n');
    });
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-system-prompt-'));
  const client = new LocalModelClient({
    baseUrl: `${baseUrl}/v1`,
    systemPrompt: 'runtime prompt',
  }, new ToolRegistry(loadConfig({ workspaceDir: tempDir })));

  try {
    await client.runTurn([
      { role: 'system', content: 'stale prompt' },
      { role: 'user', content: 'hello' },
    ]);
    assert.deepEqual(requestBody.messages.filter((message) => message.role === 'system'), [
      { role: 'system', content: 'runtime prompt' },
    ]);
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('interrupt aborts a stalled embedding tool call', async () => {
  let embeddingStarted;
  const embeddingRequest = new Promise((resolve) => { embeddingStarted = resolve; });
  const embeddingSockets = new Set();
  const embeddingServer = http.createServer(() => embeddingStarted());
  embeddingServer.on('connection', (socket) => {
    embeddingSockets.add(socket);
    socket.on('close', () => embeddingSockets.delete(socket));
  });
  const embeddingBaseUrl = await listen(embeddingServer);
  const modelServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"generate_embedding","arguments":"{\\"text\\":\\"hello\\"}"}}]}}]}',
      '',
      'data: [DONE]',
      '',
    ].join('\n'));
  });
  const modelBaseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-tool-abort-'));
  const config = loadConfig({
    workspaceDir: tempDir,
    embeddingUrl: `${embeddingBaseUrl}/v1`,
    modelTimeoutMs: 5_000,
  });
  const client = new LocalModelClient({
    baseUrl: `${modelBaseUrl}/v1`,
    requestTimeoutMs: 5_000,
  }, new ToolRegistry(config));
  const events = [];
  client.on('event', (event) => events.push(event));
  const turn = client.runTurn([{ role: 'user', content: 'embed hello' }]);

  try {
    await embeddingRequest;
    client.interrupt();
    const outcome = await Promise.race([
      turn.then(() => 'resolved', (err) => err?.name === 'AbortError' ? 'aborted' : String(err)),
      new Promise((resolve) => setTimeout(() => resolve('test timed out'), 300)),
    ]);
    assert.equal(outcome, 'aborted');
    assert.equal(events.some((event) => event.type === 'error'), false);
  } finally {
    for (const socket of embeddingSockets) socket.destroy();
    await turn.catch(() => undefined);
    await close(embeddingServer);
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('interrupt prevents later tools in the same batch from starting', async () => {
  const modelServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(`data: ${JSON.stringify({ choices: [{ delta: { tool_calls: [
      { index: 0, id: 'call_first', function: { name: 'first', arguments: '{}' } },
      { index: 1, id: 'call_write', function: { name: 'write_file', arguments: '{"path":"should-not-exist.txt","content":"bad"}' } },
    ] } }] })}\n\ndata: [DONE]\n\n`);
  });
  const baseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-interrupt-tool-batch-'));
  const workspaceDir = path.join(tempDir, 'workspace');
  const registry = new ToolRegistry(loadConfig({ workspaceDir }));
  registry.register({
    name: 'first',
    description: 'Complete the first test step.',
    parameters: { type: 'object', properties: {} },
    handler: async () => 'done',
  });
  const client = new LocalModelClient({ baseUrl: `${baseUrl}/v1` }, registry);

  try {
    await assert.rejects(
      client.runTurn([{ role: 'user', content: 'run both' }], (message) => {
        if (message.role === 'tool' && message.name === 'first') client.interrupt();
      }),
      (error) => error?.name === 'AbortError',
    );
    assert.equal(fs.existsSync(path.join(workspaceDir, 'should-not-exist.txt')), false);
  } finally {
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('AgentRuntimeServer survives an oversized WebSocket frame', async () => {
  const modelServer = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end('{}');
  });
  const modelBaseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-ws-payload-'));
  const runtime = new AgentRuntimeServer(loadConfig({
    lmStudioUrl: `${modelBaseUrl}/v1`,
    workspaceDir: path.join(tempDir, 'workspace'),
    dbPath: path.join(tempDir, 'runtime.sqlite'),
    logDir: path.join(tempDir, 'logs'),
    wsPort: 0,
  }));
  await runtime.start();
  const address = runtime.httpServer.address();
  assert.ok(address && typeof address === 'object');
  const ws = await connectWebSocket(`ws://127.0.0.1:${address.port}`);
  ws.on('error', () => undefined);

  try {
    const closed = new Promise((resolve) => ws.once('close', resolve));
    ws.send('x'.repeat(64 * 1024 + 1), () => undefined);
    await closed;
    const response = await fetch(`http://127.0.0.1:${address.port}/sessions`);
    assert.equal(response.status, 200);
    assert.ok(Array.isArray(await response.json()));
  } finally {
    ws.close();
    await runtime.stop();
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('AgentRuntimeServer routes turn events to their session owner with clean model history', async () => {
  const modelRequests = [];
  const modelServer = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      modelRequests.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.end('data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n');
    });
  });
  const modelBaseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-server-routing-'));
  const runtime = new AgentRuntimeServer(loadConfig({
    lmStudioUrl: `${modelBaseUrl}/v1`,
    workspaceDir: path.join(tempDir, 'workspace'),
    dbPath: path.join(tempDir, 'runtime.sqlite'),
    logDir: path.join(tempDir, 'logs'),
    wsPort: 0,
  }));
  const startPromise = runtime.start();
  if (!runtime.httpServer.listening) await once(runtime.httpServer, 'listening');
  await startPromise;
  const address = runtime.httpServer.address();
  assert.ok(address && typeof address === 'object');
  assert.equal(address.address, '127.0.0.1');
  const wsUrl = `ws://127.0.0.1:${address.port}`;
  const rejectedSocket = new WebSocket(wsUrl, { headers: { Origin: 'https://evil.example' } });
  const rejectedOrigin = await new Promise((resolve) => {
    rejectedSocket.once('unexpected-response', (_request, response) => {
      response.destroy();
      resolve(true);
    });
    rejectedSocket.once('open', () => resolve(false));
    rejectedSocket.once('error', () => undefined);
  });
  assert.equal(rejectedOrigin, true);
  const clientA = await connectWebSocket(wsUrl);
  const clientB = await connectWebSocket(wsUrl);
  const clientBEvents = [];
  clientB.on('message', (data) => clientBEvents.push(JSON.parse(data.toString())));

  try {
    const startedA = waitForMessage(clientA, (message) => message.type === 'session-started');
    clientA.send(JSON.stringify({ type: 'start', title: 'A' }));
    const sessionA = (await startedA).session.id;
    const startedB = waitForMessage(clientB, (message) => message.type === 'session-started');
    clientB.send(JSON.stringify({ type: 'start', title: 'B' }));
    await startedB;
    clientBEvents.length = 0;

    const ownerEvents = [];
    clientA.on('message', (data) => ownerEvents.push(JSON.parse(data.toString())));
    const completed = waitForMessage(clientA, (message) => message.type === 'turn-complete');
    clientA.send(JSON.stringify({ type: 'message', sessionId: sessionA, text: 'hi' }));
    await completed;

    const turnEvents = ownerEvents.filter((event) => [
      'user-message', 'text-delta', 'thinking', 'tool-call', 'tool-result', 'turn-complete',
    ].includes(event.type));
    assert.ok(turnEvents.length >= 3);
    assert.ok(turnEvents.every((event) => event.sessionId === sessionA));
    assert.equal(clientBEvents.some((event) => turnEvents.some((owner) => owner.type === event.type)), false);
    assert.deepEqual(modelRequests[0].messages.map((message) => Object.keys(message).sort()), [
      ['content', 'role'],
      ['content', 'role'],
    ]);
    const historyResponse = waitForMessage(clientA, (message) => message.type === 'history');
    clientA.send(JSON.stringify({ type: 'history', sessionId: sessionA }));
    assert.deepEqual((await historyResponse).messages.map((message) => message.role), ['user', 'assistant']);
  } finally {
    clientA.close();
    clientB.close();
    await Promise.all([once(clientA, 'close'), once(clientB, 'close')]);
    await runtime.stop();
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('only the owning WebSocket can interrupt a turn and the session is reusable afterward', async () => {
  let firstRequestStarted;
  const firstRequest = new Promise((resolve) => { firstRequestStarted = resolve; });
  let requestCount = 0;
  const modelServer = http.createServer((_req, res) => {
    requestCount += 1;
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (requestCount === 1) {
      res.flushHeaders();
      firstRequestStarted();
      return;
    }
    res.end('data: {"choices":[{"delta":{"content":"reused"}}]}\n\ndata: [DONE]\n\n');
  });
  const modelBaseUrl = await listen(modelServer);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-server-interrupt-'));
  const runtime = new AgentRuntimeServer(loadConfig({
    lmStudioUrl: `${modelBaseUrl}/v1`,
    workspaceDir: path.join(tempDir, 'workspace'),
    dbPath: path.join(tempDir, 'runtime.sqlite'),
    logDir: path.join(tempDir, 'logs'),
    wsPort: 0,
  }));
  await runtime.start();
  const address = runtime.httpServer.address();
  assert.ok(address && typeof address === 'object');
  const clientA = await connectWebSocket(`ws://127.0.0.1:${address.port}`);
  const clientB = await connectWebSocket(`ws://127.0.0.1:${address.port}`);

  try {
    const started = waitForMessage(clientA, (message) => message.type === 'session-started');
    clientA.send(JSON.stringify({ type: 'start', title: 'owner' }));
    const sessionId = (await started).session.id;
    clientA.send(JSON.stringify({ type: 'message', sessionId, text: 'first' }));
    await firstRequest;

    const denied = waitForMessage(clientB, (message) => message.type === 'error');
    clientB.send(JSON.stringify({ type: 'interrupt', sessionId }));
    assert.equal((await denied).message, 'cannot interrupt another session');

    const interrupted = waitForMessage(clientA, (message) => message.type === 'interrupted');
    clientA.send(JSON.stringify({ type: 'interrupt', sessionId }));
    assert.equal((await interrupted).sessionId, sessionId);

    const completed = waitForMessage(clientA, (message) => message.type === 'turn-complete');
    clientA.send(JSON.stringify({ type: 'message', sessionId, text: 'second' }));
    const completion = await completed;
    assert.equal(completion.text, 'reused');
    assert.equal(runtime.model.listenerCount('event'), 0);
  } finally {
    modelServer.closeAllConnections();
    clientA.close();
    clientB.close();
    await Promise.all([once(clientA, 'close'), once(clientB, 'close')]);
    await runtime.stop();
    await close(modelServer);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test('AgentRuntimeServer.start rejects when its port is already in use', async () => {
  const occupied = http.createServer();
  const occupiedUrl = new URL(await listen(occupied));
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chatos-server-port-'));
  const runtime = new AgentRuntimeServer(loadConfig({
    workspaceDir: path.join(tempDir, 'workspace'),
    dbPath: path.join(tempDir, 'runtime.sqlite'),
    logDir: path.join(tempDir, 'logs'),
    wsPort: Number(occupiedUrl.port),
  }));

  try {
    await assert.rejects(runtime.start(), (err) => err?.code === 'EADDRINUSE');
  } finally {
    await runtime.stop();
    await close(occupied);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});
