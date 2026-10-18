# data-development-plugin 离线确认模式全流程测试报告

## 结论

在 `mock` 离线模式、`interactive`（确认模式）下，插件主链路从 `ddp-setup` 跑到 `finish`，T-0001 最终 `phase=7`、`finished=true`、`completed=1..6`。

测试台账共 14 条：13 条通过，1 条失败。失败项不是主链路中断，而是收口诊断：已完成工单执行 `status --gate` 时返回 `gate.unsupported_phase` 的 FAIL。另发现 4 个需要修复的缺陷（D-01 到 D-04）和 1 个报告可读性问题（D-05）。

这次没有做真实生产发布。SDS、P3 注册、P4 测试计划、P6 语义层的确认动作都使用带 `[TEST-FIXTURE]` 标记的测试夹具；生产发布用负向用例验证了缺 `[已确认发布]` 会被 `publish_gate` 拦住。

## 用例结果

| ID | 阶段 | 结果 | 关键证据 |
| --- | --- | --- | --- |
| TC-01 | setup | PASS | engine sync、mock setup、AGENTS.md 注入、hook check rc=0 |
| TC-02 | submit | PASS | 坏工单 8 项质量门拒绝；T-0001 建单成功 |
| TC-03 | preflight | PASS | P-0001 interactive 台账、G-0001 goal；start 五步准入门通过 |
| TC-04 | P1 SDS | PASS | 八条规则通过；无确认被 `sds_confirm_gate` 拦；终版 v1 回读通过 |
| TC-05 | P2 架构 | PASS | L1 架构文档、交付表 JSON 清单、pipeline.html；P2 出口通过 |
| TC-06 | P3 研发 | PASS | 三目录同表；17 项检查通过；mock dev 执行 `q_61402c623ada34` |
| TC-07 | P3 门控 | PASS | code_diff.html、selfcheck、任务提交；P3 出口通过 |
| TC-08 | P4 测试 | PASS | 两条用例 SUCCESS + query_id，均在执行账本；P4 出口通过 |
| TC-09 | P5 dev 部署 | PASS | alter + backfill 成功；deploy_log 2 条；P5 出口通过 |
| TC-10 | P5 prod 负向 | PASS | 无 `[已确认发布]` 被 `publish_gate` 拦住，未产生新回执 |
| TC-11 | P6 语义层 | PASS | 无确认被 `semantic_entry_gate` 拦；有确认后生成摘要；finish 成功 |
| TC-12 | 证据护栏 | PASS | ctx 证据键、伪造回执、运行时账本写入均被拦 |
| TC-13 | pytest | PASS | 294 passed in 141.20s |
| TC-14 | 收口 | FAILED | `status --gate` 对 P7 返回 `gate.unsupported_phase` FAIL |

## 缺陷

### D-01（P1，阻断正常 SDS → start）

`sds bridge` 把 SDS 指纹写进 `sds.ticket_hash` 和 `state.ticket_hash`，`start` 第 4 步却拿它和工单正文的 `sha256(description_md)` 比。结果是正常走完 SDS 桥接后，`start` 仍报“工单正文 hash 与 SDS 落库 ticket_hash 不一致”。demo 通过手工重置 hash 绕开，说明这是真实路径上的语义冲突。

测试中先把两个 hash 归一为工单正文 hash，才继续进入 P2。该操作已作为测试夹具记录，不改变产品源码。

### D-02（P2，hook 误伤只读检查）

只读的 `Select-String` 因为模式字符串里出现 `run_sql` / `create_task` / `read_task`，被 `dataprocess_param_guard` 当成平台调用并拦截。命令本身没有执行任何平台动作。

### D-03（P2，静态 SQL 检查被读取门拦住）

新工单第一次跑 `ddp.py sql check` 时，DDL/ETL/dp_task 里包含 `ALTER`/`INSERT`，`backfill_guard` 要求目标表先有读取记录，于是把静态检查也拦了。测试中先补 `mark_read` 夹具，再跑三项检查，结果均为 `ok=true`、`issues=[]`。

### D-04（P3，完成态门控诊断误报）

`status --gate` 在 `phase=7` 时调用 `stage_gate(7)`，返回 `gate.unsupported_phase`、severity=`FAIL`。但 `status` 和 `governance_report.md` 都显示 `finished=true`，P6 出口在 finish 前也已无 blocker。建议对 P7 返回“已完成，无出口门”而不是 FAIL。

### D-05（P3，报告可读性）

`governance_report.md` 的部署证据显示 `env=-`、`engine=-`，而 `deploy_plan.json` 明确是 `dev`，执行回执是 `mock`。`deploy_log.json` 没有记录 env，报告因此读不到。

## 关键产物

- 测试计划：[offline-interactive-v1.4.0-test-plan.md](C:/Users/wuzongyun/Documents/ChatGPT/data-development-plugin/docs/testing/offline-interactive-v1.4.0-test-plan.md)
- 执行台账：[offline-interactive-v1.4.0-ledger.json](C:/Users/wuzongyun/Documents/ChatGPT/data-development-plugin/docs/testing/offline-interactive-v1.4.0-ledger.json)
- 工单状态：`.data-dev/tickets/T-0001/_state.json`
- 模式/Goal 台账：`.data-dev/runtime/preflight_ledger.json`
- 治理报告：`.data-dev/tickets/T-0001/governance_report.md`
- SDS：`.data-dev/tickets/T-0001/sds_design.json`
- 架构：`.data-dev/tickets/T-0001/architecture_doc.md`
- 测试证据：`.data-dev/tickets/T-0001/test_runs.json`、`test_report.md`
- 部署证据：`.data-dev/tickets/T-0001/deploy_log.json`、`delivery_summary.md`
- 语义层摘要：`.data-dev/tickets/T-0001/semantic_update_summary.md`
- 执行账本：`.data-dev/runtime/executions.json`
- 回刷账本：`.data-dev/runtime/backfill_runs.json`

## 限制

- mock 平台的 query_id 只证明流程、账本和门控闭环，不证明真实引擎能执行这些 SQL；
- 测试夹具确认不等于真实用户在生产门上的签核；
- 本次没有执行生产发布、生产 ALTER 或生产回刷；
- 本次针对当前工作区代码测试，工作区里已有 preflight 相关未提交改动，结果不等同于只测 Git HEAD；
- 插件 setup 的说明建议不要在插件仓库里执行，本次为插件自测在仓库根执行，运行产物都在 `.data-dev/`（gitignore 覆盖）。
