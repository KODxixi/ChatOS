# 记忆卡片 Schema Spec（Memory Cards）

> 抽取管线：`apps/gateway/src/wechat_ai/services/memory_cards.py`
> 人读主存储：`D:\ChatOS-Vault\knowledge\memory-cards\`（每张一个 `.md` + `index.json`）
> 结构化查询派生表：`memory_cards`（迁移 `025_memory_cards.sql`，表不存在时管线自动跳过 DB 写入）

## 目标

把微信会话里**值得长期记住**的信息抽成「人能读、AI 能查」的卡片。只读原始消息，不修改、不解密。

## 卡片类型（闭集）

| type | 中文 | 示例 |
|---|---|---|
| `preference` | 偏好/习惯 | Gary 习惯用 mouse DPI 800、晚睡 |
| `relationship` | 关系/角色 | 曹耀耀是武汉 HYP 项目的造型负责人 |
| `project` | 项目 | 武汉 HYP 项目在做柱廊造型优化阶段 |
| `fact` | 事实 | 杭州咏滨名邸某楼栋成交价、某会议地址 |
| `decision` | 决策 | 柱子改扁、侧面要有平段 |
| `constraint` | 约束 | 抬板高度必须满足深圳日照标准 |
| `event` | 事件/节点 | 明天改层叠拱券、下周报审 |

## 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | `mc-` + sha1(`type\|subject归一\|value归一`)[:12]，稳定 |
| `type` | enum | 上表闭集之一 |
| `subject` | string | 主体（人名/项目名/对象），可独立读懂 |
| `value` | string | 一句话陈述，具体、不依赖上下文 |
| `confidence` | float | 0.0–1.0，模型自评 |
| `status` | enum | `active` / `superseded` |
| `tags` | string[] | 简短标签 |
| `sources` | object[] | 来源证据：`message_id / conversation_id / conversation_title / sender / sent_at` |
| `created_at` / `updated_at` | int | 毫秒时间戳 |

## 去重 / 更新规则

- 去重键 = `(type, 归一化subject, 归一化value)`；归一化 = 去空白 + 小写。
- 同键重复卡片：合并来源消息（按 message_id 去重）、置信度取 max、tags 合并、`updated_at` 取 max。
- 落盘时合并 `index.json` 旧条目：本批未覆盖的旧卡片保留，不物理删除。

## 抽取管线

1. **采样（只读）**：近 14 天私聊 + 项目域群聊，过滤占位/系统/短于 6 字消息（`sample_messages`）。
2. **本地模型抽取**：编号后喂给 muse-glimmer-30b（`http://127.0.0.1:9123/v1`，`max_tokens≥4096`、`reasoning_effort:low`、并发 1、只取 `content`），要求输出 JSON 数组，每条带 `evidence` 消息编号。
3. **解析**：稳健截取 `[...]` JSON，非法条目丢弃（`parse_llm_cards`）。
4. **去重合并**：`dedupe_cards`。
5. **落盘**：`write_cards` 写 `index.json` + `cards/{id}.md`；表存在时 `upsert_db` 同步派生表。

## 硬约束

- 微信原始表只读；管线不写、不解密原始消息。
- 只用本地模型，禁止外部 API。
- LLM 不可用/解析失败时静默降级为空结果，不炸主流程。
