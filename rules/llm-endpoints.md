# LLM 端点规则

**核心原则：所有 LLM / embedding / reranker 调用必须优先使用本地模型，数据不出本地，零外部 API 额度消耗。**

## 本地模型栈

| 用途 | 模型名 | 端点 | 说明 |
|---|---|---|---|
| **Embedding（向量索引，主）** | `text-embedding-qwen3-embedding-8b`（4096维） | llama-swap `http://localhost:9123/v1` | **CUDA** 全层 GPU（`--gpu-layers 99 -b4096 -ub4096 -fa on`），RTX 5090 |
| **Embedding（本地降级）** | `text-embedding-nomic-embed-text-v1.5`（768维） | LM Studio `http://localhost:1234/v1` | llama-swap 不可用时降级 |
| **Chat / RAG 问答 / 摘要** | `muse-glimmer-30b` | Muse Glimmer `http://127.0.0.1:8080/v1` | CUDA + DFlash + Vision（`--alias muse-glimmer-30b`） |
| **Reranker（搜索重排序）** | `vl-reranker-2b` | llama-swap `http://localhost:9123/v1` | 同端点，文本+图文共用（2026-09-21 由 text-reranker-8b 换） |

**关键区分（2026-09-21 全量切 CUDA）**：embedding/reranker 与 chat 主模型现在**都在 CUDA** 上共驻同一张 5090 D。
embedding/reranker 由 llama-swap 9123 拉起 `llama.cpp-cuda`；chat 由 8080 独立 CUDA 实例提供。
切 CUDA 的原因：embedding 是批量 prefill（矩阵-矩阵），Vulkan 在 NVIDIA 上拿不到 Tensor Core，实测逐条跑 ~17/s；
CUDA 后 ~130–145/s（无 500 断崖）。三模型常驻合计 ~31.7GB / 余 ~0.5GB，**不要再开第四个吃显存的程序**。

## 配置

- llama-swap 端点：`LOCAL_EMBEDDING_BASE_URL=http://localhost:9123/v1`（默认）
- llama-swap embedding 模型：`LOCAL_EMBEDDING_MODEL=text-embedding-qwen3-embedding-8b`
- LM Studio 降级端点：`LOCAL_EMBEDDING_FALLBACK_URL=http://localhost:1234/v1`
- LM Studio 降级模型：`LOCAL_EMBEDDING_FALLBACK_MODEL=text-embedding-nomic-embed-text-v1.5`
- 本地 chat 模型：`LOCAL_LLM_MODEL=muse-glimmer-30b`（Muse Glimmer 8080）
- 不需要 API key（本地服务无鉴权）
- llama-swap 模型 TTL 300s 自动卸载（embedding 常驻），空闲时释放显存
- **llama-swap 和 LM Studio 必须保持运行**；llama-swap 不可用时自动降级到 LM Studio，再不可用降级到外部 API

## 降级策略（仅本地不可用时）

降级链：**llama-swap 9123 → LM Studio 1234 → 外部 APIYI**
- 第一级：llama-swap `text-embedding-qwen3-embedding-8b`（4096维，主）
- 第二级：LM Studio `text-embedding-nomic-embed-text-v1.5`（768维，本地降级）
- 第三级：APIYI 中继 `https://api.apiyi.com/v1`，模型 `text-embedding-3-small`
- 密钥：环境变量 `APIYI_API_KEY`（或 `CC_APIYI_KEY`）
- **降级是最后的手段**：优先确保 llama-swap 运行，其次 LM Studio，最后才外部
- 降级到不同维度模型时，搜索结果可能不准确（向量空间不兼容），应尽快恢复主模型
- 启动时检查本地端点可用性，日志中明确标注当前使用的是哪个端点

## 缓存与异步

- 超时：本地 10s，外部 20s
- **缓存策略**：`get_cached()`/`put_cached()` 以 `{task_type}:{ref_id}:{sha256}` 为 key 读写 `llm_cache`；
  命中即返回，未命中才调模型；读时顺手 GC 过期行。
- **异步/降级**：HTTP 处理函数先返回规则式结果，LLM 增强在后台任务里做；
  任何网络/解析失败抛 `OpenAICompatibleUnavailable`，调用方静默回退规则结果。
  **绝不让 LLM 调用阻塞用户请求。**

## Prompt 真相源

所有 system prompt 在 `system/` 目录：
- `system/rag-system.md` — RAG 问答
- `system/summarize-system.md` — 对话摘要
- `system/todo-filter-system.md` — 待办过滤
- `system/semantic-extract-system.md` + `semantic-extract-task.md` — 知识图谱提取
