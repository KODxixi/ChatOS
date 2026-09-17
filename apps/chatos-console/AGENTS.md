# AGENTS.md — chatos-console（前端）

ChatOS 的 Web 控制台。技术栈：**Vite 8 + React 19 + TypeScript 5.9（strict）**，无运行时依赖之外的框架
（无 React Router、无全局状态库、无 CSS-in-JS）。

## 架构分层

```
src/
├── main.tsx              # 入口，挂 #root
├── App.tsx               # 根组件：tab 切换 + 两栏会话布局 + Overlay
├── index.css             # 全局入口，引入 styles/
├── api/index.ts          # ★唯一 API 客户端，所有 fetch 走这里
├── components/           # 视图/通用组件
│   ├── TopBar.tsx            # 顶栏：搜索框、审计/图谱入口
│   ├── SegmentedControl.tsx  # tab 切换（conversations/tasks/projects）
│   ├── ConversationList.tsx  # 左侧会话列表
│   ├── ConversationDetail.tsx # 右侧会话详情（摘要/媒体/链接）
│   ├── EmptyState.tsx
│   ├── AuditOverlay.tsx      # 审计弹层
│   └── GraphOverlay.tsx     # 知识图谱弹层
├── pages/
│   ├── TasksPage.tsx         # 待办整页
│   └── ProjectsPage.tsx      # 项目整页
├── styles/
│   ├── gary-base.css         # ★CSS 变量/token（--bg/--panel/--ink/...）
│   ├── gary-components.css   # 组件类（.button/.detail-col/...）
│   └── demo.css
├── types/index.ts        # 全部 TS 类型
├── utils/
│   ├── format.ts             # 时间/字节格式化
│   ├── ui.ts
│   └── denoise.ts
└── vendor/gary/          # Gary-UI 母源 vendored：material.js / glass-surface.js /
                          # optical-glass.js / gradient-waves.js / dot-grid.js /
                          # icons.js / chart-recipes.js
```

## 状态管理

**没有 Redux/Zustand/Context 全局 store。** 所有状态就是组件内 `useState`/`useEffect`：

- `App.tsx` 持有 `tab`、`query`、`previews`、`selectedId`、`todayTodo`、`auditOpen`、`graphOpen`。
- 子组件通过 props 接收数据和回调（`onSelect`、`onOpenConversation`、`onJumpConversation`、`onClose`）。
- 数据请求集中在 `App.load()` 与各页面内的 `useEffect`；请求失败只设 `loadError` 并展示重试按钮。
- 新全局状态优先考虑「是否真需要跨组件共享」——能下沉就下沉，能 props 就 props。

## API 客户端约定

所有请求走 `src/api/index.ts`，`API_BASE='/api'`，统一封装 `fetchJson<T>()`：

```ts
export function getConversationSummary(id: string): Promise<ConversationSummary> {
  return fetchJson<ConversationSummary>(`/conversations/${id}/summary`)
}
```

- 新增端点：先在 `types/index.ts` 加类型，再在 `index.ts` 加一个导出函数，**不要**在组件里直接 `fetch`。
- 列表参数统一 `limit` + 游标/筛选；错误一律抛 `Error('API <status>: ...')`，调用方 `.catch` 处理。

## 样式规范

- **零硬编码色值**：所有颜色引用 `styles/gary-base.css` 的 CSS 变量：
  `--bg` `--panel` `--raised` `--ink` `--muted` `--line` `--accent`。
- 三种材质：页面用 `--bg`，卡片/列表用 `--panel`，Overlay 用 `--raised`。
- 优先复用 `gary-components.css` 里的类（`.button`、`.button--primary`、`.detail-col`、`.detail-empty`、`.app-body`）。
- 行内 style 只用于布局（flex/尺寸），颜色一律走变量。
- Gary-UI 母源在 `C:\AI\UI\gary-ui`，本目录 `vendor/gary/` 是 vendored 副本，**不改母源**。

## 组件开发规范

- **props 必须显式类型**：函数签名里写 `{ query, onQuery }: { query: string; onQuery: (v: string) => void }`。
- **事件命名**：回调 props 用 `on` 前缀（`onSelect`、`onClose`、`onOpenAudit`）。
- **样式隔离**：页面级布局写进对应组件文件或 `gary-components.css`，不为单组件新建散 CSS 文件。
- 弹层（Overlay）由 `App.tsx` 条件渲染（`{auditOpen && <AuditOverlay/>}`），Escape 关闭。

## 构建与部署

```powershell
cd C:\ChatOS\apps\chatos-console
npm install
npm run typecheck   # tsc -p tsconfig.app.json，必须 0 error
npm run build       # typecheck + vite build → dist/
```

- `dist/` 由后端 `plugins/frontend.py` 在 `http://127.0.0.1:8912/` 静态托管。
- **前端源码改动后必须重跑 `npm run build`**，否则刷新看不到变化；纯前端调试可另开 `npm run dev`。

## 测试

无单测框架。轻量校验：

```powershell
# 类型检查（build 的一部分）
npx tsc -p tsconfig.app.json

# 轻量脚本式测试用 node 直接跑（支持 strip types）
node --experimental-strip-types tests/
```

改动后至少保证 `npm run build` 通过。
