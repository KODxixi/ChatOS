import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build as buildUi } from 'vite';
import { build as buildServer } from 'esbuild';
import { sourceIdentity, outputIdentity } from '../../../scripts/entry-contract.mjs';

const consoleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(consoleRoot, '../..');
process.chdir(consoleRoot);
const identity = sourceIdentity(root);
await buildUi();
await buildServer({
  entryPoints: ['server.ts'], bundle: true, platform: 'node', format: 'cjs',
  packages: 'external', sourcemap: true, outfile: 'dist/server.cjs',
  define: { __CHATOS_BUILD__: JSON.stringify(identity) },
});
if (identity.buildId !== sourceIdentity(root).buildId) throw new Error('Source changed during build; rebuild');
fs.writeFileSync('dist/build.json', JSON.stringify({ ...identity, outputHash: outputIdentity(path.join(consoleRoot, 'dist')) }, null, 2) + '\n');
console.log(`ChatOS build ${identity.buildId}`);
