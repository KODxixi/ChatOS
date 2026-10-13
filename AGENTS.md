# AGENTS.md — ChatOS

<!-- repository-verify: scripts/verify-entry.mjs -->

## 这是什么

ChatOS 是**本地 agent（OpenClaw、Claude Code 等）的微信会话记忆与检索服务**。
消费方式：HTTP 调用常驻网关 `http://127.0.0.1:8912`（`/api/*`），或按需起只读 MCP（`python -m wechat_ai mcp`，不需要网关）。**没有浏览器 UI**——原控制台已归档到 `archive/chatos-console`。

数据本体在 `D:\ChatOS-Vault`：`library\wechat-ai.sqlite`（微信会话，**只读**）+ `knowledge\`（文件文字投影）。
模型端点唯一权威：`C:\AI\memory\_canonical\LOCAL-MODELS.md` —— **仓库文档只写指针，不复述数字**。

## 90 秒上手

```powershell
cd C:\ChatOS
scripts\start_chatos.bat          # 唯一启动入口：网关 8912（日志 var/logs/）
node scripts/verify-entry.mjs --live   # 唯一验收入口
curl http://127.0.0.1:8912/api/health  # 确认网关就绪（app=ready）
```

## 任务入口

| 要做什么 | 去这里 |
|---|---|
| 改检索 / 向量 / 索引 / 迁移 / 运维规则（含过滤规则、事实演化、记忆卡片） | [唯一规则](rules/local-model.md) |
| 改后端 API、数据库、迁移 SQL | [网关入口](apps/gateway/AGENTS.md) |
| 给 agent 接入 / 改 MCP 工具 | `apps/gateway/src/wechat_ai/mcp_server.py`、`.mcp.json.example` |
| 跑 / 改启动与验收 | `scripts/start_chatos.mjs`、`scripts/verify-entry.mjs` |
| 了解产品边界与验收 | [README](README.md#验收与提交) |
| 历史归档（勿当现状） | `docs/archive/`、`archive/` |

## 别碰 / 别信

- **微信原始表只读**；派生表与原始表严格分离。
- **不用旧端口**：8765 / 1234 / 8080 / 3108 均已退役；正式网关只有 8912。
- **不复制模型端点数字到文档**——只写指向 LOCAL-MODELS.md 的指针。
- 旧索引迁移已完成：检索空间以 `apps/gateway/config/settings.json` 的 `indexing` 段为契约值，不符即拒绝读写。
- 启动任何本地服务前，先按命令行匹配杀掉旧 `wechat_ai serve` 实例，避免 SQLite 锁。
