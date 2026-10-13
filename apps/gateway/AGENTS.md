# AGENTS.md — gateway（后端）

向量模型、配置、旧库兼容与迁移：唯一权威为 [本地检索契约](../../rules/local-model.md)。下文历史吞吐只对应旧模型。

ChatOS 的 FastAPI 网关。入口 `wechat_ai/cli.py`（`chatbox serve`），应用组装在 `wechat_ai/app.py`。
运行时是 Cordis 风格的 plugin kernel：每个能力是 `plugins/composition.py` 里的一行 plugin 声明。

## 架构分层

```
wechat_ai/
├── cli.py               # typer CLI：serve --port --no-browser
├── app.py               # create_app()：组装 Kernel + 默认 overrides
├── config.py            # Settings + load_settings()，读 config/settings.json
├── paths.py             # 项目根 / 相对路径解析
├── database.py          # 连接工厂、PRAGMA、启动迁移、schema_version
├── openai_compatible.py # OpenAI 兼容异常 OpenAICompatibleUnavailable
├── plugins/             # ★plugin kernel 与各 plugin
│   ├── kernel.py            # 拓扑排序 + 应用 plugin 行，产出 FastAPI
│   ├── composition.py       # DEFAULT_COMPOSITION：plugin 行清单
│   ├── query.py / gchat.py / media.py / answer.py / frontend.py / ...
├── api/                 # HTTP 路由层（一文件一组资源）
│   ├── conversations.py / tasks.py / audit.py / graph.py / search.py
│   ├── assets.py / knowledge.py / gchat.py / ask.py / health.py ...
│   └── _errors.py          # ★统一错误响应 helper
├── services/            # 业务逻辑层（事务、LLM、清洗、抽取）
│   ├── llm_service.py          # 非流式 LLM + llm_cache
│   ├── message_cleaning.py     # 摘要文本清洗/打分
│   ├── conversation_service.py
│   ├── project_extractor.py
│   ├── graph_service.py / search_service.py / knowledge_archiver.py
│   ├── job_worker.py           # 后台任务队列
│   └── ...
└── migrations/          # 编号 SQL，启动按序自动执行
```

**分层纪律**：`api/` 只做参数解析/响应组装/错误；业务写在 `services/`；
`plugins/` 只负责把 service 实例、router、事件订阅串起来。新增能力 = 新 service + 新 router 文件 + composition 加一行。

## 数据库迁移流程

- SQL 文件放 `migrations/`，文件名即序号：`NNN_snake_case.sql`；当前最高版本以目录中的编号为准。
- `database.py` 在启动时：`PRAGMA journal_mode=WAL; foreign_keys=ON; busy_timeout=30000`，
  然后读 `app_meta` 表里的 `schema_version`，把所有序号 > 当前版本的 SQL 依次执行，最后更新版本号。
- **迁移只增不改**：已提交的 SQL 绝不回改；改 schema 就加新序号文件。
- 升级前自动备份到 `data/backups/`（文件名带 `v{schema_version}` 与时间戳）。
- 派生表（tasks/llm_cache/…）与微信原始表严格分离，**原始表只读**。

## LLM service 使用规范（本地模型优先，零外部额度）

`services/llm_service.py` 是唯一的 LLM 出口。**四条铁律**：

1. **只用本地**：chat/VLM 与 embedding/reranker 统一走本地 llama-swap（端点、模型、维度以
   `C:\AI\memory\_canonical\LOCAL-MODELS.md` 与 `settings.json` 为准，文档不复述数字）。
   **没有外部 API 兜底**（2026-09-21 删除）—— 不可用时降级到规则式结果。
   **目标：零外部模型额度消耗，数据不出本地。**
2. **必须异步/后台化**：HTTP 请求路径里不得同步等待 LLM。先返回规则式结果，
   LLM 增强走 `jobs/job_worker.py` 后台任务。
3. **必须缓存**：调模型前先 `get_cached(connection, task_type, reference_id, content)`；
   拿到结果后 `put_cached(...)`。cache_key = `{task_type}:{ref_id}:{sha256(task_type\0ref_id\0content)}`。
4. **必须降级**：`complete()` 失败抛 `OpenAICompatibleUnavailable`，调用方捕获后静默回退规则结果，
   不把错误暴露给用户。

### 本地模型栈（只写指针，不抄数字）

模型端点、模型名、维度、显存与调度：**唯一权威 `C:\AI\memory\_canonical\LOCAL-MODELS.md`**。
本服务只消费 `settings.json` 与实际环境变量的值；文档不得复述模型数字（2026-09-21 曾因复述漂移，防再犯）。

### 配置（环境变量，本地优先）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` | 见 LOCAL-MODELS.md 与 `settings.json` | llama-swap chat 端点/模型 |
| `LOCAL_EMBEDDING_BASE_URL` / `LOCAL_EMBEDDING_MODEL` / `LOCAL_EMBEDDING_DIMENSIONS` | 见 LOCAL-MODELS.md 与 `settings.json` `indexing` 段 | 模型与维度必须匹配 |

`config/settings.json` 中的 `lmStudio.baseUrl` 与环境变量等价，环境变量优先。
超时：本地 10s，外部 20s；远程调用用标准库 `urllib`，无第三方 SDK 依赖。
**没有降级链**（2026-09-21 全删）：embedding 与 chat 都只有本地 llama-swap；
不可用即明确失败，由调用方降级到规则式结果。

### 向量索引

- `message_embeddings` 表（migration `017`）存储消息向量，BLOB float32
- **模型与维度**：以本地检索契约和运行配置为准。
- embedding 服务独立于 llm_service，但复用相同的本地优先+降级策略
- **模型切换**：从原文 shadow 重建，验收后切换；保留旧库回滚，不直接清除生产向量。
- 批量 128 条/请求（`indexing.batchSize` → `index_conversation`），实测吞吐 ~140 条/秒。批量 >256 会断崖、并发无收益、WAL 早已开启，详见仓库根 `rules/local-model.md`「性能真相」，勿从 SQLite/批量提交理论优化

#### 索引过滤规则（硬约束）

`services/embedding_service.py` 的 `_get_indexable_conversations()` 统一应用：
- **跳过 >30 人群**：`GROUP_SIZE_THRESHOLD=30`，按唯一发送者数判断
- **跳过噪音群**：`NOISE_GROUP_KEYWORDS`（家人/闲聊/营销/销售/客户/用户群/交流群/福利/团购/业主群/同学群/老乡/亲友团/红包/抽奖/外卖/淘宝/拼多多 等）
- 私聊（`direct`）不跳过；system 不索引
- `index_all_conversations()` / `index_top_conversations()` 必须经过此过滤，**不得绕过**

#### 触发端点

- GET `/api/vector/index/start`（top20）/ `?full=true`（全量）/ `?conversation_id=<id>`（单会话）
- GET `/api/vector/status` — 进度查询
- POST `/api/vector/index` — 备用（body 可选）

## API 开发规范

- 新建路由文件放 `api/`，router 在对应 plugin（多为 `plugins/query.py`）里 `include_router`。
- **错误响应用 `_errors.py` 的 helper**（如 `_not_found_response()`），不要裸 `raise HTTPException`。
- 参数用 FastAPI Query/Path 声明校验；返回类型写 `response_model` 或 `dict | JSONResponse`。
- 每个端点开头 `with closing(connection_factory()) as connection:`，用完即关；`row_factory = sqlite3.Row`。
- 耗时动作（extract/archive/classify）走 POST + 后台 job，POST 立即返回 202/摘要计数。

## 配置管理

运行配置为本地文件、不入库（同目录 `settings.example.json` 为可入库模板）：

- `apps/gateway/config/settings.json`（`config.py`）：`libraryDb`、
  `lmStudio.baseUrl`、`llamaSwap.retrievalBaseUrl`、`rerankEnabled`、
  `activityFilterEnabled` / `activityFilterInactiveDays`。
- `apps/gateway/config.json`：`{"db_dir": "<微信 db_storage 路径>"}`，媒体根由此推导。
- LLM 相关走环境变量（见上表），模板见仓库根 `.env.example`。

## 关键服务

| Service | 职责 |
|---|---|
| `llm_service` | 非流式 chat completion + `llm_cache` 读写 + 降级 |
| `message_cleaning` | 摘要文本清洗：去 @、去 URL、去表情/系统邀请、按「动作+时间」打分、剔除纯问句 |
| `conversation_service` | 会话查询、预览分页、活跃度标记 |
| `tasks`（api/tasks.py + services） | 待办提取（规则初筛 + LLM 过滤）、状态/优先级更新 |
| `graph_service` / `semantic_graph_service` | 知识图谱 nodes/edges 与证据 |
| `knowledge_archiver` | 文件/链接归档 |
| `job_worker` | 后台任务队列，承载异步 LLM 增强 |

## 本地运行

```powershell
cd C:\ChatOS\apps\gateway
# 单独开发网关；正式 UI+网关统一使用根 scripts/start_chatos.bat
.venv\Scripts\python.exe -m wechat_ai serve --port 8912 --no-browser
# 正式日志：var/logs/gateway.stdout.log 和 gateway.stderr.log
.venv\Scripts\python -m pytest -q
```
