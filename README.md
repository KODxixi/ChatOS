# ChatOS

微信会话第二大脑。本地正式 UI 为 [127.0.0.1:3108](http://127.0.0.1:3108/)，Python 网关保留 8912 API，其网页入口跳转同一 UI。

目前会话与消息使用真实 SQLite 只读适配；待办、项目、资料、图谱、协作及用户身份仍含演示数据和内存状态。正式源码集成不代表这些业务已接入网关。界面保留 e9d78e9 原型视觉与交互，演示范围已标注。

## 启动

前置 Node >=22.13、Python >=3.13；后端在 `apps/gateway` 用 `uv sync` 安装。运行配置为 `apps/gateway/config/settings.json`，模板在同目录；数据库保持外部数据根，无需迁移。

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

## 源码和数据边界

| 项目 | 位置/说明 |
|---|---|
| 正式 UI / Express 适配 | `apps/chatos-console`，UI3108 |
| Python API | `apps/gateway`，8912 |
| 开发 UI | `npm run dev`，默认3109；不通过正式运行门控 |
| 退役原型 | `artifacts/chatos-ui-aistudio`，不再维护/启动；3000 仅历史预览 |
| 构建 | `apps/chatos-console/dist`，同时生成服务端与内容身份 |
| 数据 | `libraryDb` 配置；当前 `D:\ChatOS-Vault\library\wechat-ai.sqlite`；原始表只读 |
| 日志 / 备份 | `var/logs` / `var/backups`，不入 Git |

模型优先本地服务。UI Thinking Mode 仅调用本机 LM Studio，是否可用以实际加载和请求结果为准。

验证：console 内 `npm test`、`npm run typecheck`、`npm run build`；网关 `python -m pytest`。HTTP/契约检查不替代浏览器视觉验收。

详见 [启动、冲突处理与恢复](docs/canonical-entry.md)、[贡献约定](AGENTS.md) 和组件 AGENTS。
