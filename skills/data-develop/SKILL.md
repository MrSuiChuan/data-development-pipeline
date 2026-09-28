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

## 引擎与表格式（多引擎兼容）

同一份 SQL 在不同引擎上**成立与否不同**，所以检查分两层：基线 17 项跨引擎恒成立；
方言与湖表格式的规则只在该引擎/格式下叠加（`engine/core/dialects.py`、`engine/core/formats.py`）。

- 方言由 `platform.kind` 推出，也可显式指定：`sql check --file … --dialect hive`。
  FAIL 例：Hive/ODPS/Flink 的行级写、Trino 与 MySQL 协议族的 `INSERT OVERWRITE`。
  WARN 例：Doris/StarRocks 的键模型限制、Spark 的行级写与整表覆盖、PostgreSQL 的 `ALTER TYPE`/`TRUNCATE`。
- 湖表格式由 `platform.format` 推出（`--format` 可显式指定）：Hudi 缺 `primaryKey`、
  Delta 的 `VACUUM`/`overwriteSchema`、Iceberg 的行级 `DELETE`、Paimon 缺 `bucket` 都是 WARN + 修复建议。
- 配完引擎先体检：`platform doctor`（kind 是否登记、必填键、profiles 形状、明文口令、kind×format 组合）。
  未登记的 kind/组合一律报错并列出可用项——**不要**按"差不多能跑"往下写。
- 建表不要凭记忆抄类型：`ddl render --spec <json> [--dialect <id>] [--format <id>]` 出该引擎的
  起始模板（含类型映射与注释/键/属性语法差异）；渲染产物只是模板，仍须过 `sql check` 与真实执行。
- dp_task 的调度注解按 `config.scheduler.kind` 校验：`cron`（默认）只要非空
  `-- schedule:`；`airflow` / `dolphinscheduler` 还要 `-- schedule-id:`（建议带
  `-- schedule-timezone:`，否则按调度器默认时区解释）；`dataworks` 建议带 `-- schedule-depends:`；
  纯执行引擎（`none`）不要求调度注解。缺必填注解是 FAIL，缺建议注解是 WARN。

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
