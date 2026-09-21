# AGENTS.md

<!-- repository-verify: scripts/verify-entry.mjs -->

ChatOS 将微信只读原始数据变成可检索的本地第二大脑。贡献前先看本文件禁忌与当前组件 AGENTS。

> **一句话定义**：ChatOS = 微信聊天的本地语义搜索层。文本向量在 SQLite，文件向量在 LanceDB，模型全本地。

## 用户场景

ChatOS 帮你做三件事：

| 场景 | 怎么用 | API |
|---|---|---|
| **搜你三个月前和某人的微信对话** | 输入关键词或自然语言，语义搜索相关消息 | `GET /api/search/semantic?q=&limit=` |
| **帮我总结这个礼拜的会议** | 问一个问题，RAG 基于聊天记录回答 | `POST /api/rag/ask` |
| **从聊天里提取待办事项** | 自动扫描消息，提取未来动作/承诺/请求 | `POST /api/tasks/extract` |

核心价值：**不用翻几百条聊天记录，直接用自然语言问，答案来自你的真实微信对话。**

## 快速开始（外部 agent 必读）

```powershell
# 1. 杀旧进程（避免 database locked）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "wechat_ai" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. 启动开发实例（端口 8765）
cd C:\ChatOS\apps\gateway
.venv\Scripts\python.exe -m wechat_ai serve --port 8765 --no-browser

# 3. 验证
Invoke-RestMethod http://127.0.0.1:8765/api/runtime
# 返回 pid + appVersion 即正常
```

- **正式端口**：8912（走 `scripts/start_chatos.bat`，含 UI 3108）
- **开发端口**：8765（只起 gateway，不起 UI）
- **数据库**：`D:\ChatOS-Vault\library\wechat-ai.sqlite`（只读原始表 + 可写派生表）

## 状态查询（外部 agent 必读）

**ChatOS 向量索引已经建好，不要去 openclaw/Gavis_Core/vault_search_mcp 找。**

| 查什么 | 在哪 | 命令 |
|---|---|---|
| 文本向量有多少条 | SQLite `message_embeddings` 表 | `SELECT COUNT(*) FROM message_embeddings WHERE status='indexed'` → 应返回 ~25277 |
| 文件向量有多少 chunks | LanceDB `kb_chatos` 表 | lancedb 连 `D:\ChatOS-Vault\library\knowledge\lancedb`，打开表 `kb_chatos` → 应返回 ~15142 |
| 服务在跑吗 | HTTP | `Invoke-RestMethod http://127.0.0.1:8765/api/runtime` |
| 索引进度 | HTTP | `GET /api/vector/status` |

**不要找的地方：**
- ❌ `C:\AI\repos\Gavis_Core\index\lancedb\` — 这是 Gavis 项目的，不是 ChatOS
- ❌ openclaw transcript FTS — 这是 openclaw 的关键词搜索，不是 ChatOS 的向量索引
- ❌ vault_search_mcp — 未部署的桥接，ChatOS 不需要它


## 规则真相源指针（agent-md）

| 功能 | 真相源 | 说明 |
|---|---|---|
| 启动/运维 | 本文件 + C:\AI\memory\_canonical\TOOLS.md | 启动前杀旧进程、端口/锁管理 |
| 向量索引过滤 | apps/gateway/src/wechat_ai/services/embedding_service.py | 大群/噪声群过滤，配置在 config/settings.json indexing 块 |
| 向量索引吞吐/调优 | rules/vector-index.md「性能真相」 | 批量甜点 128、CUDA 后端稳态 ~140/s（2026-09-21 由 Vulkan ~17/s 切换）、并发无收益、WAL 已开；勿从 SQLite/WAL/批量提交理论优化 |
| 文本清洗 | apps/gateway/src/wechat_ai/services/message_cleaning.py | filler 词表、打分规则 |
| LLM/embedding 端点 | 本文件「LLM 集成」段 + local-model skill | llama-swap 9123 / LM Studio 降级链 |
| 数据库 schema | apps/gateway/src/wechat_ai/migrations/ | 原始表只读，派生表可写 |
| RAG 问答 prompt | `apps/gateway/src/wechat_ai/system/rag-system.md` | 微信聊天 RAG 系统 prompt，改这个不改代码 |
| 对话摘要 prompt | `apps/gateway/src/wechat_ai/system/summarize-system.md` | LLM 对话摘要 system prompt |
| 待办过滤 prompt | `apps/gateway/src/wechat_ai/system/todo-filter-system.md` | LLM 待办事项过滤 system prompt |
| 语义提取 prompt | `apps/gateway/src/wechat_ai/system/semantic-extract-system.md` + `apps/gateway/src/wechat_ai/system/semantic-extract-task.md` | 知识图谱实体提取 prompt |
## 唯一维护的源码与入口

- 正式 UI：`apps/chatos-console`，来自原型 commit `e9d78e9c3f1c216c668a4810fe890d764131b88a`；Vite + React + Express，`http://127.0.0.1:3108/`。
- 正式网关：`apps/gateway`，FastAPI，8912 保留全部 API；网页入口按本地配置跳转正式 UI。
- 统一启动：`scripts/start_chatos.bat` → Node 标准库 `scripts/start_chatos.mjs`。无需 PowerShell 执行策略修改。
- 唯一运行配置：`apps/gateway/config/settings.json`；模板同目录 `apps/gateway/config/settings.example.json`。`libraryDb` 同时供 UI 只读适配与网关使用，`frontendUrl` 指向正式 UI。
- 历史原型及旧前端展开目录已清理，3000 旧实例已停；不重新创建第二套 UI。历史只从 Git 或 `var/backups/canonical-entry-20260917/` 的既有 zip/bundle 回溯。
- 默认门控：`node scripts/verify-entry.mjs` 检查源码、构建、配置；`--live` 额外核验两项服务的启动身份、跳转与线上 HTML/assets。

## 实际接入范围

会话列表与消息来自真实 SQLite 只读适配；摘要、关系和排序含原型启发式。待办读取/状态保存与增量整理通过 `/api/gateway/*` 接入网关；项目、资料库、图谱和协作仍含演示数据/内存状态，身份未绑定时明确显示未绑定。不能以正式入口就绪宣称所有功能生产可用。Thinking Mode 仅请求本地 LM Studio，配置存在不等于模型已加载。

## 目录与分工

| 路径 | 职责 |
|---|---|
| `apps/chatos-console/server.ts` | Express 只读 SQLite 适配、演示协作和 UI 静态服务 |
| `apps/chatos-console/src/` | 原型提升后的唯一 React 组件树、context、core、vendor |
| `apps/chatos-console/scripts/build.mjs` | UI/服务端构建与内容身份生成 |
| `apps/gateway/src/wechat_ai/` | 网关 api/services/plugins/migrations，保留既有后端修复 |
| `scripts/entry-contract.mjs` | 源码/输出摘要、端口与运行身份契约 |
| `classification/`、`bridges/archos/` | 分类规则、跨项目契约 |
| `var/` | 忽略的备份、日志、运行证据；不是源码 |

## 架构总览

```
用户浏览器 (3108)  <--->  Express server
                          v 只读 SQLite 适配
FastAPI gateway (8912/8765)
  +-- plugins/      拓扑排序，组装 service + router
  +-- api/          HTTP 路由层（参数解析、响应组装）
  +-- services/     业务逻辑（事务、LLM、清洗、索引）
  +-- migrations/   SQL schema，启动自动执行

数据层：
  SQLite (wechat-ai.sqlite)
    +-- conversations / messages / message_parts / assets  [原始表只读]
    +-- message_embeddings (BLOB 4096维)                  [文本向量]
    +-- tasks / llm_cache / projects                      [派生表可写]
  LanceDB (D:\ChatOS-Vault\library\knowledge\lancedb)
    +-- kb_chatos (15,142 chunks)                       [文件类向量]

模型层：
  llama-swap 9123  <- embedding/rerank (CUDA GPU, 2026-09-21 由 Vulkan 切换)
  Muse Glimmer 8080 <- chat/VLM (CUDA, DFlash, 常驻; LM Studio 1234 仅降级)
```

### 已知技术债（2026-09-20 记录）

| 项 | 位置 | 说明 |
|---|---|---|
| 大文件 | services/agent_service.py (1822行) | 需拆分，非本次范围 |
| 大文件 | services/gchat_service.py (1554行) | 需拆分，非本次范围 |
| ~~硬编码 prompt~~ | ~~rag_prompt.py, api/vector.py, llm_service.py 等~~ | **已解决**（2026-09-20 提取到 system/*.md） |
| 模板复制 | 多个 service 均 ~470行 | 脚手架复制，后续去重 |

## 核心数据模型

数据库：`D:\ChatOS-Vault\library\wechat-ai.sqlite`（由 `apps/gateway/config/settings.json` 的 `libraryDb` 指定）。
WAL 模式，`busy_timeout=30000`。**原始表只读，派生表可写。**

### 原始表（微信解密导入，只读）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `conversations` | `id`(TEXT PK), `title`, `kind`(group/direct/system) | 会话；`kind` 决定展示与索引策略 |
| `messages` | `conversation_id`, `sender_name`, `sender_id`, `sent_at`(INT ms), `type`(text/image/…), `content` | 消息正文；摘要只取 `type='text'` 且 `content` 非空 |
| `message_parts` | 与 `messages` 关联 | 富媒体分段（引用、卡片、链接等） |
| `assets` | 附件元数据 | 图片/文件/语音；本地文件经 `/api/assets/{id}` 提供 |

### 派生表（v0.3.0 新增，可写）

**`tasks`**（`migrations/015_tasks.sql`）—— 从消息中提取的待办：

```sql
task_id          TEXT PRIMARY KEY
conversation_id  TEXT NOT NULL
conversation_title TEXT
content          TEXT NOT NULL
sender_name      TEXT
sent_at          INTEGER NOT NULL
status           TEXT NOT NULL DEFAULT 'todo'     -- todo / done / ...
priority         TEXT NOT NULL DEFAULT 'medium'   -- low / medium / high
due_date         TEXT
created_at       INTEGER NOT NULL
updated_at       INTEGER NOT NULL
-- 索引: status, sent_at, conversation_id
```

**`llm_cache`**（`migrations/016_llm_cache.sql`）—— LLM 结果缓存：

```sql
id          INTEGER PK AUTOINCREMENT
cache_key   TEXT UNIQUE NOT NULL   -- {task_type}:{ref_id}:{sha256(task_type\0ref_id\0content)}
task_type   TEXT NOT NULL          -- summary / todo_filter / topic / ...
reference_id TEXT                  -- 通常是 conversation_id
result      TEXT NOT NULL          -- LLM 原始输出
created_at  INTEGER NOT NULL
expires_at  INTEGER                -- 可空；非空且过期会被 GC 删除
```

**`projects`**（由 `apps/gateway/src/wechat_ai/services/project_extractor.py` + gchat 派生）：从 project 类会话聚类，
含 `project_id`、`project_name`、消息数、决策/里程碑/甲方反馈计数。

关系：`conversations 1—N messages`；`messages 1—N message_parts/assets`；
`tasks N—1 conversations`；`llm_cache` 通过 `task_type+reference_id` 回指任意实体。

## API 一览

以下是 8912 网关 API；3108 的 Express 同源 API 保留现有只读适配与演示协作，两者并未全量桥接。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查，`app=='ready'` 才算就绪 |
| GET | `/api/runtime` | 运行时契约/版本，防重复启动旧版本 |
| GET | `/api/conversations?limit&cursor&kind` | 会话分页 |
| GET | `/api/conversations/preview?limit&onlyActive&domain` | 左侧列表预览（默认 onlyActive=true） |
| GET | `/api/conversations/{id}/summary` | 规则式摘要 + 关键信息（LLM 异步增强见下） |
| GET | `/api/conversations/{id}/media?kind=` | 媒体分页（图片/文件/语音） |
| GET | `/api/conversations/{id}/links` | 会话内链接 |
| POST | `/api/conversations/evaluate-activity` | 按 30 天阈值标记 inactive |
| POST | `/api/conversations/classify` | 按 domain 分类会话 |
| POST | `/api/conversations/extract-projects` | 从会话抽取项目 |
| GET | `/api/tasks?status&limit` | 待办列表 |
| POST | `/api/tasks/extract` | 触发待办提取（规则 + LLM 过滤） |
| PATCH | `/api/tasks/{id}` | 更新 `status` / `priority` |
| GET | `/api/audit/stats` | 审计统计（媒体/链接/任务覆盖率） |
| GET | `/api/graph?limit&hideNoise` | 知识图谱 nodes/edges |
| GET | `/api/projects?minMessages=` | 项目列表 |
| GET | `/api/search?q=&limit=` | 全文搜索消息（关键词） |
| GET | `/api/search/semantic?q=&conversation_id=&limit=&threshold=` | **语义搜索**（向量相似度，核心功能） |
| GET | `/api/vector/status` | 向量索引进度 |
| GET | `/api/vector/index/start?full=true` | 触发向量索引 |
| POST | `/api/rag/ask` | RAG 问答（语义搜索 + LLM 生成答案） |
| GET | `/api/assets/{id}` | 取本地附件文件流 |
| POST | `/api/knowledge/archive` | 归档知识（文件+链接落盘） |
| POST | `/api/ask/stream` | 两阶段 RAG 流式问答（高级） |

错误响应统一由 `apps/gateway/src/wechat_ai/api/_errors.py` 构造；未捕获异常由全局 handler 包成 JSON。

## 开发流程

### 正式入口与验证

```powershell
cd C:\ChatOS\apps\chatos-console
npm ci
npm run build
npm test
cd C:\ChatOS
node scripts/verify-entry.mjs
scripts\start_chatos.bat
node scripts/verify-entry.mjs --live
```

启动器先验证源码受 Git 追踪（允许 staged）、内容摘要、构建输出和配置，再复用相同身份的实例或隐藏启动。未知/旧端口占用直接失败，不杀进程。修改源码后重建；运行中的旧进程不会因磁盘构建被替换而冒充新版本。网关改动也需要核对启动身份。

开发用 `npm run dev` 默认 3109；其身份不能通过正式门控。原型目录与 3000 记录为历史，不用于正式启动。后端单独开发仍可用 `apps/gateway/.venv/Scripts/python.exe -m wechat_ai serve --port 8912 --no-browser`，它不启动 UI，正式流程统一走根启动器。

Node >=22.13（node:sqlite）、Python >=3.13；日志 `var/logs/`，构建 `apps/chatos-console/dist/`，备份 `var/backups/`，均不入 Git。数据由 `apps/gateway/config/settings.json` 的 `libraryDb` 指向外部数据根。详见 [启动与恢复](docs/canonical-entry.md)。

## 代码与设计规范

保留 e9d78e9 原型现有 React/Express 与 Gary-UI 视觉；不要恢复旧组件树、迁整套适配到 Python、或顺手重设计。新增样式复用现有 token，禁止反向修改 Gary-UI 母源。
前端状态在 `src/context/ChatOSContext.tsx`，HTTP 适配在 `apps/chatos-console/server.ts`；会话读取 SQLite 为只读，其余演示状态在浏览器/进程内存中。
后端继续按 `api/`、`services/`、`plugins/` 分层；错误走已有 helper，保持本地会话保护。
新非平凡逻辑先 RED 再 GREEN；HTTP/资源验证不能替代浏览器视觉验收。

## LLM 集成
- 真相源：`rules/llm-endpoints.md`（本地模型栈、降级链、缓存异步、prompt 真相源）
- 原则：本地模型优先，零外部 API 额度。embedding/rerank 走 llama-swap 9123（CUDA），chat/VLM 走 8080 独立 CUDA 实例（Muse Glimmer 30B，非 LM Studio）。

## 禁忌（红线）

1. **不修改 `wechat-ai.sqlite` 的微信原始表**（conversations/messages/message_parts/assets 及其它导入表）——只读查询。新需求一律新建派生表 + 新 migration。
2. **不同步阻塞 LLM**：任何请求路径里的 LLM 调用必须走缓存 + 后台任务 + 降级，不得 `await`/同步等待远程响应。
3. **不删 `llm_cache`**：它是省钱省延迟的缓存层；要清缓存按 `task_type`/`expires_at` 精删，不整表 DROP。
4. 不把 `key/`、`.env*`、真实 key 提交进 git。
5. 不反向修改 Gary-UI 母源 `C:\AI\UI\gary-ui`；本仓库 `vendor/gary/` 是 vendored 副本。
6. 不跨仓库硬编码读写 `C:\GarchOS`；走 `bridges/archos/` 契约。
7. **不优先调用外部 LLM/embedding API**：所有模型调用（摘要、待办过滤、RAG 问答、向量索引、reranker）必须优先使用本地 CUDA 实例（chat/VLM 走 8080 Muse Glimmer，embed/rerank 走 llama-swap 9123）。外部 API（APIYI 等）仅在本地服务不可用时作为降级 fallback，且必须在日志中明确标注。**目标：零外部模型额度消耗，数据不出本地。**
