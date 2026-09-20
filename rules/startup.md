# 启动与运维规则

## 启动规则（2026-09-20 固化）

- **启动任何实例前，先按命令行匹配杀掉旧 `wechat_ai serve` 进程**，否则 SQLite `database is locked`。
- 开发端口：`--port 8765 --no-browser`；正式端口：8912（走根启动器）。
- 启动时自动执行 `self_check()`：验证 SQL 绑定和表结构
- 启动时自动执行 `start_periodic_sync()`：每 15 分钟索引新消息

## 常见问题排查

| 症状 | 排查 |
|---|---|
| 服务启动失败 / 端口被占 | 根启动器按源码与启动身份核验；未知/过期服务拒绝复用，先核实 PID/可执行文件/源码归属再处理；日志见 `var/logs/` |
| LLM 401 / 摘要没生效 | `APIYI_API_KEY` 未设或 key 失效；`llm_service.is_available()` 应为 True；此时摘要自动回退规则式，功能不挂 |
| 前端白屏 | 前端改完没构建或 3108 未启动；先跑 `node scripts/verify-entry.mjs --live` |
| 数据库锁定 / `database is locked` | WAL + 30s busy_timeout 已缓解；仍报锁通常是外部进程独占打开了 sqlite，关闭后重启 |
| 媒体打不开 | `apps/gateway/config.json` 的 `db_dir` 没配，媒体根解析不到；`/api/assets/{id}` 依赖它 |
| 迁移没跑 | `app_meta.schema_version` 对不上 migrations 最大序号；删库前先备份（`data/backups/`） |
| 索引覆盖 gap | 跑 `verify_index_coverage()` 对比 eligible vs indexed；gap>0 说明有消息未索引或失败 |
