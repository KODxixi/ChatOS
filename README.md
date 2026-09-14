# ChatOS

会话聊天与项目管理操作系统。与 [ArchOS](https://github.com/KODxixi/garchos) 并列，
基于 [Gary-UI](https://github.com/KODxixi/Gary-UI) Liquid Glass Design System。

## 定位

- **会话审计**：微信聊天内容目录化入库、系统性分类、可检索可追溯
- **项目管理**：从项目相关群聊中自动提取设计决策、时间节点、甲方意见
- **跨 OS 联动**：项目信息通过 `bridges/archos/` 自动同步到 ArchOS 建筑设计系统

## 快速开始

前置：Python ≥3.13、Node ≥20（仅构建前端需要）、本地模型栈（检索走 llama-swap `127.0.0.1:9123`，对话/识图可选 LM Studio `127.0.0.1:1234`）。

### 1. 后端依赖

```powershell
cd apps\gateway
uv sync            # 或 python -m venv .venv; .venv\Scripts\pip install -e .
```

### 2. 运行时配置（本地文件，不入库）

`apps/gateway/config/settings.json`：

| 键 | 作用 |
|---|---|
| `libraryDb` | 会话库 SQLite 路径（默认 `data/library/wechat-ai.sqlite`） |
| `webDist` | 前端 dist 目录（默认 `web/dist`；本机部署指向 `../chatos-console/dist`） |
| `rerankEnabled` | 检索是否走精排模型 |
| `activityFilterEnabled` / `activityFilterInactiveDays` | 活跃度过滤 |

### 3. 构建前端

```powershell
cd apps\chatos-console
npm install
npm run build      # 产出 dist/，由后端在 / 上托管；前端源码改动后必须重跑
```

### 4. 启动

```powershell
# Windows 一键（8912 已在监听时自动跳过，防重复启动）
scripts\start_chatos.bat

# 或直接
cd apps\gateway
.venv\Scripts\chatbox.exe serve --port 8912 --no-browser
```

访问 http://127.0.0.1:8912/ —— 控制台 UI 与 API 同源（`/api/*`），
**不需要**单独跑 Vite dev server。`serve` 默认会打开浏览器，自动化/自启场景加 `--no-browser`。

### 5. Windows 自启

计划任务 `chatbox-serve`（Boot + Logon 触发，`LogonType=Interactive`）→ `scripts\start_chatos.bat`。
`InteractiveToken` 的含义是**仅在用户已登录时可启动**。

## 架构

```
ChatOS
├── apps/
│   ├── chatos-console/   # 主控制台 UI（gary-ui）
│   ├── gateway/          # FastAPI 后端网关
│   └── shell/            # Agent UI Shell
├── classification/       # 会话分类体系
├── conversations/        # 会话库指针（本体在 D:\ChatOS-Vault\）
├── bridges/archos/       # ArchOS 互操作桥
└── ...
```

详见 [AGENTS.md](AGENTS.md)（agent 治理）和 [classification/taxonomy.md](classification/taxonomy.md)（分类体系）。
