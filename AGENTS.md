# AGENTS.md

给在此工作区工作的 AI agent 定位用。人类读者看 [README.md](README.md)。

## 这是什么

**ChatOS** —— 会话聊天与项目管理操作系统。与 `C:\GarchOS`（ArchOS 建筑设计系统）并列，
文件结构互相独立但可通过 `bridges/` 互相吸纳。后续自媒体视频系统等均遵循同一基座规范。

- 定位：会话审计、聊天内容目录化管理、项目信息自动提取与分发
- 前端：基于 `C:\AI\UI\gary-ui` Liquid Glass Design System
- 后端：从 `D:\WXWork\chatbox` 迁移而来的 FastAPI/uvicorn 网关
- 知识库/会话本体在 D 盘，不落本仓库

## 与其他 OS 的关系

| OS | 路径 | 职责 | 关系 |
|---|---|---|---|
| **ChatOS** | `C:\ChatOS` | 会话聊天与项目管理 | 本仓库 |
| **ArchOS** | `C:\GarchOS` | 建筑设计系统 | 并列；ChatOS 提取项目信息 → `bridges/archos/` 自动落入 ArchOS |
| 未来系统 | `C:\*OS` | 待定 | 遵循同一基座规范 |

**互操作原则**：各 OS 独立 git 仓库、独立部署；通过 `bridges/` 目录定义跨系统数据契约，
由 agent 主动驱动数据流动，不做硬编码耦合。

## 目录

| 路径 | 职责 |
|---|---|
| `apps/chatos-console/` | ChatOS 主控制台 UI（gary-ui） |
| `apps/gateway/` | 后端网关（FastAPI，从 chatbox 迁移） |
| `apps/shell/` | Agent UI Shell 与命令面板 |
| `packages/` `modules/` | 共享包 / 模块注册描述符 |
| `config/` | 配置（`settings.json` 等，gitignore 真实配置） |
| `conversations/` | 会话库指针；本体在 `D:\ChatOS-Vault\conversations\` |
| `classification/` | 会话分类体系与规则 |
| `bridges/archos/` | ArchOS 互操作桥：项目信息提取与同步契约 |
| `knowledge/` | 知识库指针；本体在 D 盘 |
| `ops/` `scripts/` `tests/` | 运维/脚本/测试 |
| `docs/` | 文档 |
| `artifacts/` `var/` `tmp/` | 构建产物/运行时数据/临时文件 |
| `key/` | 部署私钥（gitignore，绝不提交） |
| `.agent-context/` | Agent 决策日志与上下文记录 |

## 会话分类体系（核心）

所有会话按以下维度分类，详见 `classification/taxonomy.md`：

1. **关系类型**：group（群聊）/ direct（私聊）/ system（公众号/系统）
2. **领域**：project（项目相关）/ work（工作协作）/ personal（个人社交）/ service（服务消费）/ subscription（资讯订阅）
3. **活跃度**：active（近 30 天有用户发言）/ inactive（近 30 天零发言，自动不索引）
4. **项目关联**：project 类会话必须关联到具体建筑项目 ID，通过 bridge 同步到 ArchOS

**目录化入库**：会话本体按分类组织在 `D:\ChatOS-Vault\conversations\{domain}\{project-or-id}\`，
SQLite 库作为索引和检索层。详见 `conversations/README.md`。

## 硬约定

- **大文件不进 git**：>10MB 的文件放 `D:\ChatOS-Vault\`（保留相对路径），仓库内放 README 指针。
- **会话库在 D**：聊天记录、媒体文件、索引数据库都在 D 盘，不进本仓库、不上 C 盘。
- **密钥不入库**：`key/`、`.env*`、`*.key`、`*.pem` 一律 gitignore。
- **gary-ui 母源不改**：UI 消费 `C:\AI\UI\gary-ui` 的 tokens/base.css 和 components，不反向修改母源。
- **C:\AI\ 是唯一真相源**：skills、UI、Vaults 以 `C:\AI\` 为准；本仓库不存放 skill 副本。
- **跨 OS 数据走 bridge**：ChatOS 与 ArchOS 之间的数据交换必须通过 `bridges/` 定义的契约，
  不得直接读写对方仓库的文件。

## 常用命令

（待迁移完成后补充）

- 后端启动：`uv run --no-sync chatbox serve --port 8912`（迁移后路径更新）
- 前端开发：`cd apps/chatos-console && npm run dev`
- 测试：`uv run --locked pytest -q`

## Agent 治理

- 进入本仓库的 agent 必须先读本文件和 `classification/taxonomy.md`
- 修改目录结构或分类体系前必须确认，基座规范需保持跨 OS 统一且稳定
- 项目信息提取和 ArchOS 同步由 `bridges/archos/` 下的 agent 主动驱动
- 活跃度过滤（30 天零发言不索引）是默认策略，阈值在 `config/settings.json` 中可调
