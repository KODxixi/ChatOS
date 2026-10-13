# 已移除的 HTTP 端点（2026-10-14）

这些端点在全仓库（README / AGENTS / rules / docs / scripts / tests / console 源码）内**零引用**，
按「精简」裁定移除。记录在此以备恢复——恢复时从 git 历史取实现即可。

> 生成方式：从应用自身的 OpenAPI schema 导出（不是手抄）。

| 方法 | 路径 | 路径/查询参数 | 请求体 | 响应码 | 摘要 |
|---|---|---|---|---|---|
| `POST` | `/api/agent/runs/{run_id}/retry` | run_id(path!:string) | - | 200,422 | Retry Run |
| `POST` | `/api/agent/runs/{run_id}/cancel` | run_id(path!:string) | - | 200,422 | Cancel Run |
| `GET` | `/api/audit/stats` | — | - | 200 | Audit Stats |
| `POST` | `/api/conversations/classify` | all(query:boolean) | - | 200,422 | Classify Conversations Endpoint |
| `POST` | `/api/conversations/evaluate-activity` | inactiveDays(query:integer) | - | 200,422 | Evaluate Activity Endpoint |
| `POST` | `/api/conversations/extract-projects` | minMessages(query:integer) | - | 200,422 | Extract Projects Endpoint |
| `GET` | `/api/conversations/preview` | limit(query:integer) onlyActive(query:boolean) domain(query:?) | - | 200,422 | Conversation Previews |
| `GET` | `/api/conversations/{conversation_id}/links` | conversation_id(path!:string) limit(query:integer) | - | 200,422 | Conversation Links |
| `POST` | `/api/knowledge/archive` | — | - | 200 | Archive Knowledge |
| `POST` | `/api/projects/{project_id}/conversations` | project_id(path!:string) | ProjectConversationRequest {"conversationId": "string", "source": "string", "confidence": "number"} | 204,422 | Assign Project Conversation |
| `DELETE` | `/api/projects/{project_id}/conversations/{conversation_id}` | project_id(path!:string) conversation_id(path!:string) | - | 204,422 | Unassign Project Conversation |
| `POST` | `/api/rag/ask` | — | RagBody {"question": "string", "conversationId": [{"type": "string"}, {"type": "null"}], "limit": "integer"} | 200,422 | Rag Ask |
| `GET` | `/api/search/semantic` | q(query!:string) conversation_id(query:?) limit(query:integer) threshold(query:number) | - | 200,422 | Semantic Search |
| `POST` | `/api/setup/wechat-source` | — | WechatSourceRequest {"databaseRoot": "string"} | 200,422 | Configure Wechat Source |
| `GET` | `/api/gchat/memories/{memory_id}/evidence` | memory_id(path!:string) | - | 200,422 | Memory Evidence |

## 明确废弃（有替代品）

- `GET /api/search/semantic` → 由 `GET /api/search` 取代（BM25 + 向量 + RRF 混合检索）
- `POST /api/rag/ask` → 由 `POST /api/ask/stream` 取代（两段式 RAG 流式证据）
