# ChatOS — 本地 agent 的微信记忆与检索服务

微信会话第二大脑，**消费方是本地 agent**（OpenClaw、Claude Code 等），不是浏览器用户。
常驻网关 `http://127.0.0.1:8912` 提供 `/api/*`；浏览器 UI（3108）只是**可选演示层**。

## 启动

前置 Node >=22.13、Python >=3.13；后端在 `apps/gateway` 用 `uv sync --extra migration` 安装。运行配置为 `apps/gateway/config/settings.json`，模板在同目录。检索空间以该配置 `indexing` 段为契约值；旧索引必须从原文重建并验收后切换，不能只改配置。

```powershell
cd C:\ChatOS\apps\chatos-console
npm ci
npm run build
cd C:\ChatOS
node scripts/verify-entry.mjs
scripts\start_chatos.bat
node scripts/verify-entry.mjs --live
```

统一启动器用 Node 隐藏启动 UI 与网关；仅复用源码、构建及启动身份相符的服务。未知/过期端口占用会报错退出，不杀进程。默认门控只检查源码/构建/配置；`--live` 额外验证运行实例、跳转、HTML 与 assets。

## 给 agent 的消费入口（网关 8912）

| 能力 | 端点（示例） |
|---|---|
| 健康检查 | `GET /api/health` |
| 会话查询 | `GET /api/conversations`、`GET /api/conversations/{id}` |
| 检索 | `GET /api/search?q=...` |
| 待办 / 整理 | `GET /api/tasks`、`POST /api/organize` |
| 知识归档 / 文件索引 | `GET /api/knowledge/*`、`GET /api/knowledge/index/status` |
| 向量状态 | `GET /api/vector/status` |

完整路由与参数以 `apps/gateway/src/wechat_ai/api/` 源码为准。模型端点与维度**不在仓库文档复述**——见 `rules/local-model.md` 与 `C:\AI\memory\_canonical\LOCAL-MODELS.md`。

## 给 AI 接上（MCP 插头，可选但推荐）

ChatOS 自带零依赖 MCP stdio server（只读），Claude Code / OpenClaw 等一行配置即可查微信记忆：

1. 复制仓库根 `.mcp.json.example` 为你的 AI 工具的项目配置（Claude Code 为 `.mcp.json`，OpenClaw 按 `mcpServers` 同构配置）。
2. 启动后你的 AI 会看到 5 个工具：`chatos_search`（搜聊天记录）、`chatos_conversations`（会话列表）、`chatos_recent_messages`（读最近消息）、`chatos_tasks`（待办）、`chatos_memory_status`（记忆健康）。

MCP 进程与网关独立：**不需要网关在跑**，只读打开 `D:\ChatOS-Vault` 的库，不写任何数据。手动验证：

```powershell
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | apps\gateway\.venv\Scripts\python.exe -m wechat_ai mcp
```

## 边界

| 项 | 位置 |
|---|---|
| 核心服务（网关 API） | `apps/gateway`，8912 |
| 可选演示 UI | `apps/chatos-console`，3108（agent 不需要它） |
| 构建 | `apps/chatos-console/dist` |
| 数据 | `libraryDb` 配置；当前 `D:\ChatOS-Vault\library\wechat-ai.sqlite`；**原始表只读**；会话/知识指针在 `D:\ChatOS-Vault\conversations\` 与 `knowledge/`（本仓库仅指针） |
| 历史归档 | `docs/archive/`、`archive/`（含已归档的 agent-runtime，勿当现役） |
| 日志 / 备份 | `var/logs` / `var/backups`，不入 Git |

模型调用一律本地（llama-swap，见 LOCAL-MODELS.md），无外部额度。

## 验收与提交

在 `apps/gateway` 运行 `.venv\Scripts\python.exe -m pytest -q`；在 `apps/chatos-console` 依次运行 `npm test`、`npm run typecheck`、`npm run build`。原生依赖须与所用 Node 版本匹配。提交前运行 `node scripts/verify-entry.mjs` 和 `node scripts/verify-entry.mjs --live`，并实际核对消息及文件索引的模型、维度、覆盖和语义检索结果。HTTP/契约检查不替代浏览器视觉验收。

迁移入口和切换条件见 [本地检索契约](rules/local-model.md#迁移验收)。只在全量检查、生产索引切换及真实检索验收通过后提交并推送；已知失败要修复或按现役产品契约更新检查，不以历史基线失败作为豁免。

详见 [启动、冲突处理与恢复](docs/canonical-entry.md)、[agent 入口](AGENTS.md) 和 [网关规则](rules/local-model.md)。
