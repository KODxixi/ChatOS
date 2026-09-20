import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const baseUrl = (process.env.CHATOS_GATEWAY_URL || 'http://127.0.0.1:8912').replace(/\/$/, '');
const pollMs = Number(process.env.CHATOS_SYNC_POLL_MS || 5000);
const timeoutMs = Number(process.env.CHATOS_SYNC_TIMEOUT_MS || 55 * 60 * 1000);
const knowledgeIndexTimeoutMs = Number(process.env.CHATOS_KNOWLEDGE_INDEX_TIMEOUT_MS || 70 * 60 * 1000);
const logDir = path.join(root, 'var', 'logs');
const logPath = path.join(logDir, 'hourly-sync.log');
let sessionCookie = '';

fs.mkdirSync(logDir, { recursive: true });

function log(event, details = {}) {
  const entry = { at: new Date().toISOString(), event, ...details };
  const line = JSON.stringify(entry);
  fs.appendFileSync(logPath, `${line}\n`, 'utf8');
  console.log(line);
}

async function requestJson(endpoint, options = {}) {
  const headers = { accept: 'application/json', ...(options.headers || {}) };
  if (sessionCookie) headers.cookie = sessionCookie;
  const response = await fetch(`${baseUrl}${endpoint}`, {
    ...options,
    headers,
    signal: AbortSignal.timeout(15_000),
  });
  const setCookies = response.headers.getSetCookie?.() ||
    (response.headers.get('set-cookie') ? [response.headers.get('set-cookie')] : []);
  const sessionSetCookie = setCookies.find(value => value.startsWith('wechat_ai_session='));
  if (sessionSetCookie) sessionCookie = sessionSetCookie.split(';', 1)[0];
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    const error = new Error(`${options.method || 'GET'} ${endpoint} returned ${response.status}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

async function runIncrementalOrganize() {
  const queued = await requestJson('/api/organize/incremental', { method: 'POST' });
  const jobId = queued.id || queued.jobId;
  if (!jobId) throw new Error(`organize response has no job id: ${JSON.stringify(queued)}`);
  log('job_queued', { jobId, status: queued.status });

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = await requestJson(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (['completed', 'degraded', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
      log('job_finished', { jobId, status: job.status, result: job.result ?? null, errorCode: job.errorCode ?? null });
      if (job.status === 'completed' || job.status === 'degraded') return job;
      throw new Error(`organize job ${job.status}: ${job.errorCode || 'unknown error'}`);
    }
    await delay(pollMs);
  }

  throw new Error(`organize job timed out after ${timeoutMs}ms: ${jobId}`);
}

async function runKnowledgeIndex() {
  const started = await requestJson('/api/knowledge/index/start', { method: 'POST' });
  log('knowledge_index_started', { status: started.status, stage: started.stage });

  const deadline = Date.now() + knowledgeIndexTimeoutMs;
  while (Date.now() < deadline) {
    const status = await requestJson('/api/knowledge/index/status');
    if (status.status === 'completed') {
      log('knowledge_index_finished', { status: status.status, stats: status.stats ?? null });
      return status;
    }
    if (['failed', 'interrupted'].includes(status.status)) {
      log('knowledge_index_finished', { status: status.status, error: status.error ?? null });
      throw new Error(`knowledge index ${status.status}: ${status.error || 'unknown error'}`);
    }
    await delay(pollMs);
  }

  throw new Error(`knowledge index timed out after ${knowledgeIndexTimeoutMs}ms`);
}

async function main() {
  const health = await requestJson('/api/health');
  if (health.app !== 'ready') {
    throw new Error(`gateway is not ready: ${JSON.stringify(health)}`);
  }

  const organize = await runIncrementalOrganize();
  await runKnowledgeIndex();
  log('pipeline_finished', { organizeStatus: organize.status, status: 'completed' });
}

main().catch(error => {
  log('job_failed', { message: error.message, status: error.status ?? null, body: error.body ?? null });
  process.exitCode = 1;
});
