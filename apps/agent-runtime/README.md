# ChatOS Agent Runtime

本地 Codex Agent Runtime，为 ChatOS 提供可编程的 agent 执行引擎。所有推理走本地模型，禁止外部 API。

## 架构

```
前端 (chatos-console)
    ↓ WebSocket (:8913)
Agent Runtime Server (Node.js + TypeScript)
    ├── CodexClient ── stdio JSON-RPC ──→ codex app-server
    │                                      ├── OSS mode → LM Studio :1234 (qwen3-vl-8b)
    │                                      └── sandbox (workspace-write)
    ├── SessionManager (SQLite 持久化)
    ├── ToolRegistry (embedding / file / quality-check)
    └── StreamRouter (事件总线)
```

## 硬约束

- **禁止外部模型 API**，所有推理本地
- **LM Studio** `http://localhost:1234/v1`，模型 `qwen/qwen3-vl-8b`
- **llama-swap** `http://localhost:9123/v1`，embedding `text-embedding-qwen3-embedding-8b`
- `approval_policy = "never"`（全自动，无人工确认）
- `sandbox_mode = "workspace-write"`

## 快速开始

```bash
cd C:\ChatOS\apps\agent-runtime
npm install
npm run dev          # 开发模式 (tsx)
# 或
npm run build && npm start
```

### 最小 Demo

```bash
npm run demo         # 启动 codex，发"列出当前目录的文件"，展示流式输出
```

## 配置

复制 `config/example.json` 并修改：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `model` | `qwen/qwen3-vl-8b` | LM Studio 模型名 |
| `lmStudioUrl` | `http://localhost:1234/v1` | LM Studio 端点 |
| `embeddingUrl` | `http://localhost:9123/v1` | llama-swap 端点 |
| `sandbox` | `workspace-write` | 沙箱模式 |
| `approvalPolicy` | `never` | 审批策略 |
| `wsPort` | `8913` | WebSocket+HTTP 端口 |
| `workspaceDir` | `~/ChatOS/workspace` | agent 工作目录 |
| `codexHome` | `C:\ChatOS\var\codex-home` | 独立 CODEX_HOME（隔离用户全局配置） |
| `modelTimeoutMs` | `120000` | 模型调用超时 |
| `maxRestarts` | `3` | 崩溃自动重启次数 |

## WebSocket API

连接 `ws://localhost:8913`，发送 JSON 消息：

```jsonc
// 开始新会话
{ "type": "start", "title": "My Task" }

// 发消息（触发流式输出）
{ "type": "message", "sessionId": "sess_xxx", "text": "清洗 data/raw 下的 CSV" }

// 中断当前 turn
{ "type": "interrupt" }

// 列出会话
{ "type": "list" }

// 会话历史
{ "type": "history", "sessionId": "sess_xxx" }

// 删除会话
{ "type": "delete", "sessionId": "sess_xxx" }
```

服务端推送事件：
- `connected` — 连接成功，返回可用工具列表
- `session-started` — 新会话创建
- `user-message` — 用户消息已入库
- `text-delta` — 助手文本增量（打字机）
- `tool-call` / `tool-result` — 工具调用状态
- `turn-complete` — 本轮结束
- `error` — 错误
- `sessions` / `history` / `deleted` — 查询响应

## HTTP API

- `GET /health` — 健康检查（codex 运行状态、活跃会话、工具列表）
- `GET /sessions` — 会话列表

## 内置工具

| 工具 | 说明 |
|---|---|
| `generate_embedding` | 调用 llama-swap :9123 生成向量 |
| `read_file` | 读取工作区内文件 |
| `list_files` | 列出工作区目录 |
| `quality_check` | CSV/JSON 数据质检（缺失值、重复、格式） |

添加自定义工具：在 `ToolRegistry.registerDefaults()` 中注册，或运行时调用 `registry.register()`。

## Codex App Server 协议

- 传输：JSONL over stdin/stdout，JSON-RPC 2.0
- 流程：`initialize` → `thread/start` → `turn/start`（流式 notification）
- 类型定义：`src/schemas/`（由 `codex app-server generate-ts` 生成）
- 重新生成：`codex app-server generate-ts --out ./src/schemas`

## 已知限制

1. **LM Studio context size**：qwen3-vl-8b 默认 8K context 不足以承载 codex 系统提示（~12K tokens）。已在 `~/.lmstudio/settings.json` 将 `defaultContextLength` 改为 32768。**需在 LM Studio UI 重载模型生效**。
2. **qwen3-vl-8b tool-calling**：该模型对 Codex Responses API 格式兼容性可能有限。如工具调用异常，可尝试换用 tool-calling 更强的模型。
3. **MCP 工具**：当前通过自定义 ToolRegistry 实现，未接入 Codex 原生 MCP。后续可在 `codexHome/config.toml` 配置 `[mcp_servers]`。

## 验证标准

- [x] `npm run typecheck` 通过
- [x] codex app-server 启动 + initialize 成功
- [x] `thread/start` 返回有效 threadId
- [x] `turn/start` 触发模型推理（需 LM Studio context ≥ 16K）
- [ ] 断网状态完全可用（本地模型，天然断网可用）
- [ ] 杀掉 LM Studio 后 agent 正确报错而非崩溃

## 文件结构

```
src/
├── index.ts          # 入口
├── server.ts         # WebSocket+HTTP 服务器
├── CodexClient.ts    # codex app-server JSON-RPC 客户端
├── SessionManager.ts # SQLite 会话持久化
├── ToolRegistry.ts   # 工具注册与执行
├── config.ts         # 配置
├── demo.ts           # 最小可运行 demo
└── schemas/          # codex 协议 TypeScript 类型（生成）
config/
└── example.json      # 配置示例
```
