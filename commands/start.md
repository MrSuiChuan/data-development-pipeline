---
description: 准入门五步 + P2-P6 六阶段研发态执行（全托管流水线）
argument-hint: <T-xxxx> [--autopilot]
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - Task
  - Workflow
  - Workflow(data-development-plugin:phase4-test)
  - Workflow(data-development-plugin:phase5-deploy)
  - Agent(data-development-plugin:architecture-explorer, data-development-plugin:data-tester, data-development-plugin:dqc-deployer, data-development-plugin:backfiller, data-development-plugin:privilege-applier)
---

# /start <T-xxxx> [--autopilot] — 进入研发态并跑完 P2-P6

`start` 是进入研发态的唯一入口。第一个动作必须是**准入门五步**，任何一步失败都拒绝开工。

## 第一步：准入门（引擎执行）

```
py -3 .data-dev/engine/ddp.py start --ticket <T-xxxx> [--autopilot]
```

引擎完成：工单校验 → 终版锚点（【数据概要设计 vN】）→ 内容重验（贴单 json 块重跑八条规则）→
口径 hash 比对 → 上下文重建。若返回 blocked：

- 无终版 → 提示用户先跑 `/sds-design`；
- hash 不一致 → AskUserQuestion 强提示是否重跑 SDS（人工决策，不自动作废）；
- 通过后工单自动流转「开发中」，进入 P2。

## 第二步：P2 架构设计

- 加载 `architecture-design` skill；L2/L3 调 `data-development-plugin:architecture-explorer` agent 做四层设计+方案对比+影响分析；
  L1 走精简路径（复核目标表+查下游+判加列/改值，不画方案）。
- 产出 `architecture_doc.md`（含难度级别、链路图）与 `pipeline.html`（L2/L3）。
- 转场：`py -3 .data-dev/engine/ddp.py advance --phase 3 --ticket <T-xxxx> --reason "P2 出口"`。

## 第三步：P3 数据研发（工程量最重）

- 加载 `data-develop` skill；按架构清单**自底向上**（适配→逻辑→接口→核对）。
- 每张表严格 DDL → ETL → `dp_task/` 顺序（dev_order_guard / difficulty_guard 硬门拦截乱序与 L0/L1 建表）。
- 每份 SQL 过 17 项检查（`py -3 .data-dev/engine/ddp.py sql check --file …`，9 条零容忍）。
- 开发环境真跑 ETL 并让引擎自动落证据：
  `py -3 .data-dev/engine/ddp.py platform run-sql --file <etl/*.sql> --env dev
  --ticket <T-xxxx> --save-evidence`（引擎把回执写进执行账本并生成
  `etl_dev_exec.json`；禁止 AI 手写该文件，evidence_ledger 会拦截伪造）。
- L1/L2 改现有任务必须 `py -3 .data-dev/engine/ddp.py diff --ticket <T-xxxx>` 生成 `code_diff.html`
  并向用户展示核对（autopilot 旁路的是“确认”，不旁路“diff 生成展示”）。
- self_check（L1/L2 code_diff 已看、口径一致）后由真实产物驱动写证据键：
  `py -3 .data-dev/engine/ddp.py evidence selfcheck --ticket <T-xxxx>`
- 开发环境真跑完成后把执行证据转成门控键：
  `py -3 .data-dev/engine/ddp.py evidence verify --ticket <T-xxxx>`
  （引擎只认 `etl_dev_exec.json` 里的 SUCCESS+query_id；禁止 `ctx set` 写这些证据键）。
- 平台任务注册按语义可选：有调度器的平台走 create/update/submit；纯执行平台
  （MySQL/Doris/Paimon…）只做本地代码快照。AskUserQuestion 确认后，把确认记录写入
  `_user_confirmation.md`（含 `[已确认]`），再 `evidence confirm --ticket <T-xxxx>` 置
  `user_confirmed=true`。
- 转场 P4：`advance --phase 4`（delivery_checklist 硬门 + stage_gate）。

## 第四步：P4 数据测试（机器证据硬门）

- 加载 `data-testing` skill，产出测试计划（autopilot 豁免确认，interactive 需 AskUserQuestion）。
- **调用 Workflow：`data-development-plugin/phase4-test`**（参数：ticket、项目根、test 计划），
  由 workflow 并行调度 `data-tester` agents；每个结果必须有 schema 强制的 `query_id`。
  测试 SQL 必须经 `engine platform run-sql` 执行，让 query_id 进入执行账本
  （live 模式无账本会直接 FAIL）。
- 把 workflow 返回的 `runs` 原样写成 `test_runs.json`，再把 SUCCESS 用例写成
  `test_report.md`（✅ 只标真有 SUCCESS+query_id 的 TC）。
- 转场 P5：`advance --phase 5`（报告标✅ 的 TC 无机器证据会被硬拦）。

## 第五步：P5 运维部署（按环境分级，生产三道不可逆门）

- 写 `deploy_plan.json`（9 条规则，写时校验）。
- `env` 支持 dev/test/qa/staging/prod：**只有 `env=prod` 需要逐一 AskUserQuestion
  确认发布 / ALTER 执行 / 历史回刷**（autopilot 也不旁路）；非生产环境直接执行并留证据。
  SQL/命令平台必须配置 `platform.profiles.<env>`（或 env=base_env），否则部署被拒——
  防止把 SQL 打到错误数据库。
  确认前先设 `awaiting_confirmation=true`（confirmation_gate 会锁死所有交付物写入与生产 CLI），
  用户确认后立即清除。
- autopilot 在部署前展示 `autopilot_decisions.md` 全文与 `review-report`（引擎命令生成 html）。
- 执行确定性部署（prod 命令必须带 `[已确认发布]` token；非生产无需 token）：
  `py -3 .data-dev/engine/ddp.py deploy --ticket <T-xxxx> --comment "[已确认发布] 三道部署门已由用户确认：…"`
- 生产部署后工单流转待验收；非生产跳过流转。生成 `delivery_summary.md`；
  create/alter 项建议声明 `rollback_sql_file`，需要回滚时执行
  `ddp.py rollback --comment "[已回滚] ..."`。转场 P6：`advance --phase 6`。

## 第六步：P6 语义层更新 / 交付登记

- 若 `config.semantic.enabled=true`：**AI 不得自行决定进入语义层更新。** 先产出建议书
  （是否更新+判断逻辑+拟更新项），AskUserQuestion 请用户拍板（autopilot 不旁路）。
- 用户确认后：把拍板结果写入 `_user_confirmation.md`（含 `[已确认]`），再
  `py -3 .data-dev/engine/ddp.py semantic --ticket <T-xxxx> --scenario <metric-dev|feature-dev|cdm-dev>`
  产出 `semantic_update_summary.md`（无确认记录被 semantic_entry_gate 拦截）。
- 若 `semantic.enabled=false`：跳过语义层步骤，P6 仅作交付登记。
- 闭环：`py -3 .data-dev/engine/ddp.py finish --ticket <T-xxxx> --reason "…"`
  （生产流水线把工单逐级流转到已交付；非生产只记交付）。

## autopilot 模式

`--autopilot` 会把阶段转场、入口定级、方案选择、测试计划等“可返工判断”交给 AI 自主，
并把决策写入 `autopilot_decisions.md`；**生产发布/ALTER/回刷与语义层进入门永远 AskUserQuestion**
（非生产部署不需要发布确认 token）。
每次部署前必须向用户全文展示决策日志与审查报告。
