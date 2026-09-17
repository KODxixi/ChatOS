# ChatOS console

唯一维护源码，来源 e9d78e9c3f1c216c668a4810fe890d764131b88a。保留原型的 React/Vite/Express/Gary-UI 实现与视觉，禁止另建重复组件树。根 AGENTS 的数据、安全与模型约束继续生效。

- `src/context/ChatOSContext.tsx` 管理状态；`src/components/` 是现役组件，`src/core/` 为过滤/排序逻辑。
- `server.ts` 是同源 Express 适配：真实会话/消息只读 SQLite，其余协作 API 在内存里。不要把演示数据声明为已落库，也不要将整个适配迁到 Python。
- `src/mockData.ts` 仍用于项目、资料、图谱、协作等演示及初始占位。真实待办与增量整理走 `/api/gateway/*`；新 UI 文案明确真实/演示边界。
- `src/vendor/gary/` 为 vendored 资源；复用 token，不修改母源。新增交互保留键盘、焦点与 reduced-motion 支持。

Node >=22.13。`npm ci` 安装已有锁定依赖；`npm run typecheck`；`npm test`；`npm run build` 同时构建 UI、server.cjs 和 build.json。内容摘要包含源码/配置/构建脚本及锁文件，时间不参与身份。

正式启动使用根 `scripts/start_chatos.bat`，同时核验/启动 UI3108 和网关8912。`npm start` 仅启动正式 UI（需网关独立就绪）；`npm run dev` 为开发3109，不是正式验收入口。`npm run preview` 只有静态预览，不提供 API。

配置只读 `../gateway/config/settings.json`，相对 `libraryDb` 以网关目录解析。`dist/`、node_modules 不入 Git。模型变量仅本地 `.env`，不要入库凭据。UI 根目录 `/api/runtime` 返回进程启动时固定身份，不能按请求重新读 build.json。

验证：根 `node scripts/verify-entry.mjs`，线上再加 `--live`。`npm run verify:production` 会真实调用一次本地模型，需加载模型；门控不会调用模型。HTTP 验证不等于浏览器视觉验收。
