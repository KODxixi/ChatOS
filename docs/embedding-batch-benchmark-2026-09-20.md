> ⚠️ **历史文档（2026-09-20）**：当时测的是 Vulkan 后端，数字已过时。后端已于 2026-09-21 全切 CUDA，当前吞吐与结论以 `rules/vector-index.md`「性能真相」为准（~140–150/s，非本文的 ~15/s）。

# Embedding BatchSize 实测结论（2026-09-20）

## 背景

ChatOS 微信聊天知识库重建前，需要确定 `settings.json` 里 `indexing.batchSize` 的最优值。
旧配置写 256，local-model `registry.yaml` 注释说"64 是实测甜点"，两者矛盾。
本文是本机实测证据，作为后续配置和调优的依据。

## 测试环境

- 硬件：RTX 5090 D（32.6 GB VRAM）
- 推理后端：llama-swap 9123（Vulkan llama.cpp）
- 模型：`text-embedding-qwen3-embedding-8b`（Q4_K_M，4096 维，全层 GPU）
- 样本：从 `D:\ChatOS-Vault\library\wechat-ai.sqlite` 随机抽取真实聊天文本
  - 512 条：min 8 字符 / median 26 / max 237
  - 4096 条：同上分布
- 端点：`POST http://127.0.0.1:9123/v1/embeddings`

## 结果

| batchSize | 吞吐 (docs/s) | 状态 | 备注 |
|---|---|---|---|
| 32 | 3.4 | OK | 太慢，HTTP 往返主导 |
| 64 | 15.1 | OK | **registry.yaml 注释说的"甜点"，实际最差区间** |
| 128 | 59.1 | OK | |
| 256 | 68.4 | OK | 旧配置值，可用 |
| 512 | 66–83 | OK | 两轮波动大（冷/热状态差异） |
| **1024** | **~70** | **OK** | **拐点：平台期起点** |
| 2048 | ~69 | OK | 持平，无增益 |
| 4096 | ~73 | OK | 略升但单次测量噪声大，恢复成本高 |

冷加载：embedding 模型从 unloaded 拉起耗时 ~80 秒（warmup），仅发生在首次请求；
`warmup_on_index: true` 已覆盖日常索引场景。

## 结论

1. **拐点在 1024**。512→1024 仍有提升，1024 后进入平台期，再大收益递减。
2. **64 不是甜点**。local-model registry.yaml 里"64 是甜点 35 docs/s"的注释是旧环境/旧版本数据，
   与当前 llama-swap 9123 + Vulkan 后端实测不符。当前 64 仅 15.1 docs/s。
3. **256 能用但偏慢**，1024 是性价比最高的选择：吞吐平台期起点，请求体 ~2MB，
   单次失败重试成本可控。
4. 4096 虽然数字略高，但只跑了 1 批、噪声大；且 4096 条一批中途失败要整批复跑，不推荐。
5. 所有 batchSize 均稳定通过，无 OOM、无 HTTP 400、无超时——这台卡跑短聊天文本余量充足。

## 适用范围

- 本结论针对**短聊天消息**（median 26 字符）。
- 长文档（knowledge_archiver 投影的 markdown、PDF 全文）batchSize 大了显存压力不同，需另测。
- ChatOS 消息向量索引的对象就是短文本，直接采用 1024。

## 最终配置

`settings.json` → `indexing.batchSize = 1024`
