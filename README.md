# ChatOS

微信会话的第二大脑：从微信对话中自动提取摘要、待办和项目，在本地构建一个可检索、可图谱化的个人知识库。

ChatOS 不修改你的微信原始数据——它把解密后的只读 SQLite 作为索引层，在旁边新建派生表
（tasks / llm_cache / projects / graph），再用 LLM 给会话加一层智能。前端是 Gary-UI Liquid Glass 风格的
本地控制台，后端是 FastAPI 网关，全部跑在 `http://127.0.0.1:8912/`，数据不出本机。

## 功能特性

- **会话摘要**：规则式打分即时出摘要，LLM 在后台异步增强；结果缓存进 `llm_cache`，不阻塞界面。
- **待办提取**：自动从消息里抽出 todo（规则初筛 + LLM 过滤），支持按状态/优先级过滤与勾选。
- **项目识别**：从 project 类会话聚类出项目，统计决策、里程碑、甲方反馈。
- **知识图谱**：会话/实体/决策的 nodes-edges 图谱，带证据回溯。
- **审计面板**：媒体、链接、任务覆盖率与健康度统计。
- **全文搜索**：跨会话检索消息内容。
- **媒体预览**：图片/文件/语音分页，本地附件经 `/api/assets/{id}` 直出。
- **链接归档**：会话内链接一键归档到知识库。
- **自动归档**：30 天零发言会话自动标记 inactive，不进索引。

## 快速启动（3 步）

前置：Python ≥3.13、Node ≥20。

```powershell
# 1. 安装后端依赖
cd C:\ChatOS\apps\gateway
uv sync

# 2. 构建前端（产出 dist/，由后端托管）
cd C:\ChatOS\apps\chatos-console
npm install; npm run build

# 3. 启动
cd C:\ChatOS\apps\gateway
.venv\Scripts\chatbox.exe serve --port 8912 --no-browser
```

打开 http://127.0.0.1:8912/ 。
一键脚本见 `scripts\start_chatos.bat`。配置（数据库路径、模型、活跃度阈值）在
`apps/gateway/config/settings.json`；LLM key 走环境变量，模板见根目录 `.env.example`。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vite 8 · React 19 · TypeScript 5.9 (strict) · Gary-UI Liquid Glass (CSS 变量) |
| 后端 | Python ≥3.13 · FastAPI · uvicorn · typer · Cordis 风格 plugin kernel |
| 数据 | SQLite (WAL)，原始表只读 + 派生表；迁移 `migrations/001-016.sql` |
| LLM | OpenAI 兼容中继（APIYI）· claude-haiku · `llm_cache` 数据库缓存 · 异步+降级 |

## UI 一览

- **顶栏**：全局搜索框（Ctrl/Cmd+K 聚焦）、审计、知识图谱入口。
- **两栏会话视图**：左侧会话列表（活跃度过滤），右侧会话详情（摘要 / 媒体 / 链接）。
- **三个主 tab**：会话、待办、项目。
- **两个 Overlay**：审计面板、知识图谱，Esc 关闭。

## 项目结构

```
C:\ChatOS
├── apps\
│   ├── chatos-console\   # 前端 SPA（见其内 AGENTS.md）
│   └── gateway\          # FastAPI 后端（见其内 AGENTS.md）
├── config\               # 运行配置 settings.json
├── classification\       # 会话分类体系
├── bridges\archos\       # ArchOS 互操作契约
├── scripts\              # 启动/运维脚本
└── AGENTS.md             # ★agent 贡献指南（架构/数据模型/API/红线）
```

完整架构、数据模型、API 一览与禁忌见 [AGENTS.md](AGENTS.md)。
