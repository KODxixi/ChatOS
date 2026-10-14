# 网关由外部 harness 持有（分发包）

本文件回答一个问题：**谁负责把 ChatOS 网关（`127.0.0.1:8912`）拉起来并让它活着？**
答案是**任何外部 harness** —— OpenClaw 的 agent、其他 agent 运行时、Claude Code 会话，都算。
ChatOS 自己不注册计划任务，也不假定由谁托管。

规则正文不在这里（避免两处维护）：**唯一权威是 [`rules/local-model.md` 的「生命周期与刷新」](../rules/local-model.md)**。
本文件只给**可复制的分发包**与执行注意事项。

## 为什么需要"外部持有"这件事

网关要跑导入（把新微信消息抓进来），而**一次导入实测 10–43 分钟** —— 比任何 agent 回合都长。
如果网关是某个短命会话的子进程，会话一结束就被带走，导入反复被打断，记忆永远刷不新。

所以 `chatos` skill 的 `ensure` / `fresh` 一律**经 WMI 创建进程**（`Win32_Process.Create`）：
WMI 建出来的进程挂在 `WmiPrvSE.exe` 下、不在调用方的 Windows job 对象里，因此**活得比调用方久**。

- **实测（2026-10-14，A/B 对照）**：同一个程序，直接 spawn 的那个在调用方进程树被 `taskkill /T` 后**消失**；经 WMI 起的那个**仍存活**（父进程 `WmiPrvSE.exe`）。
- **实操含义**：任何一个 harness 成功跑过一次 `ensure`，网关就**不再依赖任何会话**。

## 分发提示词（整段复制给目标 harness）

````text
【任务】接管 ChatOS 网关的持有

背景：ChatOS 是本地微信记忆服务（网关 127.0.0.1:8912）。它从 2026-10-14 起
不再注册任何 Windows 计划任务，改为由「外部 harness」通用持有。你就是一个外部
harness，现在请承担这个角色。

请依次做三件事：

1) 拉起并持有
   在【本机真 shell】里执行：
     python C:\AI\skills\chatos\scripts\chatos.py ensure --wait 180
   期望：stdout 一个 JSON，gateway 字段为 "ready"（本来就在跑）或 "started"
   （刚拉起），退出码 0。

2) 查记忆新鲜度
     python C:\AI\skills\chatos\scripts\chatos.py fresh
   期望：退出码 0（新鲜）或 3（偏旧）。
   ⚠️ 拿到 3 不是失败：本机一次导入要跑 10–43 分钟，fresh 默认【不等待】是刻意的，
   它只负责「判定 + 点火 + 如实汇报」。若你之后要回答微信相关问题，需声明
   「本次回答可能不含最近消息」。

3) 回报
   把两次命令的 JSON 原样贴回来，并用人话说清：网关是 ready 还是 started、
   记忆距上次成功导入多少分钟。

【硬约束 · 别做】
- ❌ 不要给 ChatOS 注册任何 Windows 计划任务（看门狗、定时同步都不要加）
- ❌ 不要停网关（chatos skill 不提供 stop；停会打断 10–43 分钟的导入）
- ❌ 不要直连 D:\ChatOS-Vault 的库文件绕开脚本
- ✅ 查聊天记录用 chatos.py search / conversations / messages / tasks —— 这些
  不需要网关，网关没起来也能查

【契约真相源】rules/local-model.md 的「生命周期与刷新」章节

【可选 · 常态化】若你希望网关长期不掉，可让你自己的心跳/自动化定期调用
chatos.py ensure —— 它是幂等的（已在跑就秒回、不做任何改动）。注意这是在
harness 内部调度，不是新增 Windows 计划任务。
````

## 执行注意事项

- **必须走本机真 shell**：OpenClaw 的 exec 有个已知坑——`exec.host` 会解析成 sandbox 模式，
  那样命令跑在隔离环境里、碰不到本机进程。给该执行配 `host=gateway`。
- **不要自己实现托管**：不需要写守护脚本、不需要建计划任务、不需要装服务。
  调 `ensure` 就是全部；它已经处理了"脱离调用方进程树"。
- **`ensure` 是幂等的**：网关已在跑时秒回 `{"gateway": "ready", "waitedSeconds": 0}`，不做任何改动。

## 怎么算这件事做成了

机器可判的判据（任一条成立即说明持有已建立）：

1. `curl http://127.0.0.1:8912/api/health` 返回 `app=ready`；
2. 拉起它的 harness 会话结束后，网关仍在跑（`Get-NetTCPConnection -LocalPort 8912 -State Listen` 有监听）；
3. 库里出现**完整跑完**的一轮导入 —— `background_jobs` 里 `type='import' AND status='completed'`。

第 3 条最重要：它同时证明"网关活着"和"长任务没被打断"。
