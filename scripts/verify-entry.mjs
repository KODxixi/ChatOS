import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyTrackedSources, validateLibraryDb, gatewayIdentity, inspectService, GATEWAY_URL } from './entry-contract.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
try {
  verifyTrackedSources(root);
  validateLibraryDb(root);
  const gateway = gatewayIdentity(root);
  const args = process.argv.slice(2);
  if (args.some(arg => !['--live', '--preflight'].includes(arg))) throw new Error('Usage: node scripts/verify-entry.mjs [--live | --preflight]');
  if (args.length) {
    const states = { gateway: await inspectService(GATEWAY_URL, gateway), gatewayUrl: GATEWAY_URL };
    if (args.includes('--preflight')) console.log(JSON.stringify(states));
    else {
      if (states.gateway !== 'reuse') throw new Error(`Required service absent: ${JSON.stringify(states)}`);
      // /api/health 跑 operational 探针，依赖不在时实测 4-6 秒，超时必须留足。
      const health = await fetch(`${GATEWAY_URL}/api/health`, { signal: AbortSignal.timeout(20_000) }).then(r => r.json());
      if (health.app !== 'ready') throw new Error('Gateway is not ready');
      console.log(`PASS source/runtime ${gateway.startupCodeIdentity.slice(0, 12)}`);
    }
  } else console.log(`PASS source ${gateway.startupCodeIdentity.slice(0, 12)}`);
} catch (error) {
  console.error(`ChatOS verification failed: ${error.message}`);
  process.exitCode = 1;
}
