import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyBuild, validateLibraryDb, captureUiRuntime, gatewayIdentity, inspectService, settingsPath, UI_URL, GATEWAY_URL } from './entry-contract.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
try {
  const build = verifyBuild(root);
  const settings = JSON.parse(fs.readFileSync(settingsPath(root), 'utf8'));
  if (settings.frontendUrl !== UI_URL) throw new Error(`Set frontendUrl to ${UI_URL} in ${settingsPath(root)}`);
  validateLibraryDb(root);
  const args = process.argv.slice(2);
  if (args.some(arg => !['--live', '--preflight'].includes(arg))) throw new Error('Usage: node scripts/verify-entry.mjs [--live | --preflight]');
  if (args.length) {
    const ui = captureUiRuntime(root, build, path.join(root, 'apps/chatos-console/dist/server.cjs'));
    const gateway = gatewayIdentity(root);
    const states = {
      ui: await inspectService(UI_URL, ui), gateway: await inspectService(GATEWAY_URL, gateway),
      uiUrl: UI_URL, gatewayUrl: GATEWAY_URL,
    };
    if (args.includes('--preflight')) console.log(JSON.stringify(states));
    else {
      if (states.ui !== 'reuse' || states.gateway !== 'reuse') throw new Error(`Required service absent: ${JSON.stringify(states)}`);
      const health = await fetch(`${GATEWAY_URL}/api/health`, { signal: AbortSignal.timeout(5000) }).then(r => r.json());
      if (health.app !== 'ready') throw new Error('Gateway is not ready');
      const response = await fetch(GATEWAY_URL, { redirect: 'manual', signal: AbortSignal.timeout(3000) });
      if (response.status !== 307 || response.headers.get('location') !== `${UI_URL}/`) throw new Error('Gateway browser redirect mismatch');
      const html = await fetch(UI_URL, { signal: AbortSignal.timeout(3000) }).then(r => r.text());
      const localHtml = fs.readFileSync(path.join(root, 'apps/chatos-console/dist/index.html'), 'utf8');
      if (html !== localHtml) throw new Error('Live HTML differs from canonical build');
      for (const [, asset] of html.matchAll(/(?:src|href)="(\/(?!\/)[^"?#]+)"/g)) {
        const remote = await fetch(`${UI_URL}${asset}`, { signal: AbortSignal.timeout(5000) });
        if (!remote.ok || !Buffer.from(await remote.arrayBuffer()).equals(fs.readFileSync(path.join(root, 'apps/chatos-console/dist', asset)))) throw new Error(`Live asset mismatch: ${asset}`);
      }
      console.log(`PASS source/build/runtime/assets ${build.buildId}`);
    }
  } else console.log(`PASS source/build ${build.buildId}`);
} catch (error) {
  console.error(`ChatOS verification failed: ${error.message}`);
  process.exitCode = 1;
}
