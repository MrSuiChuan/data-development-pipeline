---
name: data-develop
description: P3 数据研发方法论：自底向上开发、DDL→ETL→dp_task 顺序、17 项 SQL 检查、真跑证据、code_diff
---

# data-develop（P3）

按架构表清单自底向上开发：适配层 → 逻辑层 → 接口层 → 核对层。每张表固定三段：
`ddl/<table>.sql` → `etl/<table>.sql` → `dp_task/<table>.sql`（dp_task 为 P5 部署用，禁止再改）。

## 硬顺序与难度

- dev_order_guard：ETL 必须等 DDL 落盘，dp_task 必须等 ETL 落盘；
- difficulty_guard：L0/L1 禁建表（禁 CREATE TABLE），L3 文件须带分层前缀；
- 周期任务（dp_task）禁 ALTER：DDL 变更留在 ddl/。

## 每份 SQL 的 17 项检查（sql_compliance_check）

9 条零容忍：目标表/文件名一致、周期任务禁 ALTER、禁无 WHERE 的 UPDATE/DELETE、禁硬编码生产库、
语法护栏（括号/引号配对）、写表前 DDL 存在、禁 SELECT *、dp_task 带调度注解、带 -- source: 血缘。
8 条软提醒：WHERE 过滤、超长行、分号、CTE、聚合、-- table: 注解、分区字段、多语句。

## 证据纪律

- 每张表必须在**开发环境**真跑，把返回的 query_id 落 `etl_dev_exec.json`（SUCCESS+env=dev）；
- 用 `platform run-sql --file <etl/*.sql> --env dev --save-evidence` 真跑并让引擎
  自动写 `etl_dev_exec.json` + 执行账本；再执行 `evidence verify`（交叉核对
  query_id 在账本中才写 `verify_result=passed`；禁止手写证据文件）。
- 改现有任务必须 `ddp diff` 生成 code_diff.html 并给用户看（L1/L2）；
- self_check 通过后执行 `evidence selfcheck` 设 `self_check_result=passed`；
  AskUserQuestion 后把确认写进 `_user_confirmation.md` 再 `evidence confirm`；
- 之后才允许提交/发布（submit_guard 硬门消费这些键）。证据键禁止 `ctx set` 自写；
  有调度器的平台才注册任务，纯执行平台（MySQL/Doris/Paimon…）只做代码快照。

不要写“应该能跑”：跑不出来就没有 query_id，没有 query_id 就过不了 P3 出口。
