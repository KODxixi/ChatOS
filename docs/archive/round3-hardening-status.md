# 第三轮困难项交付状态

基线：`029f763`；本轮修改在 `codex/canonical-entry` 工作区，尚未提交、暂存或 push。
分工提示词见 [round3-agent-handoffs.md](round3-agent-handoffs.md)。

## 已处理

- 从导入重投影中复用提取只更新派生数据的索引函数；会话索引开关、后台检索任务、启动恢复不再借此写入 message_parts/assets。显式导入流程保留。缺失导入分段时启动恢复报告 degraded，不擅自补写原始数据。
- 单会话/单消息向量写入遵守有效启用状态；模型返回后写入 SQL 再原子检查状态。全局及指定会话语义检索排除禁用会话的旧向量。
- 消息普通加载与定位共享取消及版本控制；迟到请求不能覆盖较新窗口、高亮或重开详情栏，同会话定位不再吞掉下一次切换。
- 代理采用 Node 原生流 pipeline：保留二进制/SSE，处理背压、客户端断开和响应体阶段超时。120 秒总期限保留；发送响应头后超时关闭流。
- 修正 authorizer 回调；原先重复 SQL 的整理测试改为真实 JobWorker、run_incremental_organize、项目提取及 tasks API 调用。所有测试使用临时数据，模型被隔离。
- 前端消息/代理回归已纳入 npm test。

## 验证

### 前端

在 `apps/chatos-console`：

```powershell
npm test
npm run typecheck
```

45/45 Node 测试通过（入口9、消息18、代理18）；UI基线及特征筛选检查通过；类型检查通过。
消息修复前14/16失败，代理修复前11/16失败；后续补充边界测试最终各18通过。

Vite UI 与 esbuild server 隔离构建通过，输出 `var/verification/round3-hardening-build/`，构建摘要 `00879384314e4214c2ff583de197c871b5c4ac47f010c8b44f925a42442e55d9`。仍有既有大包警告。未替换正式 dist。

### 后端

在 `apps/gateway`：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_round2_regression.py tests/test_round3_data_safety.py tests/test_database.py tests/test_knowledge_projection_service.py tests/test_graph_service.py tests/test_search_api.py tests/test_graph_api.py -q -p no:cacheprovider
```

49/49 通过。数据安全首轮7项按预期失败、计算期间禁用另2项按预期失败，修复后通过。
第26次真实水位写入后注入中断，已提交的任务/水位/pending保持前25条；项目文件失败后无新消息恢复30个项目，人工任务及原始行保持不变。

另跑 conversations_api、pagination、knowledge_api：16通过、2失败。两项均为会话响应多出分类字段的旧断言；在同一环境内存加载029f763会话模块，两项同样失败。未删除断言，不将完整后端套件宣称全绿。

## 尚未交付

- Agent A：待办状态映射、完整任务终态及超时展示。
- Agent B：评论服务器ID、协作回滚完整性。
- 全量pytest、干净检出安装、真实浏览器点击验收尚未完成。
- 未提交、push、合并或部署；未改真实聊天库，未调用真实模型，未重启3108/8912。
- 运行中的页面仍是此前版本，不代表本轮源码；两组补丁集成后统一正式构建、启动身份核验和浏览器验收。
