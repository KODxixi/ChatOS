# 第三轮修复分工（基线 029f763）

主仓库：`C:\ChatOS`，分支 `codex/canonical-entry`。正式入口与安全约束先读根及组件 AGENTS.md。

## 主审负责，不分发

原始表只读调用链、向量有效状态过滤、消息加载/定位竞态、网关流式代理、失效回归测试，以及最终集成验证。保留现有 UI，不修改真实数据库，不自行重启共享服务或 push。

## 通用协作边界（两组都要复制）

从 commit `029f763` 建独立 worktree 工作，不在 `C:\ChatOS` 切分支或直接修改共享工作区。只改下列认领范围；`ChatOSContext.tsx` 的其他区域不格式化、不重构。该共享文件由主审统一集成。

先写调用真实生产逻辑的失败测试，确认因目标缺陷失败，再修复并复跑。禁止用复制业务算法的测试代替实际函数测试。不得连接真实数据库写入、调用模型、重启 3108/8912、修改 Gary-UI 或提交凭据。不要 push、合并或自行解决主分支冲突。

交付独立 commit、精确文件/函数列表、RED/GREEN 命令与结果；如与主审修改冲突，提供限定函数补丁，由主审集成。未验证项明确标记。

## Agent A：待办状态与整理轮询

建议分支 `codex/round3-todos`。

只处理：`apps/chatos-console/src/context/ChatOSContext.tsx` 的真实待办读取、`updateTodoStatus`、`runIncrementalOrganize`；`src/types.ts` 的必要待办类型；`src/components/views/TodosView.tsx` 的必要状态交互；`apps/gateway/src/wechat_ai/api/tasks.py` 的 TaskPatch 状态契约；以及独立的新测试文件。

1. 保留现有待办状态含义。草稿/待处理/进行中/完成必须保存后重新读取仍一致，不能把非 completed 全映射为 todo。兼容已有 todo/done/completed/ignored 数据，忽略状态不要丢失。
2. 初始加载与整理后刷新使用一致映射；可复用小型函数消除这两处重复，禁止大范围拆 Context。
3. 终态和后端保持一致：completed/degraded/failed/cancelled/interrupted；queued/running/retrying 继续轮询。取消、中断、失败、超时分别如实呈现，不能挂住600次后假装idle成功。
4. 测试每个可选状态 PATCH→读取保持，以及各终态立即停止、排队/重试继续、完成后待办刷新。不得改消息加载/jump、星标、协作函数、server.ts、其他后端业务文件或 test_round2_regression.py。

若扩大 TaskPatch 的合法状态，`test_round2_regression.py` 中旧的枚举断言需要同步；只提交该测试函数的补丁建议，由主审合入，避免覆盖本轮刚重写的整理回归测试。

## Agent B：协作评论与失败回滚

建议分支 `codex/round3-collaboration`。

只处理：`apps/chatos-console/src/context/ChatOSContext.tsx` 的 `addProjectComment`、`deleteProjectComment`、`toggleCommentReaction`、`toggleSharedTask`（以实际函数名为准），以及独立的新测试文件。若认为必须修改 server.ts，先交原因/补丁建议，不直接编辑。

1. 评论创建成功后用服务器返回的完整实体替换临时实体，并返回服务器实体；后续点赞/删除使用服务器ID。
2. 删除评论、表情操作、任务状态操作失败，恢复受影响内容、计数、completedAt/completedBy。旧请求失败不能覆盖后来的成功操作。
3. 保留现有演示标识，不扩展数据库持久化，不冒充已持久化交付。
4. 测试客户端与服务器不同ID；逐种注入503；验证全部字段回滚及两个操作乱序返回。不得修改待办/轮询、消息加载/jump、server.ts或后端。

## 集成约定

两个 agent 完成后把 commit 和验证报告交给主审；不直接 cherry-pick 到正在工作的主分支。主审先检查限定 diff，再处理共享 Context 的交叉修改。最终统一构建、核对运行身份和浏览器验收；独立子任务通过不等于整站验收通过。

当前主审改动尚未提交、未部署。验证构建输出在 `var/verification/round3-hardening-build/`，只用于编译验证，不是新正式入口。现有 `dist/` 和 3108/8912 进程未替换；不要用旧运行实例的截图作为本轮修改的验收证据。
