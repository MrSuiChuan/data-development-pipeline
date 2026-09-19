---
description: 单动作穿插（免整条流水线）：改一张表、补一个测试、查状态…
argument-hint: <想做的单一动作>
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion]
---

# /copilot <单一动作> — 穿插式单点操作

当用户只想做**一个动作**（改某张表的 ETL、给某表补一个测试用例、看某工单进度）而不想重跑
整条流水线时使用。做完即走，不强行推进阶段。

## 步骤

1. 从 `$ARGUMENTS` 识别：工单号 + 意图 + 目标对象（表/任务/文件）。
   - 无工单号且只有一个候选 → 直接采用；多个候选或无法定位 → AskUserQuestion。
2. 意图 → 能力映射（只能做这些）：
   - 查状态/查工单 → `ddp status / ticket show`；
   - 改 DDL/ETL/dp_task → 先 `/start` 已到 P3（未到 P3 会被 phase_entry/阶段写门拦截）；
     或 `advance --allow-back --phase 3 --reason "copilot 穿插修改"` 回到 P3 再改；
   - 补测试 → 回 P4 后按 data-testing skill 执行单用例，更新 `test_runs.json`；
   - 展示 code_diff / 决策日志 → `ddp diff / review-report`；
   - 申请只读权限 → 调 `privilege-applier` agent（被动触发，不执行 SQL）。
3. 执行时遵循：改完 SQL 必须重跑 compliance 与 self_check；只做动作，不擅自 advance。

## 边界（不可旁路）

- **不松 SDS 门**：P2-P6 穿插改动不影响已贴单 SDS；口径变化必须 AskUserQuestion 建议重跑 /sds-design。
- **不可逆门不旁路**：发布 / ALTER / 回刷 / 进入语义层，一律走完整确认流程。
- 状态文件 `_state.json`/`_context.md` 永远由引擎写，禁止手改。

完成后汇报：改了什么、哪些门通过/被拦、下一步建议。
