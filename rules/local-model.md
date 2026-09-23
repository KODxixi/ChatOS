# ChatOS 本地检索契约

## 配置与调用

- 唯一运行配置：[settings.json](../apps/gateway/config/settings.json)；可分发模板：[settings.example.json](../apps/gateway/config/settings.example.json)。加载实现：[config.py](../apps/gateway/src/wechat_ai/config.py)。
- 全部 ChatOS 向量只允许 `text-embedding-qwen3-embedding-0.6b`、1024 维；`indexing.model` 与 `indexing.dimensions` 成对声明。旧配置和不同的环境变量覆盖会被拒绝。
- 消息索引与问答均读取上述配置。服务统一使用 llama-swap 9123；reranker 仅打分，没有向量维度。
- 消息 SQLite、文件 LanceDB 与 Agent Runtime 使用同一模型和维度；备份不属于活动索引，不加载备份配置。
- 文件投影生成器首次生成 registry 时使用网关传入的检索端点与固定的 0.6B / 1024 空间；已有 registry 保留，不由归档覆盖。切换时需显式更新 `knowledge/local-model-registry.yaml`。

## 数据与状态

- 消息：`libraryDb` 指定的 SQLite，派生表 `message_embeddings`；微信原始表只读。
- 文件：同目录 `knowledge/lancedb` 的 `kb_chatos`，源文档在 `knowledge/index`；这是文件文字投影，不是 Archlib 图文索引。
- 正式网关 8912；`GET /api/vector/status` 返回配置及真实 `indexSpaces`，`GET /api/knowledge/index/status` 返回文件索引状态。无服务时用只读 SQLite 查询 `SELECT model_version,length(embedding)/4,count(*) FROM message_embeddings WHERE status='indexed' GROUP BY model_version,length(embedding)`。
- 旧索引与当前模型或维度不符时拒绝读写，提示 shadow 重建；不得混入新向量或修改旧 BLOB。

## 迁移验收

先备份配置与索引；SQLite 使用 backup API 包含 WAL。从原文在独立 SQLite/LanceDB 重建，逐库运行，不并发抢 GPU。新向量实际维度、配置与 schema 一致后，验证真实检索再切换；旧库保留用于回滚。

索引返回存在 `files_failed` 必须判为失败。语义检索、精排、数据覆盖与无孤儿分别验证，不能用帮助命令或模拟测试代替。迁移脚本：[migrate_vectors_1024.py](../scripts/migrate_vectors_1024.py)，先 prepare，再通过 local-model CLI 在独立目录重建文件索引，验收后 cutover。切换前停止网关和索引写入；`--settings` 必须仍指向 prepare 时的同一配置。文件重建的完整 JSON 输出保存为工作目录中的 `file-index-report.json`（必须 `files_failed=0`，总条数与 shadow 表一致）；`registry-1024.yaml` 的 `chatos.root` 保持指向原始 `knowledge/index`。旧版工作目录缺少绑定清单时需新建工作目录，不能继续切换。

## 入口与验证

- [local-model Skill](C:/AI/skills/local-model/SKILL.md)：CLI 参数和本地模型调用。
- [向量过滤规则](vector-index.md)：业务过滤与旧版本实验记录；旧吞吐数字不代表 0.6B 当前表现。
- [embedding_service.py](../apps/gateway/src/wechat_ai/services/embedding_service.py)：消息索引、模型输出校验和语义查询。
- [knowledge_archiver.py](../apps/gateway/src/wechat_ai/services/knowledge_archiver.py)：文件投影 registry；[local_knowledge_index.py](../apps/gateway/src/wechat_ai/services/local_knowledge_index.py)：索引完成判定。
- 在 `apps/gateway` 运行 `.venv/Scripts/python.exe -B -m pytest tests/test_embedding_service.py tests/test_ask_api.py tests/test_chatos_knowledge_archive.py tests/test_round3_data_safety.py -q`。
- 不查 OpenClaw/Gavis 的库，不使用已退役 local-models MCP，不占用 Archlib 8765。

一次性同步：先通过 `scripts/start_chatos.mjs` 启动并验证服务，再运行 `scripts/sync-once.ps1`；脚本复用现有服务，按真实任务状态等待完成，不关闭共享进程。

迁移工具需要网关依赖及 `migration` 可选依赖；在网关环境运行 `python -m pip install -e ".[migration]"` 后再使用脚本。测试仅使用临时数据；通过模拟 embedding 不等于真实模型检索验收。
