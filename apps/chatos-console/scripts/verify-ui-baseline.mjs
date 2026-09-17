import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const conversations = readFileSync(new URL('../src/components/views/ConversationsView.tsx', import.meta.url), 'utf8').replaceAll('\r\n', '\n');
const context = readFileSync(new URL('../src/context/ChatOSContext.tsx', import.meta.url), 'utf8');
const header = readFileSync(new URL('../src/components/Header.tsx', import.meta.url), 'utf8');
const auxModals = readFileSync(new URL('../src/components/AuxModals.tsx', import.meta.url), 'utf8');
const sidebar = readFileSync(new URL('../src/components/Sidebar.tsx', import.meta.url), 'utf8');
const detail = readFileSync(new URL('../src/components/RightDetailPanel.tsx', import.meta.url), 'utf8');
const thinking = readFileSync(new URL('../src/components/ThinkingCopilotModal.tsx', import.meta.url), 'utf8');
const server = readFileSync(new URL('../server.ts', import.meta.url), 'utf8');
const mockData = readFileSync(new URL('../src/mockData.ts', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
const lightRays = readFileSync(new URL('../public/gary-light-rays.js', import.meta.url), 'utf8');

assert.match(conversations, /cardDensity === 'compact'/, 'AIstudio compact cards must stay');
assert.match(conversations, /cardDensity === 'standard'/, 'AIstudio standard cards must stay');
assert.match(conversations, /cardDensity === 'comfortable'/, 'AIstudio comfortable cards must stay');
assert.doesNotMatch(conversations, /workbench-conversation-layout/, 'replacement workbench must stay removed');
assert.match(app, /data-gary-scene-engine="light-rays"/, 'ShiBao light-rays scene must be mounted');
assert.match(app, /h-screen w-screen/, 'the 1920x1080 baseline must fill the viewport');
assert.match(styles, /vendor\/gary\/tokens\/base\.css/, 'Gary 2.0 token entry must be loaded');
assert.match(styles, /\.conversation-card-surface/, 'card surface override must exist');
assert.equal(
  (styles.match(/:not\(\.conversation-card-surface\)/g) || []).length,
  2,
  'legacy rounded-card overrides must not erase direct/group card materials'
);
assert.match(styles, /--chatos-font-ui:/, 'Apple-style platform font stack must be explicit');
assert.match(styles, /--chatos-canvas:\s*var\(--gary-surface-page\)/, 'page canvas must reuse Gary pure-black scene token');
assert.match(styles, /--chatos-shell-surface:\s*var\(--gary-glass-ultrathin\)/, 'shells must use the transparent Gary layer');
assert.match(
  styles,
  /--chatos-card-surface:\s*color-mix\(in srgb, var\(--gary-glass-regular\) 60%, transparent\)/,
  'direct cards must expose the ShiBao background through a Gary-derived surface'
);
assert.match(
  styles,
  /\.conversation-card-surface\.is-direct\s*\{[\s\S]*?background:\s*var\(--chatos-card-surface\) !important;/,
  'direct cards must use the transparent card surface instead of the opaque legacy glass token'
);
assert.match(
  styles,
  /--chatos-card-group-surface:\s*color-mix\(in srgb, var\(--gary-glass-thick\) 57%, transparent\)/,
  'group cards must stay visually distinct without becoming opaque'
);
assert.match(
  styles,
  /\.chatos-app \.chatos-sidebar,\s*\.chatos-app \.chatos-header,\s*\.chatos-app \.chatos-inspector/,
  'Gary shell material must outrank legacy background utilities'
);
assert.doesNotMatch(
  styles,
  /--chatos-(?:canvas|surface-[123]|text-(?:primary|secondary|tertiary)|separator):\s*oklch\(/,
  'neutral surfaces and text must not introduce a blue hue'
);
assert.match(lightRays, /raysSpeed:\s*1\.5,/, 'ShiBao ray speed must match the live source');
assert.match(lightRays, /lightSpread:\s*0\.8,/, 'ShiBao ray spread must match the live source');
assert.match(lightRays, /rayLength:\s*1\.2,/, 'ShiBao ray length must match the live source');
assert.match(lightRays, /followMouse:\s*true,/, 'ShiBao rays must keep the subtle pointer response');
assert.match(lightRays, /mouseInfluence:\s*0\.1,/, 'ShiBao pointer response must remain restrained');
assert.match(lightRays, /noiseAmount:\s*0\.1,/, 'ShiBao breathing texture must match the live source');
assert.match(lightRays, /distortion:\s*0\.05,/, 'ShiBao breathing distortion must match the live source');
assert.match(
  styles,
  /\.chatos-scene\[data-scene-status="ready"\] \.portal-scene-canvas,[\s\S]*?opacity:\s*0\.8;/,
  'the ready scene must retain ShiBao canvas opacity'
);
assert.match(styles, /mix-blend-mode:\s*screen;/, 'the ShiBao light scene must use screen blending');
assert.match(
  styles,
  /\.chatos-app \.chatos-main\s*\{[\s\S]*?background:\s*transparent !important;/,
  'the central workspace must outrank legacy opaque background utilities'
);
assert.match(styles, /--chatos-type-title:/, 'semantic product type scale must exist');
assert.match(styles, /\.chatos-rich-text/, 'rich text must have an explicit readable role');
assert.equal(
  (conversations.match(/className="conversation-card-name /g) || []).length,
  3,
  'every card density must keep conversation names on one line'
);
assert.match(styles, /\.conversation-card-name\s*\{[^}]*white-space:\s*nowrap;/s, 'card names must never wrap');
assert.match(styles, /\.conversation-card-name\s*\{[^}]*text-overflow:\s*clip;/s, 'card names must not be ellipsized');
assert.doesNotMatch(conversations, />\s*私信(?:\s*1v1)?\s*</, 'direct-chat labels must not consume the name row');
assert.doesNotMatch(conversations, />\s*群聊(?:\s*·[^<]*)?\s*</, 'group-chat labels must not consume the name row');
assert.match(conversations, /is-group/, 'group cards must expose a visual material class');
assert.match(conversations, /is-direct/, 'direct cards must expose a visual material class');
assert.match(conversations, /gary-glass/, 'conversation cards must use the canonical Gary glass material');
assert.doesNotMatch(
  conversations,
  /\+\{conv\.unreadCount\} 新消息/,
  'comfortable cards must keep unread badges compact so names retain priority'
);
assert.match(header, /chatos-workspace-title/, 'workspace title must use a semantic type role');
assert.match(header, /chatos-workspace-subtitle/, 'workspace subtitle must use a semantic type role');
assert.doesNotMatch(header, /tracking-wide/, 'workspace titles must not use generic wide tracking');
assert.match(header, /gary-web-bar gary-ultrathin/, 'workspace navigation must reuse the Gary WebBar material');
assert.match(header, /chatos-header-actions/, 'workspace navigation actions must be grouped for progressive disclosure');
assert.match(header, /chatos-control-center/, 'expanded navigation must use one control-center surface');
assert.match(header, /chatos-control-tile/, 'control-center functions must use solid inner tiles instead of nested glass');
assert.match(header, /conversation-toolbar-host/, 'conversation controls must mount inside the hover WebBar');
assert.doesNotMatch(header, /\bh-16\b/, 'desktop navigation must not reserve a permanent 64px row');
assert.match(conversations, /createPortal/, 'conversation controls must leave the main content flow');
assert.match(conversations, /conversation-type-module/, 'conversation type controls must occupy one control-center tile');
assert.match(conversations, /conversation-search-module/, 'conversation search must occupy one control-center tile');
assert.match(conversations, /conversation-density-module/, 'density controls must occupy one control-center tile');
const conversationControlGroups = conversations.match(/<div[^>]*role="group"[^>]*>/g) || [];
assert.ok(conversationControlGroups.length > 0, 'conversation controls must expose named groups');
assert.ok(
  conversationControlGroups.every(group => group.includes('gary-segmented')),
  'every conversation control group must inherit the canonical single-line segmented-control contract'
);
assert.match(conversations, /conversation-alert-rail/, 'freed workspace space must expose live reminders');
assert.match(conversations, /selectConversationAlerts\(conversations\)/, 'reminders must be derived from live conversation data');
const alertCopySource = conversations.slice(
  conversations.indexOf('const renderAlertCopy'),
  conversations.indexOf('\n\n  return (', conversations.indexOf('const renderAlertCopy'))
);
const alertRailSource = conversations.slice(
  conversations.indexOf('<section className="conversation-alert-rail"'),
  conversations.indexOf('</section>', conversations.indexOf('<section className="conversation-alert-rail"'))
);
assert.doesNotMatch(alertCopySource, /<(?:AlertTriangle|Star)\b/, 'scrolling reminder items must be text-only');
assert.doesNotMatch(alertRailSource, /重点提醒|conversation-alert-label/, 'the redundant reminder label and its reserved slot must be removed');
assert.equal(
  (alertCopySource.match(/data-gary-surface="optical"/g) || []).length,
  2,
  'both reminder copies must use the Gary optical glass surface'
);
assert.equal(
  (alertCopySource.match(/data-gary-material="glass"/g) || []).length,
  2,
  'both reminder copies must activate the Gary glass material contract'
);
assert.doesNotMatch(styles, /\.conversation-alert-label\s*\{/, 'removed reminder labels must not leave dead CSS');
assert.doesNotMatch(
  styles,
  /\.conversation-alert-item\s*\{[^}]*background:\s*var\(--gary-action-fill\)/s,
  'local reminder styles must not cover the Gary optical surface'
);
assert.match(
  styles,
  /@media \(min-width: 768px\) and \(hover: hover\) and \(pointer: fine\)[\s\S]*?\.chatos-header:is\(:hover, :focus-within\)/,
  'fine-pointer navigation must expand on hover and keyboard focus'
);
assert.match(
  styles,
  /\.chatos-header\s*\{[\s\S]*?--chatos-header-peek:\s*1rem;/,
  'collapsed navigation must expose only a compact discovery rail'
);
assert.match(styles, /@keyframes chatos-alert-scroll/, 'desktop reminders must use a compositor-only horizontal track');
assert.match(styles, /animation:\s*chatos-alert-scroll 96s linear infinite;/, 'desktop reminders must move at a readable pace');
assert.match(
  styles,
  /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.conversation-alert-track\.is-moving\s*\{[\s\S]*?animation:\s*none !important;/,
  'reminder motion must stop when reduced motion is requested'
);
assert.match(detail, /chatos-message-meta/, 'message metadata must have a distinct role');
assert.match(detail, /chatos-message-bubble/, 'message copy must have a distinct rich-text surface');
assert.match(context, /matchMedia\('\(max-width: 767px\)'\)/, 'narrow screens must start with collapsed navigation');
assert.match(styles, /@media \(max-width: 767px\)/, 'narrow-screen navigation styling must exist');
assert.doesNotMatch(app, /floating-thinking-trigger-btn/, 'Thinking Mode must not float over the main workspace');
assert.match(sidebar, /sidebar-thinking-trigger-btn/, 'Thinking Mode must live in the sidebar tool area');
assert.match(sidebar, /chatos-sidebar gary-glass gary-ultrathin/, 'sidebar must use canonical Gary ultrathin glass');
assert.match(sidebar, /chatos-sidebar-nav-item/, 'sidebar navigation must use the compact semantic navigation treatment');
assert.match(
  styles,
  /\.chatos-sidebar-nav-item\s*\{[^}]*font-size:\s*var\(--chatos-type-caption\);[^}]*background:\s*transparent !important;/s,
  'sidebar navigation must keep compact type and remove persistent card fills'
);
assert.match(
  sidebar,
  /chatos-sidebar-tools[\s\S]*?id="sidebar-sync-btn"[\s\S]*?id="aux-audit-btn"[\s\S]*?id="aux-graph-btn"[\s\S]*?id="aux-engine-btn"[\s\S]*?id="sidebar-thinking-trigger-btn"/,
  'incremental organize must be the first action in the shared five-tool dock'
);
assert.equal(
  (sidebar.match(/className="chatos-sidebar-tool"/g) || []).length,
  5,
  'the five bottom utilities must share one background-free tool treatment'
);
assert.doesNotMatch(sidebar, /Quick Auto-Organize Action/, 'incremental organize must not reserve a separate sidebar row');
assert.match(
  styles,
  /\.chatos-sidebar-tool\s*\{[^}]*background:\s*transparent !important;/s,
  'bottom utility icons must not have persistent card backplates'
);
assert.match(detail, /chatos-inspector gary-glass gary-ultrathin/, 'inspector must use canonical Gary ultrathin glass');
assert.match(context, /\/api\/conversations\/live/, 'conversation state must refresh from the local database endpoint');
assert.doesNotMatch(thinking, /gemini-3\.1-pro-preview/, 'Thinking Mode must never display a fabricated model name');
assert.match(thinking, /\/api\/local-model\/thinking-analysis/, 'Thinking Mode must call the local-model endpoint');
assert.match(thinking, /if \(!response\.ok\)/, 'Thinking Mode must surface model gateway failures');
assert.match(server, /LOCAL_LLM_BASE_URL/, 'the server must use the configurable local LM Studio endpoint');
assert.match(server, /\/chat\/completions/, 'the server must make a real local chat-completion request');
assert.doesNotMatch(server, /\/api\/gemini\/thinking-analysis/, 'the obsolete Gemini endpoint must be removed');
assert.doesNotMatch(
  [header, auxModals, thinking, mockData].join('\n'),
  /gemini-3\.1-pro-preview|Gemini Reasoning|模型待联调|尚未接入 ChatOS 本地模型/,
  'no visible UI source may retain the obsolete Gemini or unconfigured-model claims'
);
assert.match(header, /本地模型/, 'the workspace model control must identify the local-model path');

console.log('UI baseline preserved; Gary tokens and ShiBao light rays are wired.');
