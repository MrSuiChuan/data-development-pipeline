# data-development-plugin 离线确认模式全流程测试计划

## 1. 测试对象与目标

- 插件版本：`data-development-plugin v1.4.0+codex.20261018104559`
- 测试模式：`mock` 离线模式
- 运行模式：`interactive`（确认模式）
- 目标：验证从初始化、建单、模式台账、工单 Goal、SDS 到 P6 收口的完整链路，以及关键硬门在缺失证据或确认时确实会阻断。
- 不在本次范围：真实平台连接、生产发布、真实 OpenViking 写入、真实业务数据质量结论。

## 2. 环境

| 项目 | 值 |
| --- | --- |
| 项目根 | `~\Documents\ChatGPT\data-development-plugin` |
| 插件根 | `~\.codex\plugins\cache\personal\data-development-plugin\1.4.0+codex.20261018104559` |
| 宿主 | Codex（Windows / PowerShell） |
| Python | `py -3`，Python 3.12.10 |
| 执行日期 | 2026-10-18（Asia/Shanghai） |

## 3. 确认策略

`interactive` 模式要求在 SDS、P3 任务注册、P4 测试计划、P5 生产不可逆门、P6 语义层入口等位置向用户确认。

本次是插件自身的离线回归测试：

- 正向链路使用带 `[TEST-FIXTURE]` 标记的测试夹具确认，只用于验证状态机能否继续，不代表生产签核；
- 台账中把 `fixture` 与 `user` 分开记录；
- 另设负向用例，验证“没有确认记录时门控必须阻断”；
- 不会把测试夹具确认写成真实用户确认，也不会在报告中声称做过生产发布。

## 4. 测试用例

| ID | 阶段 | 用例 | 预期结果 | 证据 |
| --- | --- | --- | --- | --- |
| TC-01 | setup | 同步引擎、初始化 mock 运行时、注入 AGENTS.md、检查 hook 启动器 | `.data-dev/engine/ddp.py` 存在，完整性自检通过，config.mode=mock，AGENTS.md 只注入一次，hook check 返回 0 | 命令输出、`.data-dev/config.json`、`.data-dev/engine/.engine-manifest.json` |
| TC-02 | submit | 先提交缺五章的坏工单，再提交合格工单 | 坏工单被 `submit_quality_gate` 拒绝；合格工单生成 T-xxxx | 两次命令输出、`_state.json` |
| TC-03 | preflight | 在 mode/goal 缺失时直接 start；随后选 interactive 并创建工单 goal | start 分别返回 `mode_choice_required` / `goal_required`；补齐后 start 通过并进入 P2 | `preflight_ledger.json`、`_mode.json`、`_goal.json`、`_goal.md`、start 输出 |
| TC-04 | P1 SDS | 进入 verify 模式，生成八条规则可验证的 SDS，渲染并尝试无确认发布；补充夹具确认后发布 | 无确认时 `sds validate --confirm` 被拦；有确认记录后发布终版 v1，`ticket verify-latest` 回读通过，bridge 生成 requirement_spec.md | `sds_design.json`、`verification/*`、工单评论、`requirement_spec.md` |
| TC-05 | P2 架构 | 产出 architecture_doc.md（含交付表 JSON 清单）与 pipeline.html，执行 P2 出口 | 交付表清单可解析且与 state.level 一致；P2 stage_gate 通过并转 P3 | `architecture_doc.md`、`pipeline.html`、advance 输出 |
| TC-06 | P3 研发 | 按 DDL → ETL → dp_task 顺序产出单表交付物，跑 SQL 17 项检查，执行 dev SQL 并保存证据 | 三目录同名同序；SQL 无零容忍项；`platform run-sql` 返回 SUCCESS + query_id；`evidence verify` 通过 | `ddl/`、`etl/`、`dp_task/`、`etl_dev_exec.json`、`executions.json` |
| TC-07 | P3 门控 | 生成 code_diff.html，执行 self_check、任务注册确认与 P3 出口 | code_diff 可见；`evidence selfcheck` 通过；P3 stage_gate 通过并转 P4 | `code_diff.html`、`_user_confirmation.md`、advance 输出 |
| TC-08 | P4 测试 | 产出测试计划与执行 SQL，记录 SUCCESS + query_id，写 test_runs.json / test_report.md，执行 P4 出口 | 每个 ✅ 用例都能在 test_runs.json 找到 SUCCESS + query_id，且 query_id 在执行账本中；P4 通过并转 P5 | `test_runs.json`、`test_report.md`、advance 输出 |
| TC-09 | P5 部署 | 以 dev 环境写 deploy_plan.json，执行确定性部署，产出 delivery_summary.md，执行 P5 出口 | 部署无需生产 token；deploy_log.json 有结果；delivery_summary.md 达门控长度；P5 通过并转 P6 | `deploy_plan.json`、`deploy_log.json`、`delivery_summary.md` |
| TC-10 | P5 负向 | 在 prod 环境缺少 `[已确认发布]` / `publish_confirmed` 时执行部署 | 部署被 publish_gate 阻断，不得产生成功回执 | 命令输出、`_state.json` |
| TC-11 | P6 语义层 | 先尝试无确认进入 semantic；补夹具确认后生成 semantic_update_summary.md 并 finish | 无确认时 `semantic_entry_gate` 阻断；有确认后摘要来自本单产物；finish 将状态置为已完成 | `semantic_update_summary.md`、`_state.json`、finish 输出 |
| TC-12 | 证据护栏 | 尝试 `ctx set verify_result` 写证据键、伪造 etl_dev_exec.json、写 `.data-dev/runtime` 账本 | 三类写入均被 `state_write_guard` / `evidence_ledger` / `runtime_guard` 阻断 | 命令输出、hook/gate 诊断 |
| TC-13 | 回归 | 运行插件自带 pytest 测试集 | 测试集通过（允许环境跳过项），无新增失败 | pytest 输出 |
| TC-14 | 收口 | 运行 status、status --gate、report，核对测试台账与真实产物 | 状态、门控、台账三处一致；所有 P0/P1 失败都有记录 | `status`、`governance_report.md`、测试台账 |

## 5. 通过标准

- TC-01 到 TC-14 全部有实际命令输出或机器文件作为证据；
- 正向链路最终 `finished=true`，`current_phase=7`；
- 任一硬门负向用例必须真的被阻断，不能只看代码或口头判断；
- 测试台账中的状态只能由实际结果驱动，未执行或未验证的项不得标 PASS；
- 报告必须区分 mock 证据、测试夹具确认和真实生产签核。

## 6. 预计风险

- mock 平台返回的 query_id 只能证明流程与账本闭环，不能证明真实 SQL 可执行；
- 当前仓库工作区存在未提交的 preflight 相关改动，测试针对的是这一份工作区代码，而非仅 Git HEAD；
- 插件 setup 设计上建议在数据项目根执行；本次为插件自测，项目根就是插件仓库，测试产物均在 gitignore 覆盖的 `.data-dev/` 下。
