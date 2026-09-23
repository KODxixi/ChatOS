# ChatOS 向量索引规则

模型、路径、维度与迁移唯一权威：[local-model.md](local-model.md)。全部 ChatOS 向量为 1024 维。

## 两条索引链路

- 消息正文：SQLite `message_embeddings`；[embedding_service.py](../apps/gateway/src/wechat_ai/services/embedding_service.py)。
- 文件、图片、音频、视频、链接的文字投影：LanceDB `kb_chatos`；[knowledge_archiver.py](../apps/gateway/src/wechat_ai/services/knowledge_archiver.py)。普通 text 消息不重复进入文件投影。
- 向量来自原始文本，不修改、截断或转换旧向量。原始微信表只读。

## 过滤与吞吐

- 过滤器是 `_get_indexable_conversations` / `_can_index_conversation`；尊重 conversation_overrides，跳过 system、超过人数上限的大群和配置的噪音群。
- 群人数、噪音词、最短文本长度、批量值以 settings 的 indexing 段为准；不在文档维护第二份名单。
- 默认串行建库，批量 128；不并发建库、精排或切模型。历史模型的吞吐与显存不适用于当前 0.6B。
- 配置 syncEnabled 不等于后台任务已运行；应核查任务、进程、索引状态和更新时间。

## 状态与验收

- `GET /api/vector/status`：消息索引状态及实际 `indexSpaces`。
- `GET /api/knowledge/index/status`：文件索引状态；存在失败文件不能标 completed。
- `GET /api/vector/index/start`、`POST /api/vector/index` 是写操作，须在已授权的索引任务内使用。
- `self_check` 与 `verify_index_coverage` 不能替代真实语义查询；迁移时必须检查模型、1024 维、来源覆盖和孤儿记录。
