---
name: data-testing
description: P4 数据测试方法论：用例计划、机器证据 test_runs.json、✅↔query_id 一一对应
---

# data-testing（P4）

## 计划

用例覆盖：主键唯一、空值、口径抽数比对、分区边界、下游抽样。数量下限按难度
（L1>=1 / L2>=15 / L3>=25）只是 WARN；**证据缺失才是硬 FAIL**。

## 执行

用 `data-development-plugin/phase4-test` workflow 并行调度 `data-tester` agents：

- workflow schema 强制每个结果必须带非空 query_id；没有就 FAILED；
- 汇总 `runs` 原样写 `test_runs.json`；报告里 ✅ 只能标有 SUCCESS+query_id 的 TC；
- P4 validator 会把每个 SUCCESS 的 query_id 与 `.data-dev/runtime/executions.json`
  交叉核对（存在账本时），不在账本的 query_id 视为伪造 → FAIL；
- 任一用例 FAILED → P4 出口拒绝（test_runs.json 有 FAILED 记录即 FAIL）；
- 测试前必须已有 `etl_dev_exec.json` status=SUCCESS（前置门）。

## 诚实规则

报告标 ✅ 的每个 TC 都会被校验器到 test_runs.json 里反查。写报告 ≠ 跑测试；
你最该防的是自己偷懒“只写报告”。一个没跑的用例要明说没跑，而不是标 ✅。
