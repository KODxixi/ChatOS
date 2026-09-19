import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const conversations = readFileSync(new URL('../src/components/views/ConversationsView.tsx', import.meta.url), 'utf8').replaceAll('\r\n', '\n');
const context = readFileSync(new URL('../src/context/ChatOSContext.tsx', import.meta.url), 'utf8');
const rightPanel = readFileSync(new URL('../src/components/RightDetailPanel.tsx', import.meta.url), 'utf8');
const auxModals = readFileSync(new URL('../src/components/AuxModals.tsx', import.meta.url), 'utf8');
const sidebar = readFileSync(new URL('../src/components/Sidebar.tsx', import.meta.url), 'utf8');
const detail = readFileSync(new URL('../src/components/RightDetailPanel.tsx', import.meta.url), 'utf8');
const thinking = readFileSync(new URL('../src/components/ThinkingCopilotModal.tsx', import.meta.url), 'utf8');
const todos = readFileSync(new URL('../src/components/views/TodosView.tsx', import.meta.url), 'utf8');
const projects = readFileSync(new URL('../src/components/views/ProjectsView.tsx', import.meta.url), 'utf8');
const library = readFileSync(new URL('../src/components/views/LibraryView.tsx', import.meta.url), 'utf8');
const server = readFileSync(new URL('../server.ts', import.meta.url), 'utf8');
const mockData = readFileSync(new URL('../src/mockData.ts', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
const lightRays = readFileSync(new URL('../public/gary-light-rays.js', import.meta.url), 'utf8');

assert.match(conversations, /<option value="compact">紧凑<\/option>/, 'AIstudio compact cards must stay');
assert.match(conversations, /<option value="standard">标准<\/option>/, 'AIstudio standard cards must stay');
assert.match(conversations, /<option value="comfortable">舒适<\/option>/, 'AIstudio comfortable cards must stay');
assert.match(
  conversations,
  /const effectiveDensity = workspaceMode === 'focus' \? 'comfortable' : cardDensity;/,
  'today focus must use the large comfortable card layout'
);
assert.doesNotMatch(conversations, /workbench-conversation-layout/, 'replacement workbench must stay removed');
assert.match(app, /data-gary-scene-engine="light-rays"/, 'ShiBao light-rays scene must be mounted');
assert.match(app, /h-screen w-screen/, 'the 1920x1080 baseline must fill the viewport');
assert.match(styles, /vendor\/gary\/tokens\/base\.css/, 'Gary 2.0 token entry must be loaded');
assert.match(styles, /\.conversation-card-surface/, 'card surface override must exist');
assert.doesNotMatch(
  styles,
  /html,\s*body,\s*#root\s*\{[^}]*min-width:\s*320px;/s,
  'the app root must not force horizontal overflow below the mobile viewport'
);
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
  /\.chatos-app \.chatos-sidebar,\s*\.chatos-app \.chatos-inspector/,
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
assert.match(conversations, /conversation-card-meta/, 'conversation timing metadata must expose a no-wrap role');
assert.match(styles, /\.conversation-card-meta,[\s\S]*?white-space:\s*nowrap;/, 'conversation timing metadata must not collapse into vertical text');
assert.doesNotMatch(conversations, /<span[^>]*>\s*私信(?:\s*1v1)?\s*<\/span>/, 'direct-chat labels must not consume the name row');
assert.doesNotMatch(conversations, /<span[^>]*>\s*群聊(?:\s*·[^<]*)?\s*<\/span>/, 'group-chat labels must not consume the name row');
assert.match(conversations, /is-group/, 'group cards must expose a visual material class');
assert.match(conversations, /is-direct/, 'direct cards must expose a visual material class');
assert.match(conversations, /gary-glass/, 'conversation cards must use the canonical Gary glass material');
assert.match(conversations, /is-primary/, 'the ranked primary conversation must expose the floating surface role');
assert.match(conversations, /is-secondary/, 'secondary conversations must expose the background surface role');
assert.doesNotMatch(
  conversations,
  /\+\{conv\.unreadCount\} 新消息/,
  'comfortable cards must keep unread badges compact so names retain priority'
);
assert.doesNotMatch(app, /import \{ Header \}|<Header\b/, 'the folded top WebBar must be removed from the formal layout');
assert.doesNotMatch(styles, /--chatos-header-peek|\.chatos-header\s*\{/, 'removed top WebBar must not leave a hidden CSS state machine');
assert.match(sidebar, /id="sidebar-view-btn"[\s\S]*?id="conversation-toolbar-host"/, 'conversation controls must mount in the bottom view tool');
assert.doesNotMatch(sidebar, /chatos-sidebar-conversation-tools/, 'the old persistent conversation view block must be removed');
assert.match(sidebar, /sidebar-global-search-btn[\s\S]*?chatos-sidebar-nav-item/, 'global search must be a first-level sidebar entry');
assert.doesNotMatch([app, sidebar].join('\n'), /onOpenCreateModal|sidebar-create-btn/, 'unused quick create must not consume sidebar space');
assert.match(app, /activeWorkspace === 'today'\s*&&\s*<ConversationsView mode="focus"/, 'today must be an independent workspace');
assert.match(app, /activeWorkspace === 'conversations'\s*&&\s*<ConversationsView mode="all"/, 'all conversations must be an independent workspace');
assert.match(conversations, /createPortal/, 'conversation controls must leave the main content flow');
assert.match(conversations, /conversation-search-module/, 'conversation search must remain grouped');
assert.match(conversations, /conversation-density-module/, 'density controls must remain grouped');
assert.match(conversations, /id="conversation-options-panel"/, 'conversation filters and density must stay expanded inside the view panel');
assert.doesNotMatch(conversations, /id="conversation-options-toggle"|isOptionsOpen/, 'conversation filters must not hide behind a disclosure');
assert.match(styles, /\.conversation-option-field select,[\s\S]*?color-scheme:\s*dark;/, 'native select popups must retain the dark system fallback');
assert.match(
  styles,
  /@supports \(appearance:\s*base-select\)[\s\S]*?select::picker\(select\)[\s\S]*?background:\s*var\(--gary-solid-plate\);/,
  'supported browsers must render select popups as a Gary solid transient surface'
);
assert.doesNotMatch(conversations, /id="conversation-mode-(?:focus|all|starred)"/, 'today and all must not be duplicated inside conversation controls');
assert.doesNotMatch(styles, /\.conversation-type-tabs button > span:last-child/, 'narrow layouts must not hide a tab when its label is the only span');
assert.match(styles, /\.conversation-control-module\s*\{[\s\S]*?background:\s*transparent !important;/, 'control groups must be flat inside the single glass surface');
assert.match(styles, /\.conversation-card-surface\.is-secondary\s*\{[\s\S]*?backdrop-filter:\s*none !important;/, 'secondary conversations must not use glass');
assert.match(
  conversations,
  /selectDailyPriorityConversations\(dailyFocusConversations\)/,
  'daily focus must derive one priority card per conversation channel'
);
assert.doesNotMatch(
  conversations,
  /selectedConversation && visibleConversations\.some/,
  'selection state must not redefine the business-priority lead card'
);
const conversationTabLists = conversations.match(/<div[^>]*role="tablist"[^>]*>/g) || [];
assert.equal(conversationTabLists.length, 0, 'sidebar workspaces must replace duplicate conversation view tabs');
assert.doesNotMatch(styles, /\.conversation-grid--comfortable \.conversation-card-surface\.is-primary\s*\{[^}]*grid-column:/s, 'the three daily priority cards must share one equal desktop row');
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
  0,
  'scrolling reminders must not create one glass card per item'
);
assert.equal(
  (alertCopySource.match(/data-gary-material="glass"/g) || []).length,
  0,
  'scrolling reminders must stay on the rail text layer'
);
assert.doesNotMatch(alertCopySource, /conversation-alert-item gary-glass/, 'scrolling reminders must not use per-item glass');
assert.doesNotMatch(styles, /\.conversation-alert-label\s*\{/, 'removed reminder labels must not leave dead CSS');
assert.doesNotMatch(
  styles,
  /\.conversation-alert-item\s*\{[^}]*background:\s*var\(--gary-action-fill\)/s,
  'local reminder styles must not cover the Gary optical surface'
);
assert.match(styles, /@keyframes chatos-alert-scroll/, 'desktop reminders must use a compositor-only horizontal track');
assert.match(styles, /animation:\s*chatos-alert-scroll 96s linear infinite;/, 'desktop reminders must move at a readable pace');
assert.match(
  styles,
  /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.conversation-alert-track\.is-moving\s*\{[\s\S]*?animation:\s*none !important;/,
  'reminder motion must stop when reduced motion is requested'
);
assert.match(context, /localStorage\.getItem\('chatos-alert-motion-enabled'\)/, 'ChatOS must persist its own alert-motion preference');
assert.match(sidebar, /role="menuitemcheckbox"[\s\S]*?允许弹幕滚动/, 'the system menu must expose the independent alert-motion setting');
assert.match(styles, /prefers-reduced-motion: reduce\) and \(hover: hover\)[\s\S]*?\.is-motion-allowed[\s\S]*?chatos-alert-scroll 96s linear infinite !important;/, 'an explicit app opt-in may re-enable only the alert rail on precise pointers');
assert.match(
  styles,
  /@media \(prefers-reduced-motion: reduce\)[\s\S]*?transition-duration:\s*0\.01ms !important;/,
  'reduced motion must also disable component transitions'
);
assert.match(rightPanel, /const conversationId = encodeURIComponent\(selectedConversation\.id\)/, 'detail panel must bind insight requests to the selected conversation');
assert.match(rightPanel, /conversations\/\$\{conversationId\}\/summary/, 'detail panel must load the derived recent summary');
assert.match(rightPanel, /conversations\/\$\{conversationId\}\/media/, 'detail panel must load media records');
assert.match(rightPanel, /mediaCount/, 'detail panel must expose media counts');
assert.match(rightPanel, /importantMessages/, 'recent insight must retain source-backed important messages');
assert.match(rightPanel, /重要消息摘录/, 'the inspector must label the compact evidence list');
assert.match(rightPanel, /importantMessages\.slice\(0, 10\)/, 'the inspector must render ten recent important extracts');
assert.doesNotMatch(rightPanel, /近 90 天完整上下文时间线|chatos-message-bubble|handleSendReply/, 'detail panel must not render chat history or a reply composer');
assert.match(rightPanel, /md:w-\[440px\][\s\S]*xl:w-\[480px\]/, 'detail panel must preserve the main workspace at desktop widths');
assert.match(context, /matchMedia\('\(max-width: 767px\)'\)/, 'narrow screens must start with collapsed navigation');
assert.match(styles, /@media \(max-width: 767px\)/, 'narrow-screen navigation styling must exist');
assert.match(styles, /\.chatos-sidebar\.w-16\s*\{[^}]*overflow:\s*visible !important;/s, 'collapsed navigation must not clip transient tool panels');
assert.doesNotMatch(app, /floating-thinking-trigger-btn/, 'Thinking Mode must not float over the main workspace');
assert.match(sidebar, /sidebar-thinking-trigger-btn/, 'Thinking Mode must live in the sidebar tool area');
assert.match(sidebar, /chatos-sidebar gary-glass gary-ultrathin/, 'sidebar must use canonical Gary ultrathin glass');
assert.match(sidebar, /aria-label=\{isSidebarCollapsed \? '展开侧边栏' : '折叠侧边栏'\}/, 'collapsed sidebar must expose an explicit expand action');
assert.doesNotMatch(sidebar, /isSidebarCollapsed && \(\s*<img src="\/chatos-mark\.svg"/, 'collapsed sidebar header must not leave the brand icon in place of the expand action');
assert.match(sidebar, /chatos-sidebar-nav-item/, 'sidebar navigation must use the compact semantic navigation treatment');
assert.match(sidebar, /收藏[\s\S]*归档[\s\S]*自定义/, 'conversation navigation must expose the special second-level groups');
assert.match(context, /name: '个人'[\s\S]*name: '工作'[\s\S]*name: '学习'/, 'conversation navigation must seed the requested user-managed groups');
assert.match(sidebar, /\bonDrop=/, 'conversation groups must accept native card drops');
assert.match(sidebar, /moveConversationToGroup/, 'conversation drops must persist through the shared context action');
assert.match(conversations, /\bdraggable\b/, 'conversation cards must be native drag sources');
assert.match(conversations, /application\/x-chatos-conversation/, 'conversation cards must expose a stable native drag payload');
assert.match(conversations, /has-unseen-important/, 'unseen important conversations must expose one restrained attention state');
assert.match(styles, /@keyframes chatos-important-breathe/, 'important conversation attention must use a slow whole-card material breath');
assert.match(styles, /filter:\s*brightness/, 'important conversation breath must modulate the whole card material');
assert.doesNotMatch(styles, /has-unseen-important[^}]*::after[\s\S]{0,500}border:/, 'important attention must not animate a decorative border overlay');
assert.match(styles, /prefers-reduced-motion: reduce[\s\S]*\.has-unseen-important/, 'important attention must retain a static reduced-motion fallback');
assert.doesNotMatch(conversations, /const handleSelect = \(conv: Conversation\) => \{\s*acknowledgeImportantConversation/, 'a click must not dismiss attention before detail evidence loads');
assert.match(detail, /setInsightState\('ready'\);\s*acknowledgeImportantConversation\(selectedConversation\)/, 'important attention must stop only after the detail evidence becomes visible');
assert.equal(
  (sidebar.match(/type: '(?:today|conversations|todos|projects|library)'/g) || []).length,
  5,
  'the sidebar must expose today plus the four primary data workspaces'
);
assert.match(sidebar, /type: 'today'/, 'today must be a first-level sidebar workspace');
assert.ok(
  sidebar.indexOf("type: 'conversations'") < sidebar.indexOf("type: 'today'"),
  'conversations must be the first workspace in the sidebar'
);
assert.ok(
  sidebar.indexOf('{navItems.map') < sidebar.indexOf('id="sidebar-global-search-btn"'),
  'the conversation homepage must appear above the search utility'
);
assert.match(context, /useState<WorkspaceType>\('conversations'\)/, 'conversations must be the default homepage');
assert.match(
  sidebar,
  /item\.type === 'conversations'\s*&&\s*activeWorkspace === 'conversations'\s*&&\s*!isCommandPaletteOpen\s*&&\s*!isSidebarCollapsed/,
  'conversation secondary groups must collapse outside the conversation workspace and search'
);
assert.doesNotMatch(sidebar, /type: 'mindmap'/, 'mindmap must move out of the primary workspace list');
assert.match(sidebar, /matchMedia\('\(max-width: 767px\)'\)\.matches[\s\S]*setIsSidebarCollapsed\(true\)/, 'mobile workspace navigation must dismiss the sidebar drawer');
assert.match(
  styles,
  /\.chatos-sidebar-nav-item\s*\{[^}]*font-size:\s*var\(--chatos-type-caption\);[^}]*background:\s*transparent !important;/s,
  'sidebar navigation must keep compact type and remove persistent card fills'
);
assert.match(
  styles,
  /\.chatos-sidebar-nav\s*\{[^}]*gap:\s*0;[^}]*margin-top:\s*0;[^}]*padding:\s*0 var\(--gary-space-2\);/s,
  'sidebar navigation must remove vertical dead space without shrinking the 44px targets'
);
assert.equal(
  (sidebar.match(/id="sidebar-(?:insights|system|view)-btn"/g) || []).length,
  3,
  'bottom utilities must expose insight, system, and view as three peers'
);
assert.match(sidebar, /id="sidebar-insights-btn"/, 'graph and reasoning must share one insight entry');
assert.match(sidebar, /id="sidebar-system-btn"/, 'organize, audit, and engine state must share one system entry');
assert.match(sidebar, /id="sidebar-view-btn"/, 'conversation filters must share one view entry');
assert.match(sidebar, /id="sidebar-mindmap-entry"[\s\S]*?id="aux-graph-btn"[\s\S]*?id="sidebar-thinking-trigger-btn"/, 'the insight menu must contain graph and reasoning modes');
assert.match(sidebar, /id="sidebar-sync-btn"[\s\S]*?id="aux-audit-btn"[\s\S]*?id="aux-engine-btn"/, 'the system menu must contain organize, audit, and model state');
assert.doesNotMatch(sidebar, /Quick Auto-Organize Action/, 'incremental organize must not reserve a separate sidebar row');
assert.match(
  styles,
  /\.chatos-sidebar-tool\s*\{[^}]*background:\s*transparent !important;/s,
  'bottom utility icons must not have persistent card backplates'
);
assert.match(
  styles,
  /\.chatos-sidebar-tools\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/s,
  'insight, system, and view must share one equal row'
);
assert.match(sidebar, /ref=\{sidebarToolsRef\}/, 'all sidebar tool popovers must share one click-away boundary');
assert.match(
  sidebar,
  /document\.addEventListener\('pointerdown', handlePointerDown\)[\s\S]*?document\.removeEventListener\('pointerdown', handlePointerDown\)/,
  'sidebar tool popovers must dismiss on an outside pointer press'
);
assert.match(
  styles,
  /\.conversation-option-field select,[\s\S]*?text-align:\s*center;[\s\S]*?text-align-last:\s*center;/,
  'view control labels must be centered inside their buttons'
);
assert.match(detail, /chatos-inspector gary-glass gary-ultrathin/, 'inspector must use canonical Gary ultrathin glass');
assert.match(todos, /role="tablist"[^>]*aria-label="待办视图"/, 'todo personal and collaboration modes must be the only todo tabs');
assert.match(todos, /id="todo-role-select"/, 'todo responsibility choices must be a filter, not a row of tabs');
assert.doesNotMatch(todos, /id="todo-filter-(?:all|responsible|promised|follow|other)"/, 'legacy todo filter pills must be removed');
assert.match(projects, /role="tablist"[^>]*aria-label="项目视图"/, 'project status must use one two-option tab list');
assert.match(library, /role="tablist"[^>]*aria-label="资料视图"/, 'library must expose one compact view tab list');
assert.match(library, /id="lib-status-pending"/, 'library must keep one combined pending view');
assert.doesNotMatch(library, /id="lib-status-(?:stored|missing)"/, 'library status details must not become extra tabs');
assert.match(library, /<section className="gary-glass gary-regular rounded-\[var\(--gary-radius-control\)\] p-5"/, 'library header must use the rounded Gary glass surface');
assert.doesNotMatch(library, /--gary-radius-(?:sm|lg)/, 'library must not use undefined Gary radius tokens');
assert.match(library, /aria-label="本地资料库统计"/, 'library must present aggregate knowledge statistics');
assert.match(library, /按类型[ -￿]*按会话[ -￿]*按项目/, 'library stats must be ordered by type, conversation, and project');
assert.match(styles, /\.chatos-app select/, 'all native expand controls must use the shared dark picker surface');
assert.match(context, /\/api\/conversations\/live/, 'conversation state must refresh from the local database endpoint');
assert.match(server, /COALESCE\(o\.classification_status, c\.classification_status, 'needs_review'\) AS classification_status/, 'live conversation labels must read effective classification confidence');
assert.match(server, /domainLabel\(domain, classificationStatus\)/, 'direct-chat labels must use the inferred relationship and its confidence');
assert.match(server, /needs_review[\s\S]*?关系待判断/, 'uncertain direct-chat relationships must be labeled honestly');
assert.doesNotMatch(thinking, /gemini-3\.1-pro-preview/, 'Thinking Mode must never display a fabricated model name');
assert.match(thinking, /\/api\/local-model\/thinking-analysis/, 'Thinking Mode must call the local-model endpoint');
assert.match(thinking, /if \(!response\.ok\)/, 'Thinking Mode must surface model gateway failures');
assert.match(server, /LOCAL_LLM_BASE_URL/, 'the server must use the configurable local LM Studio endpoint');
assert.match(server, /\/chat\/completions/, 'the server must make a real local chat-completion request');
assert.match(server, /PRAGMA table_info\(contact\)/, 'the UI adapter must discover the decrypted WeChat contact source');
assert.match(server, /small_head_url[\s\S]*?big_head_url/, 'conversation avatars must prefer real WeChat profile URLs');
assert.match(server, /avatarSource\.urls\.get\(senderId\)/, 'group participants and message senders must use WeChat avatars');
assert.doesNotMatch(server, /\/api\/gemini\/thinking-analysis/, 'the obsolete Gemini endpoint must be removed');
assert.doesNotMatch(
  [app, sidebar, auxModals, thinking, mockData].join('\n'),
  /gemini-3\.1-pro-preview|Gemini Reasoning|模型待联调|尚未接入 ChatOS 本地模型/,
  'no visible UI source may retain the obsolete Gemini or unconfigured-model claims'
);
assert.match(sidebar, /aux-engine-btn/, 'the local-model status entry must remain available in the sidebar');

console.log('UI baseline preserved; Gary tokens and ShiBao light rays are wired.');
