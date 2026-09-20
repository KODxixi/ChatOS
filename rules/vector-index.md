# 向量索引规则

## 两套向量系统（边界清晰，各管各的）

| 通道 | 存储 | 内容 | 真相源 |
|---|---|---|---|
| **文本聊天向量** | SQLite `message_embeddings` 表（BLOB float32, 4096维） | 纯文本消息（type='text'），私聊+小群+家人群 | `services/embedding_service.py` |
| **文件类投影向量** | LanceDB `D:\ChatOS-Vault\library\knowledge\lancedb` | 图片/附件/音频/视频/链接（type in attachment/image/audio/video/link） | `services/knowledge_archiver.py` + local-model CLI |

**text 故意不在 `knowledge_archiver._SUPPORTED_KINDS` 里**——文本走 message_embeddings 通道，文件走 LanceDB。这是设计，不是 bug。

## 向量索引配置

- 向量存储：`message_embeddings` 表（migration `017_message_embeddings.sql`），BLOB 存储 float32 向量
- **主模型**：`text-embedding-qwen3-embedding-8b`（4096 维），llama-swap 9123，Vulkan 全层 GPU
- 只索引有文字内容的消息（跳过纯表情/图片/语音/文件）
- **批量 1024 条/请求**（`embedding_service.BATCH`，2026-09-20 实测甜点 ~70 docs/s），GPU 加速
- 增量更新：`syncEnabled=true` 时后台线程每 15 分钟索引新消息（见 `indexing.syncIntervalMinutes`）
- **embedding 模型切换时必须清除旧向量重新索引**（维度不同不能混用，如 768↔4096）

## 索引过滤规则（硬约束，不可跳过）

向量索引**必须**应用以下过滤，否则大群和噪音群会拖垮索引速度并污染语义搜索结果：

| 规则 | 条件 | 原因 |
|---|---|---|
| **跳过 30 人以上大群** | 群聊唯一发送者数 > `GROUP_SIZE_THRESHOLD`（默认 30） | 大群（业主群/家庭群/行业群）占 80%+ 消息量但语义价值低，是索引速度瓶颈 |
| **跳过噪音群** | 群标题匹配 `NOISE_GROUP_KEYWORDS`（闲聊/营销/广告/推广/销售/客户/用户群/交流群/福利/优惠/团购/秒杀/业主群/同学群/老乡/红包/抽奖/外卖/淘宝/拼多多 等，共 47 词） | 废话群/营销群/销售群的消息是语义噪音，污染搜索结果。**家人群已放行**（2026-09-20 移除「家人/家庭/亲友/一家人/相亲相爱/亲友团」6 词） |
| **私聊不跳过** | `kind='direct'` | 1对1对话通常有价值 |
| **system 不索引** | `kind='system'` | 系统通知/公众号推送 |

过滤实现：`services/embedding_service.py` 的 `_get_indexable_conversations()` 函数，
在 `index_all_conversations()` 和 `index_top_conversations()` 中统一调用。
**新增索引入口必须经过此过滤，不得绕过。**

过滤效果参考：139 个群聊中跳过 18 个（9 大群 + 9 噪音群），
索引量从 84,531 条降至 ~48,000 条（减 43%），索引时间从 ~35 分钟降至 ~20 分钟。

## 触发索引

- GET `/api/vector/index/start` — 触发 top 20 会话索引（无需 body，避免 422）
- GET `/api/vector/index/start?full=true` — 触发全量索引（应用过滤规则）
- GET `/api/vector/index/start?conversation_id=<id>` — 索引单个会话
- GET `/api/vector/status` — 索引进度（indexed/total/running/progress/current conversation）
- POST `/api/vector/index` — 备用（body 可选，`conversation_id` / `full`）

## 启动时自检

- `self_check(db_path)` 在启动时验证 SQL 绑定和表结构
- `verify_index_coverage(db_path)` 在每次索引后对比 eligible vs indexed
