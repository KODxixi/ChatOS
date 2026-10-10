# ChatOS 本地知识库与重要会话呼吸提醒实施计划

> **Execution method:** Native implementation in this task. Follow TDD and verify the live 3108 entry before completion.

**Goal:** 把真实微信资料接入 ChatOS 本地资料库与 local-model 索引，并将重要提醒改为整张会话卡片的玻璃浮动呼吸，详情成功载入后停止。

**Architecture:** 微信 SQLite 与媒体目录保持只读真源；`knowledge/` 下只生成可删除重建的 Markdown/文件投影，local-model 为 `chatos` 投影建立独立 LanceDB 表。前端资料库只读真实 API。提醒状态以当前待办签名持久化，不改原始会话。

**Tech stack:** FastAPI, SQLite, Python stdlib subprocess/threading, local-model CLI, React/TypeScript, Gary-UI CSS.

---

## Task 1: 固化验收测试

**Files:**
- Add: `apps/gateway/tests/test_chatos_knowledge_archive.py`
- Modify: `apps/chatos-console/scripts/verify-conversation-features.ts`
- Modify: `apps/chatos-console/scripts/verify-ui-baseline.mjs`

1. 写入归档清单、local-model 注册表和 API 状态的失败测试。
2. 写入提醒签名、整卡材质动效、详情成功后确认的失败断言。
3. 运行定向测试，确认 RED。

## Task 2: 实现可重建知识投影与 local-model 接入

**Files:**
- Modify: `apps/gateway/src/wechat_ai/services/knowledge_archiver.py`
- Add: `apps/gateway/src/wechat_ai/services/local_knowledge_index.py`
- Modify: `apps/gateway/src/wechat_ai/api/knowledge.py`
- Modify: `apps/gateway/src/wechat_ai/plugins/query.py`

1. 归档所有支持的消息部件类型；已解析媒体用同盘硬链接优先、复制降级，未解析媒体只写状态清单。
2. 按会话/月生成规范化 Markdown，保留 `conversationId/messageId/partId/assetId` 证据定位。
3. 生成项目私有 local-model registry，固定本地 Qwen 4096 维与独立 `chatos` LanceDB。
4. 增加异步 `POST /knowledge/index/start`、`GET /knowledge/index/status`、`GET /knowledge/search`；索引运行不阻塞请求，不调用外部模型。
5. 运行网关定向测试，确认 GREEN。

## Task 3: 资料库切换为真实数据与索引控制

**Files:**
- Modify: `apps/chatos-console/src/types.ts`
- Modify: `apps/chatos-console/src/context/ChatOSContext.tsx`
- Modify: `apps/chatos-console/src/components/views/LibraryView.tsx`

1. 从 `/api/gateway/knowledge/items` 映射真实文件、图片、音频、视频、链接记录；数据库不可用时明确空态，不回退 mock。
2. 提供“整理并索引”入口与真实状态/统计；搜索走本地知识库接口。
3. 保留来源会话跳转，不提供虚假的删除与重试成功。
4. 运行 typecheck、前端测试与 build。

## Task 4: 重要卡片整面玻璃呼吸与已读时机

**Files:**
- Modify: `apps/chatos-console/src/components/views/ConversationsView.tsx`
- Modify: `apps/chatos-console/src/components/RightDetailPanel.tsx`
- Modify: `apps/chatos-console/src/index.css`

1. 删除点击瞬间确认；仅当右侧摘要/媒体至少一项成功载入后确认当前待办签名。
2. 动效作用于整张卡片的材质亮度、透明度和轻微抬升，不做边框闪烁；限制同时动画数量。
3. `prefers-reduced-motion` 下保留静态材质强调，不位移。
4. 运行定向前端验收。

## Task 5: 真实运行验收

1. 运行 gateway 定向 pytest。
2. 运行 `npm test`、`tsc --noEmit`、`npm run build`。
3. 运行 `node scripts/verify-entry.mjs`，启动正式入口后运行 `--live`。
4. 在 1920×1080 检查资料库、索引状态、重要卡片停止时机与控制台错误。
