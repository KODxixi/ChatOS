import assert from 'node:assert/strict';
import { once } from 'node:events';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import { gzipSync } from 'node:zlib';
import { test } from 'node:test';
import express from 'express';
import ts from 'typescript';
import { proxyGateway } from '../src/server/gateway-proxy.ts';

// Execute the actual production registrations, without server.ts's DB/config,
// Vite or listen() side effects. No copied handler, mocked fetch or fake streams.
const source = readFileSync(new URL('../server.ts', import.meta.url), 'utf8');
const start = source.indexOf('const UI_HOST_PORT =');
const end = source.indexOf("app.get('/api/runtime'");
assert.ok(start >= 0 && end > start, 'production security/proxy block must exist');
const mount = new Function(
  'app', 'express', 'UI_URL', 'GATEWAY_URL', 'proxyGateway',
  ts.transpileModule(source.slice(start, end), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText,
);

async function until(predicate, message, ms = 1_000) {
  const deadline = Date.now() + ms;
  while (!predicate() && Date.now() < deadline) await delay(10);
  assert.ok(predicate(), message);
}

async function fixture(t, upstreamHandler, { timeout = 2_000, observe = () => {} } = {}) {
  const upstream = http.createServer(upstreamHandler);
  const app = express();
  app.use((req, res, next) => { observe(req, res); next(); });
  const proxy = http.createServer(app);
  const clients = new Set();
  const timers = new Set();
  t.after(async () => {
    for (const client of clients) client.destroy();
    for (const timer of timers) clearTimeout(timer);
    await Promise.all([proxy, upstream].map(server => new Promise(resolve => {
      server.close(resolve);
      server.closeAllConnections();
    })));
  });
  await Promise.all([upstream, proxy].map(server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve))));
  const gatewayUrl = `http://127.0.0.1:${upstream.address().port}`;
  const uiUrl = `http://127.0.0.1:${proxy.address().port}`;
  // Observe real timers and shorten only the production deadline; I/O, fetch,
  // responses and streams remain native. Test cleanup restores both methods.
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;
  t.mock.method(globalThis, 'setTimeout', (callback, ms, ...args) => {
    if (ms !== 120_000) return realSetTimeout(callback, ms, ...args);
    const timer = realSetTimeout(() => { timers.delete(timer); callback(...args); }, timeout);
    timers.add(timer);
    return timer;
  });
  t.mock.method(globalThis, 'clearTimeout', timer => { timers.delete(timer); realClearTimeout(timer); });
  mount(app, express, uiUrl, gatewayUrl, proxyGateway);

  function request(path = '/binary', options = {}) {
    const { body, ...init } = options;
    const client = http.request(`${uiUrl}/api/gateway${path}`, { agent: false, ...init });
    clients.add(client);
    client.once('close', () => clients.delete(client));
    const response = once(client, 'response').then(([res]) => res);
    client.end(body);
    return { client, response };
  }
  return { request, timers, uiUrl };
}

async function collect(response) {
  const chunks = [];
  for await (const chunk of response) chunks.push(chunk);
  return Buffer.concat(chunks);
}

test('binary bytes, status and download/range headers survive the real proxy', { timeout: 5_000 }, async t => {
  const bytes = Buffer.from(Array.from({ length: 65_536 }, (_, i) => i % 256));
  let received;
  const f = await fixture(t, (req, res) => {
    received = { url: req.url, range: req.headers.range };
    res.writeHead(206, {
      'Content-Type': 'application/octet-stream', 'Content-Length': bytes.length,
      'Content-Disposition': 'attachment; filename="sample.bin"',
      'Content-Range': 'bytes 0-65535/100000', 'Accept-Ranges': 'bytes', ETag: '"asset-v1"',
    });
    res.end(bytes);
  });
  const res = await f.request('/assets/a?download=1', { headers: { Range: 'bytes=0-65535' } }).response;
  assert.deepEqual(await collect(res), bytes);
  assert.equal(res.statusCode, 206);
  assert.deepEqual(received, { url: '/api/assets/a?download=1', range: 'bytes=0-65535' });
  assert.equal(res.headers['content-length'], String(bytes.length));
  assert.equal(res.headers['content-disposition'], 'attachment; filename="sample.bin"');
  assert.equal(res.headers['content-range'], 'bytes 0-65535/100000');
  assert.equal(res.headers['accept-ranges'], 'bytes');
  assert.equal(res.headers.etag, '"asset-v1"');
  await until(() => f.timers.size === 0, 'success must clear the deadline');
});

test('session cookies, JSON writes and upstream error status are preserved', { timeout: 5_000 }, async t => {
  const cookies = ['session=local; HttpOnly; SameSite=Strict; Path=/', 'second=value; Path=/'];
  const f = await fixture(t, async (req, res) => {
    const body = await collect(req);
    res.writeHead(401, { 'Content-Type': 'application/json', 'Set-Cookie': cookies });
    res.end(JSON.stringify({ method: req.method, cookie: req.headers.cookie, body: JSON.parse(body.toString()) }));
  });
  const res = await f.request('/tasks/t1', {
    method: 'PATCH', headers: { Cookie: 'session=local', 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'done' }),
  }).response;
  assert.equal(res.statusCode, 401);
  assert.deepEqual(res.headers['set-cookie'], cookies);
  assert.deepEqual(JSON.parse((await collect(res)).toString()), {
    method: 'PATCH', cookie: 'session=local', body: { status: 'done' },
  });
});

test('upstream headers cannot weaken no-store/security or relay hop-by-hop fields', { timeout: 5_000 }, async t => {
  let hits = 0;
  const f = await fixture(t, (_req, res) => {
    hits++;
    res.writeHead(200, {
      'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'unsafe',
      'X-Frame-Options': 'ALLOWALL', 'Access-Control-Allow-Origin': '*',
      Connection: 'close, Content-Disposition', 'Content-Disposition': 'inline',
    });
    res.end('private');
  });
  const res = await f.request().response;
  await collect(res);
  assert.equal(res.headers['cache-control'], 'no-store');
  assert.equal(res.headers['x-content-type-options'], 'nosniff');
  assert.equal(res.headers['x-frame-options'], 'DENY');
  assert.equal(res.headers['referrer-policy'], 'no-referrer');
  assert.equal(res.headers['cross-origin-opener-policy'], 'same-origin');
  assert.equal(res.headers['access-control-allow-origin'], undefined);
  assert.equal(res.headers['content-disposition'], undefined);
  for (const options of [
    { headers: { Host: 'untrusted.invalid' } },
    { method: 'POST', headers: { Origin: 'https://untrusted.invalid' } },
  ]) {
    const denied = await f.request('/', options).response;
    await collect(denied);
    assert.equal(denied.statusCode, 403);
  }
  assert.equal(hits, 1, 'rejected requests must never reach upstream');
});

test('SSE arrives incrementally before upstream is allowed to send its next event', { timeout: 5_000 }, async t => {
  let upstreamResponse;
  const f = await fixture(t, (_req, res) => {
    upstreamResponse = res;
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: first\n\n');
  });
  const res = await f.request('/ask/stream').response;
  const chunks = [];
  res.on('data', chunk => chunks.push(chunk));
  await until(() => Buffer.concat(chunks).toString().includes('data: first\n\n'), 'first SSE event was buffered');
  assert.equal(upstreamResponse.writableEnded, false);
  const ended = once(res, 'end');
  upstreamResponse.end('data: second\n\n');
  await ended;
  assert.equal(Buffer.concat(chunks).toString(), 'data: first\n\ndata: second\n\n');
  await until(() => f.timers.size === 0, 'SSE completion must clear the deadline');
});

test('slow clients apply backpressure and resume without losing bytes', { timeout: 8_000 }, async t => {
  const chunk = Buffer.alloc(64 * 1024, 0xa5);
  const count = 512;
  let blocked = false;
  let writesWhileBlocked = 0;
  let peak = 0;
  const f = await fixture(t, async (_req, res) => {
    try {
      for (let i = 0; i < count; i++) {
        if (!res.write(chunk)) await once(res, 'drain');
      }
      res.end();
    } catch { /* Test teardown can close the upstream while it is paused. */ }
  }, { timeout: 5_000, observe: (_req, res) => {
    const write = res.write;
    res.write = function (...args) {
      if (this.writableNeedDrain) writesWhileBlocked++;
      const ok = write.apply(this, args);
      peak = Math.max(peak, this.writableLength);
      if (!ok) blocked = true;
      return ok;
    };
  } });
  const res = await f.request().response;
  res.pause();
  await until(() => blocked, 'test must actually reach downstream backpressure');
  await delay(150);
  assert.equal(writesWhileBlocked, 0, 'proxy kept writing after write() returned false');
  assert.ok(peak < 1024 * 1024, `proxy queued ${peak} bytes for a paused client`);
  let total = 0;
  for await (const data of res) {
    total += data.length;
    assert.ok(data.every(value => value === 0xa5));
  }
  assert.equal(total, chunk.length * count);
});

for (const stop of ['disconnect', 'timeout']) {
  test(`${stop} while backpressured closes both sides and clears the deadline`, { timeout: 5_000 }, async t => {
    let downstream;
    let upstreamClosed = false;
    const f = await fixture(t, async (_req, res) => {
      res.once('close', () => { upstreamClosed = true; });
      try {
        const chunk = Buffer.alloc(64 * 1024);
        for (let i = 0; i < 1024; i++) {
          if (!res.write(chunk)) await once(res, 'drain');
        }
        res.end();
      } catch { /* Aborting the pipeline closes this test producer. */ }
    }, { timeout: 800, observe: (_req, res) => { downstream = res; } });
    const res = await f.request().response;
    res.pause();
    await until(() => downstream.writableNeedDrain, 'test must stall the real destination', 600);
    if (stop === 'disconnect') res.destroy();
    await until(() => upstreamClosed && downstream.destroyed, `${stop} left a backpressured stream alive`, 1_500);
    assert.equal(f.timers.size, 0);
  });
}

for (const phase of ['before headers', 'during body']) {
  test(`client disconnect ${phase} aborts upstream and clears the deadline`, { timeout: 5_000 }, async t => {
    let started = false;
    let closed = false;
    const f = await fixture(t, (_req, res) => {
      started = true;
      res.once('close', () => { closed = true; });
      if (phase === 'during body') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.write('data: first\n\n');
      }
    });
    const { client, response } = f.request('/ask/stream');
    response.catch(() => {});
    await until(() => started, 'upstream must be connected before disconnect');
    if (phase === 'during body') (await response).destroy();
    else client.destroy();
    await until(() => closed, 'client disconnect left upstream alive', 600);
    await until(() => f.timers.size === 0, 'disconnect left a deadline timer alive');
  });
}

for (const partial of [false, true]) {
  test(`body deadline remains active after headers (partial=${partial})`, { timeout: 5_000 }, async t => {
    let closed = false;
    const f = await fixture(t, (_req, res) => {
      res.once('close', () => { closed = true; });
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.flushHeaders();
      if (partial) res.write('data: first\n\n');
    }, { timeout: 120 });
    let downstreamClosed = false;
    const { response } = f.request('/ask/stream');
    const result = response.then(async res => {
      try { await collect(res); } catch { /* Expected truncated stream. */ }
      downstreamClosed = true;
      assert.equal(res.complete, false, 'timed out body must not masquerade as complete');
    }, () => { downstreamClosed = true; });
    await until(() => closed, 'body timeout failed to abort upstream', 800);
    await until(() => downstreamClosed, 'body timeout failed to close downstream', 800);
    await result;
    assert.equal(f.timers.size, 0);
  });
}

test('headers timeout returns bounded JSON and aborts upstream', { timeout: 5_000 }, async t => {
  let closed = false;
  const f = await fixture(t, (_req, res) => { res.once('close', () => { closed = true; }); }, { timeout: 120 });
  const res = await f.request().response;
  assert.equal(res.statusCode, 504);
  assert.equal(JSON.parse((await collect(res)).toString()).error, 'gateway_timeout');
  assert.equal(res.headers['cache-control'], 'no-store');
  await until(() => closed, 'headers timeout must close upstream');
  assert.equal(f.timers.size, 0);
});

test('fetch failure returns 503 and clears its timer immediately', { timeout: 5_000 }, async t => {
  const f = await fixture(t, (req) => { req.socket.destroy(); });
  const res = await f.request().response;
  assert.equal(res.statusCode, 503);
  assert.equal(JSON.parse((await collect(res)).toString()).error, 'gateway_unavailable');
  assert.equal(f.timers.size, 0, 'rejected fetch leaked its deadline timer');
});

test('decoded gzip bytes never inherit the compressed Content-Length', { timeout: 5_000 }, async t => {
  const bytes = Buffer.from('binary\0\u00ff'.repeat(4096));
  const compressed = gzipSync(bytes);
  const f = await fixture(t, (_req, res) => {
    res.writeHead(200, {
      'Content-Type': 'application/octet-stream', 'Content-Encoding': 'gzip', 'Content-Length': compressed.length,
    });
    res.end(compressed);
  });
  const res = await f.request().response;
  assert.equal(res.headers['content-length'], undefined);
  assert.equal(res.headers['content-encoding'], undefined);
  assert.deepEqual(await collect(res), bytes);
});

test('redirect status, Location and cookies are relayed without following', { timeout: 5_000 }, async t => {
  let hits = 0;
  const f = await fixture(t, (req, res) => {
    hits++;
    if (req.url === '/api/redirect') {
      res.writeHead(302, { Location: '/api/destination', 'Set-Cookie': 'session=renewed; Path=/' });
    }
    res.end();
  });
  const res = await f.request('/redirect').response;
  await collect(res);
  assert.equal(res.statusCode, 302);
  assert.equal(res.headers.location, '/api/destination');
  assert.deepEqual(res.headers['set-cookie'], ['session=renewed; Path=/']);
  assert.equal(hits, 1);
});

for (const method of ['GET', 'HEAD']) {
  test(`${method} bodyless response ends and clears its deadline`, { timeout: 5_000 }, async t => {
    const f = await fixture(t, (_req, res) => {
      res.writeHead(method === 'HEAD' ? 200 : 204, { 'Content-Type': 'application/octet-stream' });
      res.end();
    });
    const res = await f.request('/', { method }).response;
    assert.equal((await collect(res)).length, 0);
    assert.equal(res.statusCode, method === 'HEAD' ? 200 : 204);
    assert.equal(f.timers.size, 0);
  });
}

test('upstream body failure truncates the stream instead of appending JSON', { timeout: 5_000 }, async t => {
  let upstreamResponse;
  const f = await fixture(t, (_req, res) => {
    upstreamResponse = res;
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: first\n\n');
  });
  const res = await f.request('/ask/stream').response;
  const chunks = [];
  res.on('data', chunk => chunks.push(chunk));
  res.on('error', () => {});
  await until(() => chunks.length > 0, 'first event must reach client');
  const closed = new Promise(resolve => res.once('close', resolve));
  upstreamResponse.destroy();
  await closed;
  assert.equal(res.complete, false);
  assert.equal(Buffer.concat(chunks).toString(), 'data: first\n\n');
  await until(() => f.timers.size === 0, 'stream failure left a deadline timer alive');
});
