# ChatOS

会话聊天与项目管理操作系统。与 [ArchOS](https://github.com/KODxixi/garchos) 并列，
基于 [Gary-UI](https://github.com/KODxixi/Gary-UI) Liquid Glass Design System。

## 定位

- **会话审计**：微信聊天内容目录化入库、系统性分类、可检索可追溯
- **项目管理**：从项目相关群聊中自动提取设计决策、时间节点、甲方意见
- **跨 OS 联动**：项目信息通过 `bridges/archos/` 自动同步到 ArchOS 建筑设计系统

## 快速开始

（迁移完成后补充）

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
