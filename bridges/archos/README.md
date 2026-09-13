# ChatOS ↔ ArchOS 互操作桥

## 目的

ChatOS 负责会话采集和项目信息提取，ArchOS 负责建筑设计管理。两者通过本桥定义的契约
进行数据交换，由 agent 主动驱动，不做硬编码耦合。

## 数据流

```
ChatOS 会话库 → 项目信息提取 Agent → project-brief.md → bridge 契约 → ArchOS 项目目录
```

### ChatOS → ArchOS（主方向）

1. **触发**：ChatOS import job 完成后，或 agent 检测到项目相关会话有新消息
2. **提取**：从 `project` 领域会话中提取：
   - 项目基本信息（名称、地点、阶段、甲方）
   - 设计决策与变更记录
   - 关键时间节点（报审、施工、交付）
   - 甲方意见与反馈
   - 相关文件/图纸引用
3. **生成**：写入 `D:\ChatOS-Vault\conversations\project\{project_id}\project-brief.md`
4. **同步**：通过 bridge 契约将更新推送到 ArchOS 对应项目目录

### ArchOS → ChatOS（反方向）

1. ArchOS 项目阶段变更、任务分配等事件可通知 ChatOS
2. ChatOS 标记相关会话为 `project_status_changed`
3. 用于在会话审计中关联项目进度

## 契约文件

### project-brief.md 格式

```markdown
# {项目名称}

## 基本信息
- 项目 ID: {project_id}
- 地点: {location}
- 当前阶段: {concept/schematic/design/construction}
- 甲方: {client}
- 最后更新: {timestamp}

## 设计决策
- {date}: {decision} (来源: {conversation_id})

## 时间节点
- {date}: {milestone}

## 甲方意见
- {date}: {feedback}

## 待办
- [ ] {task}
```

### 同步状态文件

`bridges/archos/state/{project_id}.json`：

```json
{
  "project_id": "...",
  "last_synced_at": 1234567890,
  "last_message_id": 12345,
  "sync_status": "success|pending|failed",
  "archos_project_path": "C:\\GarchOS\\dds\\projects\\..."
}
```

## Agent 职责

| Agent | 职责 |
|---|---|
| `project-extractor` | 从会话中提取项目信息，生成 project-brief.md |
| `archos-sync` | 将 project-brief.md 同步到 ArchOS，维护同步状态 |
| `classifier` | 会话自动分类，标记 project 领域和项目关联 |

## 约束

- 不得直接读写 ArchOS 仓库的源代码文件
- 同步目标是 ArchOS 的项目数据目录（`D:\GarchOS-Vault\` 或 ArchOS 约定的项目目录）
- 同步失败时记录状态，不阻塞 ChatOS 正常运行
- 项目 ID 命名空间与 ArchOS 对齐，避免冲突
