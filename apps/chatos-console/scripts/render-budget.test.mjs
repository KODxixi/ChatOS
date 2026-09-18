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

test('failed images use one local fallback without a retry loop or false original-image claim', () => {
  const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let handler;
  const visit = n => {
    if (ts.isJsxAttribute(n) && n.name.getText(source) === 'onErrorCapture') handler = n.initializer.expression.getText(source);
    ts.forEachChild(n, visit);
  };
  visit(source);
  assert.ok(handler, 'the application must handle failed avatars/thumbnails');
  const fallback = source.statements.flatMap(s => ts.isVariableStatement(s) ? [...s.declarationList.declarations] : [])
    .find(d => d.name.getText(source) === 'UNAVAILABLE_IMAGE');
  const js = ts.transpileModule(`const UNAVAILABLE_IMAGE=${fallback.initializer.getText(source)}; return ${handler};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  class Image { src = 'https://unavailable.test/image'; alt = 'Preview'; title = ''; }
  const callback = new Function('HTMLImageElement', js)(Image);
  const image = new Image();
  callback({ target: image });
  assert.match(image.src, /^data:image\/svg\+xml/);
  assert.match(image.title, /未加载|加载失败/);
  const previous = { ...image };
  callback({ target: image });
  assert.deepEqual({ ...image }, previous);
  callback({ target: {} });
});

test('conversation workspace defaults to daily focus and keeps an explicit path to every conversation', () => {
  const source = readFileSync(new URL('../src/components/views/ConversationsView.tsx', import.meta.url), 'utf8');
  const sidebar = readFileSync(new URL('../src/components/Sidebar.tsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
  assert.match(source, /useState<['"]focus['"]\s*\|\s*['"]all['"]>\(['"]focus['"]\)/);
  assert.match(source, /selectDailyFocusConversations\(conversations/);
  assert.match(source, /id=['"]conversation-mode-focus['"]/);
  assert.match(source, /id=['"]conversation-mode-all['"]/);
  assert.match(source, /workspaceMode\s*===\s*['"]all['"]\s*&&/);
  assert.doesNotMatch(source, /本地 Agent 聚焦|今日关注根据/);
  assert.match(source, /role="button"/);
  assert.match(source, /tabIndex=\{0\}/);
  assert.match(source, /onKeyDown=/);
  assert.match(css, /\.conversation-toolbar\s*\{[^}]*min-width:\s*0;/s);
  assert.doesNotMatch(css, /\.conversation-toolbar\s*\{[^}]*min-width:\s*56rem;/s);
  assert.match(css, /\.conversation-type-tabs\.gary-segmented\s*>\s*button[^}]*min-height:\s*var\(--gary-control-height\)/s);
  assert.match(sidebar, /useState<boolean>\(false\)/);
});
