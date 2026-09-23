# ChatOS 模型端点

- 检索模型、1024 维约束与调用配置：唯一权威为 [local-model.md](local-model.md)。
- 共享服务启动参数与模型生命周期：[LOCAL-MODELS](C:/AI/memory/_canonical/LOCAL-MODELS.md)，不要在本仓库复制显存、TTL 或吞吐结论。
- Chat/RAG/摘要使用本地 llama-swap 9123 的 `muse-glimmer-30b`；配置加载见 [config.py](../apps/gateway/src/wechat_ai/config.py)。
- embedding 使用 `text-embedding-qwen3-embedding-0.6b`，实际返回必须为 1024 维；无外部或旧模型 fallback。
- reranker 使用 `vl-reranker-2b`，仅返回分数，不设置向量维度。
- embedding 失败明确报告，禁止写入无效向量；chat 的规则式结果降级不调用外部模型。
- RAG 提示词：[rag-system.md](../apps/gateway/src/wechat_ai/system/rag-system.md)；Gchat 预设：[gchat-preset.md](../apps/gateway/src/wechat_ai/system/gchat-preset.md)。
