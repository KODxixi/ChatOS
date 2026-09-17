import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

test('graph paints visible nodes and crossing edges, not offscreen decorations', () => {
  const source = ts.createSourceFile('Canvas.tsx', readFileSync(new URL(
    '../src/components/views/mindmap/MindmapCanvas.tsx', import.meta.url), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let effect;
  const visit = n => {
    if (ts.isCallExpression(n) && n.expression.getText(source) === 'useEffect' &&
      n.arguments[0].getText(source).includes('requestAnimationFrame(render)')) effect = n.arguments[0].getText(source);
    ts.forEachChild(n, visit);
  };
  visit(source);
  assert.ok(effect);
  const labels = [];
  const ctx = new Proxy({ fillText: text => labels.push(text) }, {
    get: (target, key) => target[key] ?? (() => {}),
  });
  const nodes = [
    { id: 'inside', x: 100, y: 100, label: 'visible' },
    { id: 'left', x: -2000, y: 100, label: 'offscreen-left' },
    { id: 'right', x: 2000, y: 100, label: 'offscreen-right' },
  ];
  const frames = [];
  const env = {
    canvasRef: { current: { getContext: () => ctx } },
    containerRef: { current: { clientWidth: 800, clientHeight: 600 } },
    simulationRef: { current: { nodes: () => nodes, force: () => ({ links: () => [
      { source: nodes[1], target: nodes[2], label: 'crossing-edge' },
    ] }) } },
    transformRef: { current: { x: 0, y: 0, k: 1 } },
    window: { devicePixelRatio: 1 }, photonsRef: { current: [] },
    layoutMode: 'affinity', selectedNodeId: null, hoveredNode: null, hoveredLink: null,
    newPulseNodeId: null, egoConnectedIds: null, showParticles: false,
    requestAnimationFrame: fn => { frames.push(fn); return frames.length; }, cancelAnimationFrame: () => {},
  };
  const js = ts.transpileModule(`return (${effect})();`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const cleanup = new Function(...Object.keys(env), js)(...Object.values(env));
  frames.shift()(); cleanup();
  assert.ok(labels.includes('visible'));
  assert.ok(labels.includes('crossing-edge'));
  assert.ok(!labels.some(s => s.startsWith('offscreen')), 'offscreen labels/glows should not be rasterized');
});

test('todo cards let the browser defer offscreen layout without dropping records', () => {
  const source = readFileSync(new URL('../src/components/views/TodosView.tsx', import.meta.url), 'utf8');
  assert.match(source, /contentVisibility:\s*'auto'/);
  assert.match(source, /containIntrinsicSize:/);
  assert.ok(source.includes('colTodos.map(todo =>'));
});

test('unbound demonstration identity does not depend on a broken external avatar', () => {
  const text = readFileSync(new URL('../src/mockData.ts', import.meta.url), 'utf8');
  const source = ts.createSourceFile('mockData.ts', text, ts.ScriptTarget.Latest, true);
  const user = source.statements.flatMap(s => ts.isVariableStatement(s) ? [...s.declarationList.declarations] : [])
    .find(d => d.name.getText(source) === 'CURRENT_USER');
  const js = ts.transpileModule(`return (${user.initializer.getText(source)});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  assert.match(new Function(js)().avatar, /^data:image\/svg\+xml/);
});
