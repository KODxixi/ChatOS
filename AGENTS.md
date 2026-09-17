# AGENTS.md

> 本文件给在此仓库工作的 AI agent / 贡献者定位用。人类读者先看 [README.md](README.md)。
> 读完本文件再动手；任何修改前先确认「硬约定 / 禁忌」一节。

## 这是什么

**ChatOS = 微信会话第二大脑。** 从微信 PC 端解密后的对话中自动提取摘要、待办、项目，
在本地构建一个可检索、可图谱化的个人知识库。一句话：

> **ChatOS 是跑在 127.0.0.1:8912 上的本地 Web 应用，把只读的微信原始 SQLite 变成一个带 LLM 摘要、
> 待办、项目、知识图谱的第二大脑。**

- 前端：Vite + React 19 + TypeScript，`apps/chatos-console/`
- 后端：Python ≥3.13 + FastAPI + uvicorn（Cordis 风格 plugin kernel），`apps/gateway/`
- 数据库：SQLite，微信原始数据只读，派生表全部新建
- LLM：**本地 LM Studio 优先**（`http://localhost:1234/v1`，embedding + chat + reranker 全本地），外部 API（APIYI）仅作降级 fallback；异步摘要 + 待办过滤 + `llm_cache` 数据库缓存
- 设计系统：Gary-UI Liquid Glass，CSS 变量驱动，零硬编码色值

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser  http://127.0.0.1:8912/                                    │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ chatos-console  (React SPA, Vite build → dist/)              │  │
│  │  TopBar / SegmentedControl / ConversationList / Detail        │  │
│  │  TasksPage / ProjectsPage / AuditOverlay / GraphOverlay      │  │
│  └──────────────────────────────┬────────────────────────────────┘  │
└─────────────────────────────────┼───────────────────────────────────┘
                                  │ fetch /api/* (同源)
┌─────────────────────────────────┼───────────────────────────────────┐
│  FastAPI  app.py create_app()   ▼                                   │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ plugins/kernel.py  +  plugins/composition.py                  │  │
│  │  声明式 plugin 行：core / credentials / jobs / gchat /       │  │
│  │  media / projection / semantic / query / answer / ...          │  │
│  └───────┬───────────────────────┬───────────────────┬────────────┘  │
│          │                       │                   │               │
│  api/*.py routers        services/*.py      llm_service.py         │
│  (HTTP 层，参数/错误)    (业务逻辑，事务)   (非流式 LLM + DB 缓存)  │
└──────────┼───────────────────────┼───────────────────┼──────────────┘
           │                       │                   │
           ▼                       ▼                   ▼
   ┌──────────────────────────────────────────────────────────┐
   │ SQLite  D:\ChatOS-Vault\library\wechat-ai.sqlite (338MB) │
   │  · 微信原始表 conversations/messages/message_parts/…   │
   │    → 只读，绝不 INSERT/UPDATE/DELETE                    │
   │  · 派生表 tasks / llm_cache / projects / graph_…        │
   │    → 由 migrations/001-016.sql 建立，可写             │
   │  PRAGMA: journal_mode=WAL, foreign_keys=ON, busy_timeout=30s │
   └──────────────────────────────────────────────────────────┘
```

**请求路径约定**：所有 HTTP 端点都挂在 `/api` 前缀下（前端 `src/api/index.ts` 的 `API_BASE='/api'`）。
`/` 由 `plugins/frontend.py` 静态托管 `webDist`（即 `apps/chatos-console/dist/`）。

## 目录结构

### 仓库根

| 路径 | 职责 |
|---|---|
| `apps/chatos-console/` | 前端 SPA（详见该目录下的 `AGENTS.md`） |
| `apps/gateway/` | 后端网关（详见该目录下的 `AGENTS.md`） |
| `config/` | 运行配置 `settings.json`（gitignore 真实值，只入模板） |
| `classification/` | 会话分类体系与规则（`taxonomy.md`） |
| `bridges/archos/` | 与 ArchOS 建筑设计系统的数据契约 |
| `scripts/` | 启动/运维脚本，如 `start_chatos.bat` |
| `tests/` | 仓库级测试；前后端各自有自己的 tests |
| `key/` | 部署私钥，**绝不入库** |

### 后端 `apps/gateway/src/wechat_ai/`

| 路径 | 职责 |
|---|---|
| `app.py` | `create_app()`，只做 bootstrap；真正的组装在 plugin kernel |
| `cli.py` | typer CLI：`serve --port --no-browser` |
| `config.py` | `Settings` dataclass + `load_settings()`，读 `config/settings.json` |
| `paths.py` | 项目根 / 相对路径解析 |
| `database.py` | 连接工厂、PRAGMA、**启动自动跑 migrations**、`app_meta.schema_version` |
| `openai_compatible.py` | OpenAI 兼容客户端异常 `OpenAICompatibleUnavailable` |
| `plugins/` | Cordis 风格 kernel：`kernel.py`（拓扑排序+应用 plugin）、`composition.py`（plugin 行清单）、各业务 plugin（core/query/gchat/media/answer/frontend…） |
| `api/` | HTTP 路由层，一个文件一组资源（见下文 API 一览） |
| `services/` | 业务逻辑层：`llm_service`、`message_cleaning`、`conversation_service`、`tasks` 相关、`graph_service`、`search_service`、`knowledge_archiver` 等 |
| `migrations/` | 编号 SQL：`001_initial.sql` … `016_llm_cache.sql`，启动时按序号执行 |

### 前端 `apps/chatos-console/src/`

| 路径 | 职责 |
|---|---|
| `api/index.ts` | 唯一 API 客户端，`fetchJson<T>()` 封装，全部端点在此 |
| `components/` | 通用/视图组件：TopBar、SegmentedControl、ConversationList、ConversationDetail、EmptyState、AuditOverlay、GraphOverlay |
| `pages/` | 整页：TasksPage、ProjectsPage |
| `styles/` | `gary-base.css`（token/CSS 变量）、`gary-components.css`（组件类）、`demo.css` |
| `types/index.ts` | 全部 TS 类型（Conversation、Task、Project、GraphData…） |
| `utils/` | `format.ts`（时间/字节）、`ui.ts`、`denoise.ts` |
| `vendor/gary/` | Gary-UI 母源 vendored：`material.js`、`glass-surface.js`、`optical-glass.js`、`gradient-waves.js`、`dot-grid.js`、`icons.js`、`chart-recipes.js` |

## 核心数据模型

数据库：`D:\ChatOS-Vault\library\wechat-ai.sqlite`（由 `config/settings.json` 的 `libraryDb` 指定）。
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

**`projects`**（由 `services/project_extractor.py` + gchat 派生）：从 project 类会话聚类，
含 `project_id`、`project_name`、消息数、决策/里程碑/甲方反馈计数。

关系：`conversations 1—N messages`；`messages 1—N message_parts/assets`；
`tasks N—1 conversations`；`llm_cache` 通过 `task_type+reference_id` 回指任意实体。

## API 一览

前端只通过 `src/api/index.ts` 访问，同源 `/api`。关键端点：

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
| GET | `/api/search?q=&limit=` | 全文搜索消息 |
| GET | `/api/assets/{id}` | 取本地附件文件流 |
| POST | `/api/knowledge/archive` | 归档知识（文件+链接落盘） |
| POST | `/api/ask/stream` | 两阶段 RAG 流式问答（高级） |

错误响应统一由 `api/_errors.py` 构造；未捕获异常由全局 handler 包成 JSON。

## 开发流程

### 启动（生产/日常用）

```powershell
# 一键（scripts\start_chatos.bat 会复用已监听 8912 的实例）
cd C:\ChatOS
scripts\start_chatos.bat

# 或直接
cd C:\ChatOS\apps\gateway
.venv\Scripts\chatbox.exe serve --port 8912 --no-browser
```

CLI 默认端口 8765；`serve` 启动前会探测 `/api/runtime`，发现同端口已有兼容实例就直接复用、不重复起服务。

### 前端开发 / 构建

```powershell
cd C:\ChatOS\apps\chatos-console
npm install
npm run dev        # Vite dev server（独立调试用）
npm run typecheck  # tsc -p tsconfig.app.json
npm run build      # = typecheck + vite build → dist/
```

**重要**：后端在 `/` 静态托管 `dist/`。改完前端必须 `npm run build`，再重启/刷新后端才生效；
只有纯前端调试才开 `npm run dev`。

### 后端测试

```powershell
cd C:\ChatOS\apps\gateway
.venv\Scripts\python -m pytest -q        # 后端 pytest
# 前端无单测框架，用 node 直接跑：
node --experimental-strip-types tests/   # apps/chatos-console/tests/
```

## 代码规范

- **前端**：TypeScript `strict`（`tsconfig.app.json`）；组件 props 必须显式类型；事件 props 以 `onXxx` 命名；
  不引入全局状态库，状态用 `useState`/`useReducer` 就近管理。
- **后端**：PEP 8 + 全量 type hints；新增端点走 `api/` 新 router 文件并在 plugin 中注册；
  错误一律用 `_errors.py` 的 helper，不要裸 `raise HTTPException`。
- **样式**：CSS 变量优先，零硬编码色值（见下节）。

## 设计系统：Gary-UI Liquid Glass

所有颜色/材质走 `src/styles/gary-base.css` 的 CSS 变量，组件只引用 token：

| 变量 | 含义 |
|---|---|
| `--bg` | 页面底色（深） |
| `--panel` | 面板/卡片底 |
| `--raised` | 浮起层（overlay） |
| `--ink` | 主文字 |
| `--muted` | 次要文字 |
| `--line` | 分隔线 |
| `--accent` | 强调色 |

三种材质分工（`vendor/gary/material.js` + `glass-surface.js` + `optical-glass.js`）：
**base**（页面）/ **panel**（列表、详情卡片）/ **raised**（Overlay：审计、图谱）。
新增组件时先复用 `.button`、`.detail-col` 等已有类，不要新写色值。

## LLM 集成（本地模型优先，零外部额度）

**核心原则：所有 LLM / embedding / reranker 调用必须优先使用本地 LM Studio，数据不出本地，零外部 API 额度消耗。**

### 本地模型栈

| 用途 | 模型名 | 端点 | 说明 |
|---|---|---|---|
| **Embedding（向量索引，主）** | `text-embedding-qwen3-embedding-8b`（4096维） | llama-swap `http://localhost:9123/v1` | Vulkan 全层 GPU（-ngl 99），RTX 5090 |
| **Embedding（本地降级）** | `text-embedding-nomic-embed-text-v1.5`（768维） | LM Studio `http://localhost:1234/v1` | llama-swap 不可用时降级 |
| **Chat / RAG 问答 / 摘要** | `qwen/qwen3-vl-8b` | LM Studio `http://localhost:1234/v1` | CUDA，视觉理解+对话 |
| **Reranker（搜索重排序）** | `text-reranker-8b` | llama-swap `http://localhost:9123/v1` | 同端点，混合检索精排 |

**关键区分**：embedding/reranker 走 **llama-swap 9123**（Vulkan，文本模型常驻 GPU）；
chat/VLM 走 **LM Studio 1234**（CUDA，按需加载）。两个服务独立运行，互不抢占显存。

### 配置

- llama-swap 端点：`LOCAL_EMBEDDING_BASE_URL=http://localhost:9123/v1`（默认）
- llama-swap embedding 模型：`LOCAL_EMBEDDING_MODEL=text-embedding-qwen3-embedding-8b`
- LM Studio 降级端点：`LOCAL_EMBEDDING_FALLBACK_URL=http://localhost:1234/v1`
- LM Studio 降级模型：`LOCAL_EMBEDDING_FALLBACK_MODEL=text-embedding-nomic-embed-text-v1.5`
- 本地 chat 模型：`LOCAL_LLM_MODEL=qwen/qwen3-vl-8b`（LM Studio）
- 不需要 API key（本地服务无鉴权）
- llama-swap 模型 TTL 300s 自动卸载（embedding 常驻），空闲时释放显存
- **llama-swap 和 LM Studio 必须保持运行**；llama-swap 不可用时自动降级到 LM Studio，再不可用降级到外部 API

### 降级策略（仅本地不可用时）

降级链：**llama-swap 9123 → LM Studio 1234 → 外部 APIYI**
- 第一级：llama-swap `text-embedding-qwen3-embedding-8b`（4096维，主）
- 第二级：LM Studio `text-embedding-nomic-embed-text-v1.5`（768维，本地降级）
- 第三级：APIYI 中继 `https://api.apiyi.com/v1`，模型 `text-embedding-3-small`
- 密钥：环境变量 `APIYI_API_KEY`（或 `CC_APIYI_KEY`）
- **降级是最后的手段**：优先确保 llama-swap 运行，其次 LM Studio，最后才外部
- 降级到不同维度模型时，搜索结果可能不准确（向量空间不兼容），应尽快恢复主模型
- 启动时检查本地端点可用性，日志中明确标注当前使用的是哪个端点

### 缓存与异步

- 超时：本地 10s，外部 20s
- **缓存策略**：`get_cached()`/`put_cached()` 以 `{task_type}:{ref_id}:{sha256}` 为 key 读写 `llm_cache`；
  命中即返回，未命中才调模型；读时顺手 GC 过期行。
- **异步/降级**：HTTP 处理函数先返回规则式结果，LLM 增强在后台任务里做；
  任何网络/解析失败抛 `OpenAICompatibleUnavailable`，调用方静默回退规则结果。
  **绝不让 LLM 调用阻塞用户请求。**

### 向量索引

- 向量存储：`message_embeddings` 表（migration `017_message_embeddings.sql`），BLOB 存储 float32 向量
- **主模型**：`text-embedding-qwen3-embedding-8b`（4096 维），llama-swap 9123，Vulkan 全层 GPU
- 只索引有文字内容的消息（跳过纯表情/图片/语音/文件）
- **批量 128 条/请求**（`embedding_service.index_conversation`），GPU 加速，约 2500 条/分钟
- 增量更新：新消息导入时自动触发 embedding
- **embedding 模型切换时必须清除旧向量重新索引**（维度不同不能混用，如 768↔4096）

#### 索引过滤规则（硬约束，不可跳过）

向量索引**必须**应用以下过滤，否则大群和噪音群会拖垮索引速度并污染语义搜索结果：

| 规则 | 条件 | 原因 |
|---|---|---|
| **跳过 30 人以上大群** | 群聊唯一发送者数 > `GROUP_SIZE_THRESHOLD`（默认 30） | 大群（业主群/家庭群/行业群）占 80%+ 消息量但语义价值低，是索引速度瓶颈 |
| **跳过噪音群** | 群标题匹配 `NOISE_GROUP_KEYWORDS`（家人/闲聊/营销/广告/销售/客户/用户群/交流群/福利/优惠/团购/秒杀/业主群/同学群/老乡/亲友团/红包/抽奖/外卖/淘宝/拼多多 等） | 废话群/营销群/销售群的消息是语义噪音，污染搜索结果 |
| **私聊不跳过** | `kind='direct'` | 1对1对话通常有价值 |
| **system 不索引** | `kind='system'` | 系统通知/公众号推送 |

过滤实现：`services/embedding_service.py` 的 `_get_indexable_conversations()` 函数，
在 `index_all_conversations()` 和 `index_top_conversations()` 中统一调用。
**新增索引入口必须经过此过滤，不得绕过。**

过滤效果参考：139 个群聊中跳过 18 个（9 大群 + 9 噪音群），
索引量从 84,531 条降至 ~48,000 条（减 43%），索引时间从 ~35 分钟降至 ~20 分钟。

#### 触发索引

- GET `/api/vector/index/start` — 触发 top 20 会话索引（无需 body，避免 422）
- GET `/api/vector/index/start?full=true` — 触发全量索引（应用过滤规则）
- GET `/api/vector/index/start?conversation_id=<id>` — 索引单个会话
- GET `/api/vector/status` — 索引进度（indexed/total/running/progress/current conversation）
- POST `/api/vector/index` — 备用（body 可选，`conversation_id` / `full`）

## 常见问题排查

| 症状 | 排查 |
|---|---|
| 服务启动失败 / 端口被占 | `serve` 会探测 `/api/runtime`；旧版本残留 → 先结束旧 `chatbox.exe`。日志看根目录 `_srv_err.log` / `_srv_out.log` |
| LLM 401 / 摘要没生效 | `APIYI_API_KEY` 未设或 key 失效；`llm_service.is_available()` 应为 True；此时摘要自动回退规则式，功能不挂 |
| 前端白屏 | 前端改完没跑 `npm run build`；或 `config/settings.json` 的 `webDist` 指向了不存在的 dist |
| 数据库锁定 / `database is locked` | WAL + 30s busy_timeout 已缓解；仍报锁通常是外部进程独占打开了 sqlite，关闭后重启 |
| 媒体打不开 | `apps/gateway/config.json` 的 `db_dir` 没配，媒体根解析不到；`/api/assets/{id}` 依赖它 |
| 迁移没跑 | `app_meta.schema_version` 对不上 migrations 最大序号；删库前先备份（`data/backups/`） |

## 禁忌（红线）

1. **不修改 `wechat-ai.sqlite` 的微信原始表**（conversations/messages/message_parts/assets 及其它导入表）——只读查询。新需求一律新建派生表 + 新 migration。
2. **不同步阻塞 LLM**：任何请求路径里的 LLM 调用必须走缓存 + 后台任务 + 降级，不得 `await`/同步等待远程响应。
3. **不删 `llm_cache`**：它是省钱省延迟的缓存层；要清缓存按 `task_type`/`expires_at` 精删，不整表 DROP。
4. 不把 `key/`、`.env*`、真实 key 提交进 git。
5. 不反向修改 Gary-UI 母源 `C:\AI\UI\gary-ui`；本仓库 `vendor/gary/` 是 vendored 副本。
6. 不跨仓库硬编码读写 `C:\GarchOS`；走 `bridges/archos/` 契约。
7. **不优先调用外部 LLM/embedding API**：所有模型调用（摘要、待办过滤、RAG 问答、向量索引、reranker）必须优先使用本地 LM Studio（`http://localhost:1234/v1`）。外部 API（APIYI 等）仅在本地服务不可用时作为降级 fallback，且必须在日志中明确标注。**目标：零外部模型额度消耗，数据不出本地。**
