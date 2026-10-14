# 正式入口、开发与恢复

正式源码只有 `apps/gateway`（网关）。原浏览器控制台 `apps/chatos-console` 已于 2026-10-14 **归档**到 `archive/chatos-console`：不参与构建、启动与验收，历史原型来源通过 Git 或 `var/backups/canonical-entry-20260917/` 的 bundle/zip 回溯。

## 启动

需要 Node >=22.13（内置 SQLite）与 Python >=3.13。在 `apps/gateway` 运行 `uv sync`；从同目录 `config/settings.example.json` 创建不入库的 `config/settings.json` 并设置数据库路径。

```powershell
cd C:\ChatOS
node scripts/verify-entry.mjs
scripts\start_chatos.bat
node scripts/verify-entry.mjs --live
```

**8912 是仅有的正式端口**（3108 / 3000 已退役）。网关只注册 `/api/*`，没有前端 catch-all、没有 307 跳转。启动器用 Node 的隐藏后台进程，不依赖 PowerShell，也不修改执行策略。

**所需源码必须已入 Git 索引**（允许提交前 staged）。默认门控不访问端口：核对源码是否入索引 + `libraryDb` 存在且表结构完整；`--live` 再核对运行实例的启动身份与 `/api/health`。

## 身份契约

`/api/runtime` 报告：`startupCodeIdentity`（**进程启动时固定**的网关源码内容摘要——重建不会让旧进程自动变成新版本）、`sourceRoot`、`libraryDb`、`configIdentity`（settings.json 字节摘要）、`pid`。

启动器据此区分"可复用的自己人"与"身份不明的占用者"，不匹配即报错退出，**绝不杀进程**。变更源码后先 `git add`，再核实旧进程归属并重启。

## 数据、配置与日志

- 唯一配置：`apps/gateway/config/settings.json`，`libraryDb` 相对值以网关目录解析。
- 既有真实数据：`D:\ChatOS-Vault\library\wechat-ai.sqlite` 及媒体。不会迁移、删除或重置水位。原始表只读。
- 日志：`var/logs/gateway.{stdout,stderr}.log`；另有 `hourly-sync.log`（仅手动跑 `scripts/sync-once.ps1` 时产生）。`watchdog.log` 已随看门狗退役停写（计划任务 2026-10-14 删除）；依赖与运行文件均被忽略。

## 端口冲突与恢复

门控报 occupied/stale 时，先读取监听 PID、可执行文件、命令、创建时间和 `/api/runtime`；只有证实属于 ChatOS 且获得本任务范围授权的实例才可停。无证据则保留并报告，禁止按端口批量 kill。

迁移前归档在 `var/backups/canonical-entry-20260917/`：`former-console-9729711.zip`、`chatos-9729711.bundle`、`prototype-e9d78e9.zip`、`prototype-e9d78e9.bundle`。归档不是维护/启动入口；需要回溯时精确提取组件并配套核对配置，不用 Git hard reset 或恢复旧数据库来回滚。
