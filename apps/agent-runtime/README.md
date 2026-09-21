# ChatOS Agent Runtime

本地 Agent Runtime，直连 LM Studio OpenAI 兼容 API，为 ChatOS 提供可编程的 agent 执行引擎。所有推理走本地模型，禁止外部 API。

## 架构

```
前端 (chatos-console)
    ↓ WebSocket (:8913)
Agent Runtime Server (Node.js + TypeScript)
    ├── LocalModelClient ── HTTP/SSE ──→ llama-swap :9123 (muse-glimmer-30b)
    │                                      ├── 流式文本输出
    │                                      └── tool-calling (function calling)
    ├── SessionManager (SQLite 持久化)
    ├── ToolRegistry (embedding / file / quality-check)
    └── 事件总线
```

## 硬约束

- **禁止外部模型 API**，所有推理本地
- **llama-swap 9123** `http://127.0.0.1:9123/v1`，模型 `muse-glimmer-30b`
- **llama-swap** `http://localhost:9123/v1`，embedding `text-embedding-qwen3-embedding-8b`
- 沙箱：工具只能访问 `workspaceDir` 内的文件
- LM Studio `defaultContextLength` 需 ≥ 16384（系统提示+工具定义约 12K tokens）

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
npm run demo         # 直连 LM Studio，展示流式输出 + tool calling
npm run demo "生成一个 embedding 测试"   # 自定义 prompt
```

## 配置

复制 `config/example.json` 并修改，或通过环境变量覆盖：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `lmStudioUrl` | `http://127.0.0.1:9123/v1` | llama-swap chat 端点 |
| `model` | `muse-glimmer-30b` | 模型名 |
| `embeddingUrl` | `http://localhost:9123/v1` | llama-swap 端点 |
| `workspaceDir` | `~/ChatOS/workspace` | agent 工作目录（沙箱根） |
| `wsPort` | `8913` | WebSocket+HTTP 端口 |
| `dbPath` | `~/ChatOS/data/agent-runtime.sqlite` | SQLite 会话库 |
| `maxToolIterations` | `20` | 单轮最大工具调用次数 |
| `modelTimeoutMs` | `120000` | 模型调用超时 |
| `maxTokens` | `4096` | 单次响应最大 tokens |
| `temperature` | `0.3` | 采样温度 |

## WebSocket API

连接 `ws://localhost:8913`，发送 JSON 消息：

```jsonc
// 开始新会话
{ "type": "start", "title": "My Task" }

// 发消息（触发流式输出 + agent loop）
{ "type": "message", "sessionId": "sess_xxx", "text": "清洗 data/raw 下的 CSV" }

// 中断当前推理
{ "type": "interrupt" }

// 列出会话
{ "type": "list" }

// 会话历史
{ "type": "history", "sessionId": "sess_xxx" }

// 重命名
{ "type": "rename", "sessionId": "sess_xxx", "title": "新标题" }

// 删除会话
{ "type": "delete", "sessionId": "sess_xxx" }
```

服务端推送事件：
- `connected` — 连接成功，返回工具列表和模型名
- `session-started` — 新会话创建
- `user-message` — 用户消息已入库
- `text-delta` — 助手文本增量（打字机）
- `thinking` — 模型推理过程（reasoning_content）
- `tool-call` / `tool-result` — 工具调用状态
- `turn-complete` — 本轮结束
- `error` — 错误
- `sessions` / `history` / `deleted` / `renamed` — 查询响应

## HTTP API

- `GET /health` — 健康检查（模型可达性、活跃会话、工具列表）
- `GET /sessions` — 会话列表

## 内置工具

| 工具 | 说明 |
|---|---|
| `generate_embedding` | 调用 llama-swap :9123 生成向量 |
| `read_file` | 读取工作区内文件 |
| `write_file` | 写入工作区内文件（自动建目录） |
| `list_files` | 列出工作区目录 |
| `quality_check` | CSV/JSON 数据质检（缺失值、重复、格式） |

添加自定义工具：在 `ToolRegistry.registerDefaults()` 中注册，或运行时调用 `registry.register()`。

## Agent Loop

1. 接收用户消息，存入 SQLite
2. 构建对话历史（system + 历史消息）
3. 调用 LM Studio chat/completions（流式，带 tools）
4. 解析 SSE：text-delta 实时推送，tool_calls 累积
5. 如有 tool_calls：逐个执行工具，结果作为 tool role 消息追加，回到步骤 3
6. 无 tool_calls：本轮结束，assistant 消息入库
7. 最大 20 次工具迭代，防止死循环

## 验证结果

- ✅ `tsc --noEmit` 类型检查通过
- ✅ LM Studio 直连 + 流式输出
- ✅ tool-calling（Muse Glimmer 30B 支持 function calling）
- ✅ 多工具 agent loop（write_file → read_file → 总结）
- ✅ 沙箱路径隔离（越界路径被拒绝）
- ✅ WebSocket :8913 + HTTP /health /sessions
- ✅ SQLite 会话持久化
- ✅ 断网可用（纯本地）

## 文件结构

```
src/
├── index.ts             # 入口
├── server.ts            # WebSocket+HTTP 服务器
├── LocalModelClient.ts  # 直连 LM Studio API + agent loop + SSE 解析
├── SessionManager.ts    # SQLite 会话持久化
├── ToolRegistry.ts      # 工具注册与执行
├── config.ts            # 配置
├── demo.ts              # 最小可运行 demo
└── schemas/             # (已废弃) 旧 codex 协议类型
config/
└── example.json         # 配置示例
```

## 历史

- v2（当前）：直连 LM Studio API，移除 Codex CLI 依赖。原因：Codex CLI v0.154.0 `app-server` 模式与本地 OSS 存在兼容性问题（推理阶段持续超时），而 `codex exec` 模式正常但不适合长驻 runtime。
- v1：基于 Codex CLI `app-server --stdio` JSON-RPC。代码已移除，类型定义保留在 `src/schemas/` 供参考。
