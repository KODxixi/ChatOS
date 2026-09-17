import assert from 'node:assert/strict';

const baseUrl = process.env.CHATOS_BASE_URL || 'http://127.0.0.1:3108';

const pageResponse = await fetch(`${baseUrl}/`);
assert.equal(pageResponse.status, 200, 'production page must respond with 200');
assert.match(await pageResponse.text(), /<div id="root"><\/div>/, 'production page must contain the React root');

const healthResponse = await fetch(`${baseUrl}/api/health`);
assert.equal(healthResponse.status, 200, 'health endpoint must respond with 200');
const health = await healthResponse.json();
assert.equal(health.status, 'ok');
assert.equal(health.mode, 'canonical-mixed-data');
assert.equal(health.persistence, 'sqlite-readonly+memory-collaboration');
assert.equal(health.conversations?.status, 'ready');
assert.equal(health.conversations?.mode, 'sqlite-readonly');
assert.equal(health.model?.status, 'configured');
assert.equal(health.model?.provider, 'lm-studio-local');
assert.ok(health.model?.name, 'a configured local chat model must be named');
assert.equal(health.telemetry?.cacheHitRate, null);
assert.equal(health.telemetry?.throughput, null);
assert.equal(health.telemetry?.p99LatencyMs, null);

const thinkingResponse = await fetch(`${baseUrl}/api/local-model/thinking-analysis`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({
    query: '仅依据上下文，用一句话指出当前最优先事项。',
    contextData: { urgentTodos: [{ title: '完成生产契约验证', due: '今天' }] },
  }),
});
assert.equal(thinkingResponse.status, 200, 'local LM Studio analysis must succeed');
const thinking = await thinkingResponse.json();
assert.equal(thinking.provider, 'lm-studio-local');
assert.equal(thinking.modelUsed, health.model.name, 'the server must return the actual configured local model name');
assert.ok(thinking.analysis, 'the local model must return a real analysis');

const conversationsResponse = await fetch(`${baseUrl}/api/conversations/live`);
assert.equal(conversationsResponse.status, 200, 'local conversation database must be readable');
const conversations = await conversationsResponse.json();
assert.equal(conversations.source, 'sqlite-readonly');
assert.ok(conversations.total > 0, 'local database must return user conversations');
assert.equal(conversations.items.length, conversations.total);
assert.ok(conversations.items.every(item => item.name && typeof item.isGroup === 'boolean'));
assert.ok(
  conversations.items.filter(item => item.isGroup).every(item => item.groupTopic && item.groupTopic !== '个人关系'),
  'group conversations must use group semantics instead of a direct-contact label'
);

console.log(`Production contract verified: page 200, SQLite read-only conversations, local model ${thinking.modelUsed}.`);
