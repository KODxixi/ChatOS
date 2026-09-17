# 正式入口、开发与恢复

正式源码只有根仓库的 `apps/chatos-console` 和 `apps/gateway`。前者来自原型 e9d78e9；原型展开目录 `artifacts/chatos-ui-aistudio` 和旧前端展开目录已清理，3000 旧实例已停。历史来源通过 Git 或既有 zip/bundle 回溯，不再保留第二套可运行 UI。

## 构建与启动

需要 Node >=22.13（内置 SQLite）与 Python >=3.13。在 `apps/gateway` 运行 `uv sync`；从同目录 `config/settings.example.json` 创建不入库的 `config/settings.json` 并设置数据库路径。

```powershell
cd C:\ChatOS\apps\chatos-console
npm ci
npm test
npm run build
cd C:\ChatOS
node scripts/verify-entry.mjs
scripts\start_chatos.bat
node scripts/verify-entry.mjs --live
```

3108 是正式 Node UI/Express 适配；8912 是网关 API，网页入口用 307 跳至 3108。网关保留原 API 和本地 session 保护。启动器用 Node 的隐藏后台进程，不依赖 PowerShell，也不修改执行策略。所需源码必须 Git tracked（允许提交前 staged）。默认验证不访问端口；`--live` 检查两个进程的启动身份、健康、跳转和线上资源字节。

源码摘要覆盖 UI、静态资源、包锁与构建/门控/启动脚本。`dist/build.json` 记录内容身份、原型来源 commit、构建时主仓库 `repositoryCommit`、源码 `sourceDirty` 与输出摘要。Git 基线和 dirty 状态不参与内容 buildId；仅文档等无关提交不会使构建失效。服务器内嵌身份，启动时固定代码摘要，重建不会让旧进程自动变成新版本。网关同样固定启动源码摘要。变更后先重建，再核实旧进程归属并重启；启动器拒绝未知/旧实例，绝不自行杀进程。

开发用 `npm run dev`（3109）；`npm run preview` 只有静态 UI。它们不能通过正式运行门控。Express 只读适配与网关尚未全量桥接：会话/消息真实；待办读取/状态保存、增量整理通过 `/api/gateway/*` 接入。项目、资料、图谱和协作仍含演示/内存状态；协作 API 明确返回 `X-ChatOS-Mode: in-memory-demo`，重启不保留。

## 数据、配置与日志

- 唯一配置：`apps/gateway/config/settings.json`，`libraryDb` 相对值以网关目录解析。正式 `frontendUrl` 为 `http://127.0.0.1:3108`。
- 既有真实数据：`D:\ChatOS-Vault\library\wechat-ai.sqlite` 及媒体。不会迁移、删除或重置水位。原始表只读。
- 构建：`apps/chatos-console/dist`；日志：`var/logs/{console,gateway}.{stdout,stderr}.log`；依赖和运行文件均被忽略。
- `npm run verify:production` 除只读会话外还调用一次本地 LM Studio；配置状态不是加载/推理成功证明。

## 端口冲突与恢复

门控报 occupied/stale 时，先读取监听 PID、可执行文件、命令、创建时间和 `/api/runtime`；旧版可结合原始启动日志、cwd 证据和线上 assets 与本地构建逐字节核对。只有证实属于 ChatOS 且获得本任务范围授权的实例才可停。无证据则保留并报告，禁止按端口批量 kill。

迁移前归档在 `var/backups/canonical-entry-20260917/`：`former-console-9729711.zip`、`chatos-9729711.bundle`、`prototype-e9d78e9.zip`、`prototype-e9d78e9.bundle`。两个 bundle 保留历史；`former-console-working-tree/` 展开副本已删除。归档不是维护/启动入口；需要回溯时精确提取组件并配套核对配置，不用 Git hard reset 或恢复旧数据库来回滚 UI。

浏览器视觉验收由控制器进行；本任务仅报告实际执行的 HTTP/资源/契约结果，浏览器工具不可用时标记受阻。
