# ChatOS 本地检索与运行契约（唯一规则）

> 本文件是 ChatOS 仓库内**唯一**的规则文件。模型端点/参数/显存一律不在此复述——
> 唯一权威是 `C:\AI\memory\_canonical\LOCAL-MODELS.md`，只写指针、不抄数字（避免漂移）。

## 定位

ChatOS 是**本地 agent（OpenClaw 等）的微信会话记忆与检索服务**：一个**按需拉起**的网关
`http://127.0.0.1:8912`（**不常驻**，谁持有见下文「生命周期与刷新」）+ 一份只读微信库 + 文件知识投影。**没有浏览器 UI**（原控制台已归档到 `archive/chatos-console`）。

## 合规边界（硬约束，不可绕过）

- **微信数据只读**：微信原始表（`messages`/`conversations` 等）在任何路径下都**只读、绝不解密、绝不写入**。只读访问一律用 SQLite URI `mode=ro`；MCP server、监控探针、验收脚本同理。
- **派生表与原始表严格分离**：`message_embeddings`/`tasks`/`llm_cache`/`retrieval_chunks` 等派生表可写，但与微信原始表分开放、互不污染；迁移 SQL 只增不改，已提交的绝不回改。
- **没有浏览器 UI**：agent 消费只走网关 `/api/*` 或只读 MCP（`python -m wechat_ai mcp`）。原控制台已归档到 `archive/chatos-console`，不参与构建与验收；网关不注册任何前端 catch-all。
- **启动前先杀旧实例**：启动任何本地服务前，先按命令行匹配杀掉旧 `wechat_ai serve` 实例，避免 SQLite `database is locked`。
- 本地模型端点/维度/显存不在本仓库复述——唯一权威 `C:\AI\memory\_canonical\LOCAL-MODELS.md`，本文件只写指针。

## 配置与调用

- 唯一运行配置：`apps/gateway/config/settings.json`；可分发模板：同目录 `settings.example.json`；加载实现：`apps/gateway/src/wechat_ai/config.py`。
- 检索模型/维度/端点：以 `settings.json` 的 `indexing` 段与 `llamaSwap`/`lmStudio` 为权威值；合理值以 LOCAL-MODELS.md 为准。旧配置与不一致的环境变量覆盖会被拒绝。
- 消息 SQLite、文件 LanceDB 与 Agent Runtime 使用同一检索空间；备份不属于活动索引，不加载备份配置。
- 文件投影生成器首次生成 registry 时使用网关传入的检索端点与 `indexing` 声明的空间；已有 registry 保留，不由归档覆盖。切换时需显式更新 `knowledge/local-model-registry.yaml`。

### 检索与记忆（现役行为）

- **混合检索**：`/api/search` 与 MCP 搜索为关键词（BM25）+ 语义（向量）双通道，RRF 融合后本地精排（llama-swap 重排模型）。关键词通道不依赖模型；语义通道用注册表 embedder。
- **事实演化**：`services/gchat_service.py` 维护记忆生命周期——新事实覆盖旧记忆时，旧记录标 `superseded` 并写 `superseded_by` 指向新记忆 id，**绝不物理删除**；查询默认排除 `superseded`（可显式追溯）。改动后必须验证新旧记录的 `status`/`superseded_by` 链。
- **记忆卡片**：organize 管线（`services/incremental_organize.py`）从新会话采样抽取偏好/关系/项目卡片 → `memory_cards` 派生表（迁移 025）+ Vault 卡片文件；仅在 `affected`（本轮有新会话）时执行。手动触发：venv python 调 `run_memory_cards_pass(conn, vault_root)` 后 **必须 `conn.commit()`**（管线自身不提交）。本地 LLM 空闲自卸时首跑会 `llmDegraded=True`（冷加载超时），预热（先发一次 chat 请求）后重跑即出卡片。
- **完成度口径**：`/api/vector/status` 的 `completionPct = min(indexedEligible, eligible)/eligible` 恒 ≤100%；`indexed` 是含规则收紧前存量的历史总数，**不可当分子**（曾实测 138.8% 即此误用）。

## 向量索引（两条链路）

- 消息正文：SQLite `message_embeddings`（`services/embedding_service.py`），BLOB float32。
- 文件/图片/音频/视频/链接的文字投影：LanceDB `kb_chatos`（`services/knowledge_archiver.py`）；普通 text 消息不重复进入文件投影。
- 向量来自原始文本，不修改、截断或转换旧向量；微信原始表只读。

### 过滤规则（硬约束，不得绕过）

`services/embedding_service.py` 的 `_get_indexable_conversations()` 统一应用：

- **跳过 >30 人群**：`GROUP_SIZE_THRESHOLD=30`，按唯一发送者数判断
- **跳过噪音群**：`NOISE_GROUP_KEYWORDS`（家人/闲聊/营销/销售/客户/用户群/交流群/福利/团购/业主群/同学群/老乡/亲友团/红包/抽奖/外卖/淘宝/拼多多 等，名单在 settings.json `indexing.noiseGroupKeywords`，文档不维护第二份）
- 私聊（`direct`）不跳过；system 不索引
- `index_all_conversations()` / `index_top_conversations()` 必须经过此过滤，**不得绕过**

### 性能真相（2026-09-21 CUDA 实测，勿从理论优化）

- 批量 128 条/请求（`indexing.batchSize`），吞吐 ~140 条/秒；批量 >256 会断崖、并发无收益、WAL 早已开启。
- 默认串行建库；不并发建库、精排或切模型。
- 索引跑起来时外部探针延迟会显著升高（可能撞 OpenClaw 记忆检索的超时上限），**索引期间别指望记忆检索稳定**。

## 数据与状态

- 消息：`libraryDb` 指定的 SQLite（当前 `D:\ChatOS-Vault\library\wechat-ai.sqlite`），派生表 `message_embeddings`；**微信原始表只读**。
- 文件：同目录 `knowledge/lancedb` 的 `kb_chatos`，源文档在 `knowledge/index`；这是文件文字投影，不是 Archlib 图文索引。
- 会话分类：`kind` ∈ direct（私聊）/ group（群聊，`@chatroom` 结尾）/ system（公众号 `gh_` 开头）/ unknown；领域分类（project/work/personal/service/subscription）与活跃度（30 天发言阈值，`activityFilterInactiveDays`）由网关与 UI 使用，会话本体在 `D:\ChatOS-Vault\conversations\`（本仓库只有指针）。
- 正式网关 8912；`GET /api/vector/status` 返回配置及真实 `indexSpaces`，`GET /api/knowledge/index/status` 返回文件索引状态。无服务时用只读 SQLite 查询 `SELECT model_version,length(embedding)/4,count(*) FROM message_embeddings WHERE status='indexed' GROUP BY model_version,length(embedding)`。
- 旧索引与当前模型或维度不符时拒绝读写，提示 shadow 重建；不得混入新向量或修改旧 BLOB。

## 迁移验收

- 先备份配置与索引；SQLite 使用 backup API 包含 WAL。从原文在独立 SQLite/LanceDB 重建，逐库运行，不并发抢 GPU。
- 新向量实际维度、配置与 schema 一致后，验证真实检索再切换；旧库保留用于回滚。
- 索引返回存在 `files_failed` 必须判为失败。语义检索、精排、数据覆盖与无孤儿分别验证，不能用帮助命令或模拟测试代替。
- 迁移工具：`scripts/migrate_vectors_1024.py`，先 prepare，再通过 local-model CLI 在独立目录重建文件索引，验收后 cutover。切换前停止网关和索引写入；`--settings` 必须仍指向 prepare 时的同一配置。
- 文件重建的完整 JSON 输出保存为工作目录中的 `file-index-report.json`（必须 `files_failed=0`，总条数与 shadow 表一致）；`registry-1024.yaml` 的 `chatos.root` 保持指向原始 `knowledge/index`。旧版工作目录缺少绑定清单时需新建工作目录，不能继续切换。

## 启动与运维

- **唯一正式启动**：根 `scripts/start_chatos.mjs`（`start_chatos.bat` 为双击薄壳，参数原样透传），只拉起网关 8912；正式日志 `var/logs/`。任何预检/启动失败都是**非 0 退出 + 显眼错误块**（原因 + 下一步），不会"看起来成功"。
- **新增/改动源码文件先 `git add` 再启动**：预检用 `git ls-files` 核验全部源码已入索引，未暂存一律拒绝启动（防本地改动与运行进程漂移）。
- **诊断**：`node scripts\start_chatos.mjs --status` 一眼区分三态——进程在/端口在/`/api/health` 健康；端口被身份不兼容进程占用时输出 PID 与可执行路径，不替你杀进程。
- **守护**：`node scripts\start_chatos.mjs --watch` 以本进程为 supervisor，网关退出自动重启（基线 2s 退避；30s 内崩 ≥3 次升级为 30s 起阶梯退避并告警）。
- **无进程外看门狗**（2026-10-14 起）：计划任务 `ChatOS Watchdog` 与 `\ChatOS\Hourly Incremental Sync` 已删除，`scripts\watchdog.mjs` 与 `scripts
un_chatos_hourly_sync.cmd` 随之退役 —— **不要再把它们接到任何计划任务上**（看门狗的唯一调用方就是那个已删任务）。启停与刷新见下文「生命周期与刷新」。
- **本仓库自身零计划任务**：剩下的 `\LlamaSwap`（登录+开机双触发）是本地模型 9123 的自启，不归 ChatOS。
- **启动前先按命令行匹配杀掉旧 `wechat_ai serve` 实例**，否则 SQLite `database is locked`。
- 启动时自动执行 `self_check()`（SQL 绑定与表结构）和 `start_periodic_sync()`（每 15 分钟索引新消息，受 `indexing.syncEnabled` 控制）。
- 一次性同步：先通过启动器启动并验证服务，再运行 `scripts/sync-once.ps1`；脚本复用现有服务，按真实任务状态等待完成，不关闭共享进程。

### 常见问题排查

| 症状 | 排查 |
|---|---|
| 服务启动失败 / 端口被占 | 根启动器按源码与启动身份核验；未知/过期服务拒绝复用，先核实 PID/可执行文件/源码归属再处理；日志见 `var/logs/` |
| LLM 不可用 / 摘要没生效 | 查 llama-swap 是否在跑（`/running` 检查见 LOCAL-MODELS.md）；不可用时摘要自动回退规则式，功能不挂 |
| 数据库锁定 / `database is locked` | WAL + 30s busy_timeout 已缓解；仍报锁通常是外部进程独占打开了 sqlite，关闭后重启 |
| 媒体打不开 | `apps/gateway/config.json` 的 `db_dir` 没配，媒体根解析不到；`/api/assets/{id}` 依赖它 |
| 迁移没跑 | `app_meta.schema_version` 对不上 migrations 最大序号；删库前先备份（`data/backups/`） |
| 索引覆盖 gap | 跑 `verify_index_coverage()` 对比 eligible vs indexed；gap>0 说明有消息未索引或失败 |
| `-wal` 文件残留 | 进程异常退出（强杀/断电）后 WAL 未合并，见下方「异常退出后 WAL 恢复」 |

### 异常退出后 WAL 恢复

正常退出时 SQLite 会自动 checkpoint（把 `-wal` 合并回主库）；**强杀/断电后 `-wal` 会残留**，主库停在崩溃前的状态，`-wal` 里是尚未落库的最近写入。

- 判断：服务已停且无进程连接（`Get-NetTCPConnection -LocalPort 8912` 无监听、无 `wechat_ai` 进程）后，`wechat-ai.sqlite-wal` 仍存在且非空 → 残留。
- 恢复（先确认无进程连接，幂等、安全）：服务停止状态下执行

```powershell
& 'C:\ChatOS\apps\gateway\.venv\Scripts\python.exe' -c "import sqlite3; c=sqlite3.connect(r'D:\ChatOS-Vault\library\wechat-ai.sqlite'); print(c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()); c.close()"
```

- 结果应为 `(0, N, N)`（busy=0）即合并成功；`(1, ...)` 表示有连接占用，先停服务再重试。合并后 `-wal` 与 `-shm` 被清空/回收，主库时间戳更新。
- 有进程在跑时**不要** checkpoint：WAL 模式允许并发读写，强行合并会拿锁失败（无数据损坏，只是白跑一次）。

## 入口与验证

- [local-model Skill](C:/AI/skills/local-model/SKILL.md)：CLI 参数和本地模型调用。
- 专项验证（在 `apps/gateway`）：`.venv\Scripts\python.exe -B -m pytest tests/test_embedding_service.py tests/test_ask_api.py tests/test_chatos_knowledge_archive.py tests/test_round3_data_safety.py -q`
- 全量验收：根 `node scripts/verify-entry.mjs [--live]`，具体命令见 README「验收与提交」。
- 不查 OpenClaw/Gavis 的库，不使用已退役 local-models MCP，不占用 Archlib 8765。

## 生命周期与刷新（2026-10-14 起：ChatOS 不注册任何 Windows 计划任务）

**硬约束：不要再给 ChatOS 增加 Windows 计划任务。** 启停与刷新由本地 agent 按需驱动，
不由系统时钟驱动。此条取代了此前并存的「ChatOS Watchdog（每 30 分钟）」与
「ChatOS\Hourly Incremental Sync（每小时）」两个任务 —— 二者已于 2026-10-14 删除。

### 职责划分（唯一权威）

| 事 | 归谁 | 触发方式 |
|---|---|---|
| 拉起网关 | 本地 agent（`chatos` skill 的 `ensure`，幂等：已在跑即 no-op） | agent 需要后台能力时 |
| 导入新消息 | 网关内置调度器 | 网关在跑时，按 `app_meta.sync_interval_minutes`（15）自跑 |
| 导入后的投影（检索/图谱/媒体/语义） | 网关（`import.completed` 监听） | 每次导入完成 |
| 整理 + 知识索引 | 网关（`import.completed` 监听 + 节流） | 距上轮超过 `_AUTO_REFRESH_INTERVAL_MINUTES`＝180 分钟才跑 |
| 停网关 | **没有人主动停** | — |

网关只在被 agent 叫醒后才活，**没有外部看门狗**：它挂了不会自愈，靠下一次 agent `ensure`。
这带来一个已知破口 —— 完全没人访问的时段，记忆会旧且不设上限（这是「零调度」的必然结果）。

### 新鲜度契约（agent 必须遵守）

查微信记忆前先跑 `chatos` skill 的 `fresh`：**距上次成功导入超过 3 小时就点火一轮导入，并如实汇报。**

**关键量化（2026-10-14 实测）**：本机一次导入 **10–43 分钟**（586 / 601 / 830 / 846 / 996 / 1529 / 2475 / 2569 秒）。这比任何 agent 回合都长，所以：

- **`fresh` 默认不等待**（`--wait 0`）——它的职责是「判定 + 点火 + 如实汇报」，**不是「等它跑完」**。同步等待只会把调用方挂死。
- 退出码：`0` = 新鲜；`3` = 偏旧（`refreshing: true`，含「刚点火」与「已有一轮在跑」两种）；`2` = 触发被拒；其它非零 = 硬故障。
- 拿到 `3` 时的义务：**声明「本次回答可能不含最近消息」**，不要把旧结果说成现状。
- 读记录本身**不需要网关**（只读直连、或只读 MCP 都行）。网关没起来 ≠ 用不了，只意味着记忆可能旧。

**网关由外部 harness 通用持有（2026-10-14 定）**：ChatOS 自己不注册计划任务，也**不假定由谁托管** —— 任何**外部 harness**（OpenClaw 的 agent、其他 agent 运行时、Claude Code 会话）都通过 `chatos` skill 的 `ensure` / `fresh` 拉起它。机制是 **经 WMI 创建进程**（`Win32_Process.Create`）。

**为什么必须绕 WMI**：直接 spawn 的进程属于调用方的进程树（Windows job 对象），调用方一退出就会被一起带走；而一次导入要跑 10–43 分钟，比任何 agent 会话都长 → 导入反复被打断、记忆永远刷不新。WMI 创建的进程挂在 `WmiPrvSE.exe` 下、不在调用方的 job 里，所以活得比调用方久。这条让「任何 harness 都能持有」成立，而不需要每个 harness 各自实现托管。

- **实测（2026-10-14）**：WMI 创建的子进程，父进程确为 `WmiPrvSE.exe`（不是发起调用的 shell）；`chatos ensure` 改走该路径后，10 秒拉起、`/api/health` 返回 `app=ready`。
- **观察记录（驱动这次改动的证据）**：直接 spawn 拉起的实例约 1 小时后消失，**无崩溃事件、无 traceback**（是被强制终止，不是崩的）；而计划任务拉起的实例存活 3 小时以上。
- **尚未取得的证据**：`ensure` 拉起的网关**能否活过调用方会话的结束**，只能靠时间验证（判据：会话结束后网关仍在跑、且导入能完整跑完一轮）。

### 已知不完美（写在这里，避免以后被当成新故障重查）

- **导入报 `WechatDecryptError: wechat database changed during refresh`**：微信正常运行时一直在写自己的库，
  ChatOS 取一致快照的 3 次重试全部撞上「库在变」。这是与活库抢文件的必然结果，不是缺陷。
- **`incremental_organize` / `semantic_graph` 常报 `degraded`**：整理本身在正常工作
  （水位推进、任务抽取、项目更新都做了），降级来自 `LLM_UNAVAILABLE` —— 本地模型按需装卸时
  30 秒（`llm_service.LLM_TIMEOUT`）不够用。要修就修那个超时，不要改整理逻辑。
