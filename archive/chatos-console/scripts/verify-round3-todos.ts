/**
 * Round-3 todo state round-trip + organize polling honesty (Agent A).
 *
 * Exercises the REAL production mapping helpers — no hand-copy of the algorithm:
 *   - mapGatewayStatus(...)         gateway row -> frontend { status, isIgnored }
 *   - mapTodoStatusToGateway(...)  frontend state -> gateway status used by PATCH
 *   - isOrganizeTerminalStatus(...)  polling must stop immediately
 *   - summarizeOrganizeOutcome(...)   honest engine state + toast after polling
 *
 * Run (from apps/chatos-console):
 *   node --experimental-strip-types scripts/verify-round3-todos.ts
 */
import assert from 'node:assert/strict';
import {
  mapGatewayStatus,
  mapTodoStatusToGateway,
  isOrganizeTerminalStatus,
  summarizeOrganizeOutcome,
} from '../src/types.ts';
import type { TodoStatus } from '../src/types.ts';

// ---------------------------------------------------------------------------
// 1) Round-trip: every UI kanban state survives PATCH -> reload unchanged.
// ---------------------------------------------------------------------------
const UI_STATES: TodoStatus[] = ['draft', 'pending', 'in_progress', 'completed'];
for (const ui of UI_STATES) {
  const gatewayStatus = mapTodoStatusToGateway(ui);
  const back = mapGatewayStatus(gatewayStatus);
  assert.equal(
    back.status,
    ui,
    `round-trip broken for ${ui} (wrote gateway=${gatewayStatus}, read back=${back.status})`,
  );
  assert.equal(back.isIgnored, false, `non-ignored state must not be flagged ignored (${ui})`);
}

// ---------------------------------------------------------------------------
// 2) Historical compat: old gateway rows map honestly; ignored is preserved.
// ---------------------------------------------------------------------------
assert.equal(mapGatewayStatus('done').status, 'completed', "legacy 'done' must map to completed");
assert.equal(mapGatewayStatus('completed').status, 'completed');
assert.equal(mapGatewayStatus('todo').status, 'pending', "fresh/raw 'todo' shows as pending");
assert.equal(mapGatewayStatus('pending').status, 'pending');
assert.equal(mapGatewayStatus('draft').status, 'draft');
assert.equal(mapGatewayStatus('in_progress').status, 'in_progress');

const ignored = mapGatewayStatus('ignored');
assert.equal(ignored.isIgnored, true, 'ignored rows must keep the ignored flag (never dropped)');

// Unknown / empty gateway status degrades safely to pending instead of crashing.
assert.equal(mapGatewayStatus('').status, 'pending');
assert.equal(mapGatewayStatus('some_future_status').status, 'pending');

// ---------------------------------------------------------------------------
// 3) Polling terminal honesty.
// ---------------------------------------------------------------------------
for (const t of ['completed', 'degraded', 'failed', 'cancelled', 'interrupted']) {
  assert.ok(isOrganizeTerminalStatus(t), `${t} must be terminal (stop polling immediately)`);
}
for (const c of ['queued', 'running', 'retrying', 'pending', '']) {
  assert.ok(!isOrganizeTerminalStatus(c), `${c} must keep polling (not terminal)`);
}

// ---------------------------------------------------------------------------
// 4) Outcome decision: failed / cancelled / interrupted / timeout are NEVER idle.
// ---------------------------------------------------------------------------
assert.equal(summarizeOrganizeOutcome('completed', 5).engineStatus, 'idle');
assert.equal(summarizeOrganizeOutcome('degraded', 5).engineStatus, 'idle');

for (const bad of ['failed', 'cancelled', 'interrupted']) {
  const o = summarizeOrganizeOutcome(bad, 0);
  assert.notEqual(o.engineStatus, 'idle', `${bad} must not be reported as idle success`);
  assert.equal(o.timedOut, false, `${bad} is a real terminal outcome, not a timeout`);
  assert.ok(o.message.includes(bad), `toast must name the actual outcome (${bad})`);
}

// The critical fix: exhausting all 600 polls without reaching a terminal status
// is a TIMEOUT — engine state must NOT become idle (no fake success), and the
// toast must explicitly say timeout / unfinished.
{
  const timeout = summarizeOrganizeOutcome('running', 0);
  assert.equal(timeout.timedOut, true, 'non-terminal after loop exhaustion = timeout');
  assert.notEqual(timeout.engineStatus, 'idle', 'timeout must not pretend idle success');
  assert.ok(
    /超时|未完成/.test(timeout.message),
    `toast must honestly say timeout/unfinished, got: ${timeout.message}`,
  );
}

console.log('round3 todos verify: OK');
