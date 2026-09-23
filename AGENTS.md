# AGENTS.md — ChatOS

<!-- repository-verify: scripts/verify-entry.mjs -->

## 任务入口

| 任务 | 权威入口 |
|---|---|
| 模型、向量维度、实际索引状态、迁移边界 | [本地检索契约](rules/local-model.md) |
| 消息索引过滤与历史性能实验 | [向量规则](rules/vector-index.md) |
| 后端结构、API 与数据库开发 | [网关入口](apps/gateway/AGENTS.md) |
| 前端修改与验证 | [控制台入口](apps/chatos-console/AGENTS.md) |
| 运行配置 | [配置加载器](apps/gateway/src/wechat_ai/config.py)、[配置模板](apps/gateway/config/settings.example.json) |
| 正式启动 | [统一启动器](scripts/start_chatos.mjs) |
| 入口验收 | [验收脚本](scripts/verify-entry.mjs) |
| 全流程验收与提交门槛 | [README 验收与提交](README.md#验收与提交)、[检索迁移验收](rules/local-model.md#迁移验收) |
| RAG prompt | [提示词](apps/gateway/src/wechat_ai/system/rag-system.md)、[加载与版本日志](apps/gateway/src/wechat_ai/rag_prompt.py) |
| Gchat 行为与检索职责 | [Gchat 预设](apps/gateway/src/wechat_ai/system/gchat-preset.md) |
| 文件知识归档与索引 | [归档实现](apps/gateway/src/wechat_ai/services/knowledge_archiver.py)、[索引桥接](apps/gateway/src/wechat_ai/services/local_knowledge_index.py) |
| 产品与安装说明 | [README](README.md) |
| 本机共享模型服务 | [LOCAL-MODELS](C:/AI/memory/_canonical/LOCAL-MODELS.md) |

## 避免错误入口

- 不使用 OpenClaw/Gavis 的向量库判断 ChatOS 状态；定位方法见本地检索契约。
- 不沿用旧 8765 启动示例；正式启动以统一启动器为准。
- 不把默认 1024 或测试通过当成生产旧索引已重建；迁移验收见本地检索契约。
