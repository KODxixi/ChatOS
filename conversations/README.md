# 会话库

本目录是会话库的**指针**，本体在 `D:\ChatOS-Vault\conversations\`。

## 目录结构

```
D:\ChatOS-Vault\conversations\
├── project\           # 项目相关会话（按项目 ID 组织）
├── work\              # 工作协作会话
├── personal\          # 个人社交会话
├── service\           # 服务消费/营销会话
├── subscription\      # 资讯订阅
└── archived\          # 不活跃会话归档
```

详细分类规则见 `../../classification/taxonomy.md`。

## SQLite 索引库

- 路径：`D:\ChatOS-Vault\library\wechat-ai.sqlite`（迁移后）
- 作用：消息全文检索、向量检索、会话元数据查询
- 目录结构用于人工审计和跨系统交换

## 活跃度策略

- 用户近 30 天有发言 → `active`，正常索引
- 用户近 30 天零发言 → `inactive`，自动标记不索引，消息仍保留
- 阈值：`config/settings.json` → `activityFilterInactiveDays`（默认 30）
