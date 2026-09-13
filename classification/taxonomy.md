# 会话分类体系

ChatOS 的会话分类是目录化入库和项目管理的基础。所有会话必须归入以下分类体系。

## 分类维度

### 1. 关系类型（kind）

| 类型 | 标识 | 说明 |
|---|---|---|
| 群聊 | `group` | 多人群聊，`@chatroom` 结尾 |
| 私聊 | `direct` | 一对一私聊 |
| 系统 | `system` | 公众号（`gh_` 开头）、文件传输助手、新闻等 |

### 2. 领域（domain）

| 领域 | 标识 | 说明 | 目录路径 |
|---|---|---|---|
| 项目相关 | `project` | 与具体建筑项目直接相关的群聊/私聊 | `conversations/project/{project_id}/` |
| 工作协作 | `work` | 公司内部、团队、客户沟通，不绑定单一项目 | `conversations/work/` |
| 个人社交 | `personal` | 家人、朋友、兴趣群 | `conversations/personal/` |
| 服务消费 | `service` | 商家、客服、营销、福利群 | `conversations/service/` |
| 资讯订阅 | `subscription` | 公众号推送、新闻、行业资讯 | `conversations/subscription/` |

### 3. 活跃度（activity）

| 状态 | 标识 | 判定规则 | 索引策略 |
|---|---|---|---|
| 活跃 | `active` | 用户近 30 天有发言 | 正常索引 |
| 不活跃 | `inactive` | 用户近 30 天零发言 | 自动标记 `is_indexing_enabled=0`，清 retrieval_chunks |

活跃度阈值 `activityFilterInactiveDays` 在 `config/settings.json` 中可调，默认 30 天。

### 4. 项目关联（project_link）

仅 `project` 领域的会话需要关联项目：

| 字段 | 说明 |
|---|---|
| `project_id` | 项目唯一标识，与 ArchOS 项目 ID 对齐 |
| `project_name` | 项目名称 |
| `role` | 会话在项目中的角色：`core`（核心设计群）/ `collab`（协作方）/ `client`（甲方）/ `vendor`（供应商） |

## 自动分类规则

Agent 在导入和日常运行中按以下优先级自动分类：

1. **项目关键词匹配**：群名/聊天内容包含项目名称或项目编号 → `project`
2. **公司/团队关键词**：包含公司名、部门名、职位 → `work`
3. **营销/服务特征**：包含"福利""优惠""宠粉""客服""立减"等 → `service`
4. **公众号/系统**：`gh_` 开头或已知系统账号 → `subscription` / `system`
5. **默认**：无法自动判定的归入 `personal`，标记为 `needs_review`

## 目录化入库结构

会话本体存储在 `D:\ChatOS-Vault\conversations\`，按领域和项目组织：

```
D:\ChatOS-Vault\conversations\
├── project\
│   ├── {project_id}\
│   │   ├── group-chats\          # 项目群聊导出
│   │   ├── direct-messages\      # 项目相关私聊
│   │   ├── media\                # 项目相关媒体文件
│   │   └── project-brief.md      # Agent 自动提取的项目摘要
│   └── ...
├── work\
│   ├── {company_or_team}\
│   └── ...
├── personal\
│   ├── family\
│   ├── friends\
│   └── interests\
├── service\
│   ├── merchants\
│   ├── customer-service\
│   └── marketing\
├── subscription\
│   ├── official-accounts\
│   └── news\
└── archived\                     # 不活跃会话归档
    └── {year-month}\
```

**SQLite 索引库**（`data/library/wechat-ai.sqlite`）作为检索和查询层，
存储消息、会话元数据、分类标签、检索向量。目录结构用于人工审计和跨系统交换。

## 分类标签数据模型

```sql
-- 会话分类标签（conversations 表扩展字段）
ALTER TABLE conversations ADD COLUMN domain TEXT;           -- project/work/personal/service/subscription
ALTER TABLE conversations ADD COLUMN project_id TEXT;       -- 关联项目 ID（仅 project 类）
ALTER TABLE conversations ADD COLUMN project_role TEXT;     -- core/collab/client/vendor
ALTER TABLE conversations ADD COLUMN classification_status TEXT;  -- auto/manual/needs_review
ALTER TABLE conversations ADD COLUMN classified_at INTEGER; -- 分类时间戳
```

## 与 ArchOS 的联动

`project` 领域的会话由 `bridges/archos/` agent 监控：

1. 从项目相关群聊中提取：需求变更、设计决策、时间节点、甲方意见
2. 生成/更新 `project-brief.md`
3. 通过 bridge 契约同步到 ArchOS 的对应项目目录
4. ArchOS 中的项目状态变更也可反向通知 ChatOS 标记相关会话

详见 `bridges/archos/README.md`。
