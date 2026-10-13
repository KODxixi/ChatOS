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
scripts\start_chatos.bat            # 唯一启动入口：网关 8912；UI（3108）默认关闭，CHATOS_UI=1 才启动
node scripts/verify-entry.mjs --live
```

统一启动器用 Node 隐藏启动网关（UI 默认不启动，`CHATOS_UI=1` 环境变量或 `--with-ui` 参数才启动 UI）；仅复用源码、构建及启动身份相符的服务。未知/过期端口占用会报错退出，不杀进程。默认门控只检查源码/构建/配置；`--live` 验证运行实例（网关必查；UI 在跑时额外校验跳转、HTML 与 assets）。

> **新增/改动任何源码文件后必须先 `git add` 再启动**：预检拒绝未入 Git 索引的源码（`Source not Git tracked`），这是契约不是 bug——防止"本地改了但跑的是旧进程"的漂移。

常用诊断与守护：

```powershell
scripts\start_chatos.bat --status    # 三态诊断：进程在 / 端口在 / 健康，一眼看清服务状态（rc 0=健康, 2=端口被不明进程占用）
scripts\start_chatos.bat --watch     # 守护模式：本进程拉起网关，退出自动重启；崩溃循环自动退避告警
```

- `--status` 输出人话结论：「端口 8912 无监听 → 未启动」/「端口被 PID X 占用但身份不兼容 → 先处理占用进程」/「✅ 进程在 + 健康 app=ready」。
- `--watch`：守护进程（supervisor）常驻，网关子进程退出 2s 后自动拉起；**30 秒内连续退出 ≥3 次判定崩溃循环**，退避升至 30s/60s/90s…并打醒目日志（查 `var/logs/gateway.stderr.log`）。日志沿用 `var/logs/*.log` 追加模式。
- 守护与子进程的关系：子进程 detached 运行但**不 unref**（守护者持续感知退出）；Ctrl+C 停止守护会一并 Terminate 网关；**守护者被强杀（任务管理器/断电）时，网关子进程仍 detached 存活**——服务不停，只是失去自动重启，重新拉起 `--watch` 即可接管。
- Windows 开机/登录自启（推荐）：`schtasks /Create /TN "ChatOS-Watch" /TR "cmd /c cd /d C:\ChatOS && node scripts\start_chatos.mjs --watch" /SC ONLOGON /RL LIMITED /F`。

**进程外看门狗（已落地，计划任务 `ChatOS Watchdog`，每 5 分钟）**：`scripts\watchdog.mjs` 探测 `8912/api/health` 与 `9123/running`，任一死亡自动拉起（网关 → `node scripts\start_chatos.mjs`；llama-swap → `schtasks /run \LlamaSwap`），留痕 `var/logs/watchdog.log`，4 分钟冷却防与启动器验收窗口撞车。与 `--watch`（进程内守护）互补：两层都要。

> **计划任务语义澄清**：`chatos-serve`（每日 07:30）只跑 `scripts\sync-once.ps1` 做 API 增量同步，**不拉起服务**——名称沿袭旧称，勿当自启动；`\LlamaSwap` 负责 llama-swap 9123 的登录/开机自启；服务自愈归 `ChatOS Watchdog`。

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

## 检索与记忆能力（现役）

| 能力 | 说明 |
|---|---|
| 混合检索 | `/api/search` 与 MCP 搜索：关键词（BM25）+ 语义（向量）双通道，RRF 融合 + 本地精排（llama-swap 重排模型） |
| 过滤规则 | 营销/闲聊/大群不入库不索引（`indexing.noiseGroupKeywords` + 成员阈值 30；`conversation_overrides` 显式开启优先于自动规则）；`/api/vector/status` 带 `filterRule.version` 指纹与 `filterBreakdown` 明细 |
| 完成度口径 | `indexedEligible`（当前规则集合内已索引）/ `eligibleMessages`；`completionPct` 恒 ≤100%（`indexed` 是含规则收紧前存量的历史总数，不作分子） |
| 事实演化 | 新事实覆盖旧记忆时，旧记录标记 `superseded` 并链到新记忆 id（`superseded_by`），**绝不物理删除**；查询默认排除被取代记录，可显式追溯历史 |
| 记忆卡片 | organize 管线从新会话抽取偏好/关系/项目卡片 → `memory_cards` 派生表 + Vault 卡片文件；`projects`/`project_pulses` 同源产出 |

检索/索引/迁移/运维的唯一规则入口：[rules/local-model.md](rules/local-model.md)。

## 边界

| 项 | 位置 |
|---|---|
| 核心服务（网关 API） | `apps/gateway`，8912 |
| 可选演示 UI | `apps/chatos-console`，3108（agent 不需要它） |
| 构建 | `apps/chatos-console/dist` |
| 数据 | `libraryDb` 配置；当前 `D:\ChatOS-Vault\library\wechat-ai.sqlite`；**原始表只读**；会话/知识指针在 `D:\ChatOS-Vault\conversations\` 与 `knowledge/`（本仓库仅指针） |
| 历史归档 | `docs/archive/`、`archive/`（含已归档的 agent-runtime，勿当现役） |
| 日志 / 备份 | `var/logs` / `var/backups`，不入 Git |

**合规红线（不可绕过）**：

- 微信原始表**只读、绝不解密、绝不写入**；只读访问一律 SQLite URI `mode=ro`。
- 派生表（向量/待办/缓存/检索片段）与微信原始表**严格分离**，互不污染。
- 浏览器 UI（3108）**默认关闭**，仅 `CHATOS_UI=1`（或 `--with-ui`）才启动；agent 只用网关 `/api/*`。
- 启动任何本地服务前，先按命令行杀掉旧 `wechat_ai serve` 实例，避免 SQLite 锁。

模型调用一律本地（llama-swap，见 LOCAL-MODELS.md），无外部额度。

## 验收与提交

在 `apps/gateway` 运行 `.venv\Scripts\python.exe -m pytest -q`；在 `apps/chatos-console` 依次运行 `npm test`、`npm run typecheck`、`npm run build`。原生依赖须与所用 Node 版本匹配。提交前运行 `node scripts/verify-entry.mjs` 和 `node scripts/verify-entry.mjs --live`，并实际核对消息及文件索引的模型、维度、覆盖和语义检索结果。HTTP/契约检查不替代浏览器视觉验收。

迁移入口和切换条件见 [本地检索契约](rules/local-model.md#迁移验收)。只在全量检查、生产索引切换及真实检索验收通过后提交并推送；已知失败要修复或按现役产品契约更新检查，不以历史基线失败作为豁免。

详见 [启动、冲突处理与恢复](docs/canonical-entry.md)、[agent 入口](AGENTS.md) 和 [网关规则](rules/local-model.md)。
