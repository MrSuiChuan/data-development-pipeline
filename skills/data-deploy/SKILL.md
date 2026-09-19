---
name: data-deploy
description: P5 运维部署方法论：三道不可逆门（发布/ALTER/回刷）、[已确认发布] token、确定性 workflow
---

# data-deploy（P5）

## 三道不可逆门（autopilot 也不旁路）

发布、ALTER 执行、历史回刷，逐项 AskUserQuestion。协议：

1. 提问前 `ctx set awaiting_confirmation=true`（可用引擎 `state` 字段；写锁由 confirmation_gate 执行）；
2. AskUserQuestion 逐个问；
3. 用户确认后第一件事清除标志；
4. 发布命令必须携带 `[已确认发布]` token，publish_gate 校验；autopilot 还必须有
   code_diff.html（改动存量）与 review_report.html。

## 顺序

按 `deploy_plan.env` 分级：`prod` 走 确认 → 置 publish_confirmed=true → 发布
DDL/任务 → 回刷（先上游后本表）→ DQC 布防（L2/L3 并行 dqc-deployer）→ 生产只读
核实 → 工单逐级流转到待验收；`dev/test/qa/staging` 直接执行 DDL/回刷并留证据，
不要求发布 token、不做工单流转。统一写 delivery_summary.md（>=10 行）→ advance P6。

## 后半程也要留证据（P5 出口门会反查）

回刷和 DQC 布防不是"做完就算"，两者都要留下机器可读的产物，否则 P5 出口不放行：

- **回刷**：走引擎 `platform backfill` / deploy 命令时，引擎写
  `.data-dev/runtime/backfill_runs.json`（key = `表名|range_start|range_end`）。
  出口门逐项比对 `deploy_plan.json` 的回刷项，缺成功记录即 FAIL。
- **DQC 布防**：把 workflow 返回的 `dqc_results` 原样写入
  `<req_dir>/dqc_results.json`（`{"results":[{"table":…,"deployed":true,…}]}`）。
  出口门对 `deploy_plan` 里每个 `dqc_publish` 项反查 `deployed=true`，缺即 FAIL。
  `deployed=false` 就是没布防，不要写 true。

环境路由：SQL/命令平台由 `platform.base_env` + `platform.profiles.<env>` 决定连接，
未配置目标环境直接拒绝执行（不能靠 --env 口头指定）。create/alter 项建议声明
`rollback_sql_file`；出问题用 `ddp.py rollback --comment "[已回滚] ..."` 执行回滚。

生产工单状态只允许逐级推进（待验收→待测试→待业务验收→已交付），不许跨 stage；
工单类型必须是「数据任务」（submit 建单时 templateTypeName 必传）。
