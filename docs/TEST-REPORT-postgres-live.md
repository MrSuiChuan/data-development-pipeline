# 真实引擎联调报告：PostgreSQL 16

**目的**：证明插件在**真引擎**上能完成"建表 → 写入 → 读回 → 证据落账 → 过证据核验"，
而不是只在 mock 里自洽。对应台账 COMPAT-009。

**环境**：WSL2 Debian 13 / Docker 26.1.5 / `postgres:16` 容器（端口 55432，库 `ods`）
**日期**：2026-09-28 ｜ **脚本**：`.tmp/live-pg/run.sh`（仓库 .tmp 不入库，日志 `out.log`）
**配置**：`platform.kind=postgres`，command 族，argv = `psql -v ON_ERROR_STOP=1 -h {host} -p {port} -U {user} -d {database} -f {file}`

## 1. 配置体检

```
$ python3 engine/ddp.py --root $TT platform doctor
{
  "ok": true, "kind": "postgres", "family": "command", "dialect": "postgres",
  "evidence": "local_receipt", "default_port": 5432,
  "warnings": ["该执行族拿不到服务端 query_id：证据级别是 local_receipt，只能证明『这条 SQL 在本机执行过』，不能等价于平台 query_id"]
}
```

## 2. 建表（按 postgres 方言渲染 → 真跑）

```
$ python3 engine/ddp.py ddl render --spec spec.json --out .../ddl/ods_orders_live.sql
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT,
  amt NUMERIC(18,2),
  ds DATE
)
PARTITION BY RANGE (ds);
CREATE TABLE IF NOT EXISTS ods_orders_live_default PARTITION OF ods_orders_live DEFAULT;
COMMENT ON COLUMN ods_orders_live.order_id IS '订单号';

$ python3 engine/ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_e541fa4f6cbbb8", "engine": "postgres",
 "evidence_level": "local_receipt", "duration_s": ..., "env": "dev"}
```

## 3. 写入与读回

```
$ python3 engine/ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_1cbce4f8b326ef", "engine": "postgres", "evidence_level": "local_receipt"}

$ psql -h 127.0.0.1 -p 55432 -U postgres -d ods -c "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;"
 rows_inserted | amt_sum
---------------+---------
             3 |   60.75
(1 row)
```

## 4. 证据链

```
$ cat .data-dev/runtime/executions.json
  query_id=run_e541fa4f6cbbb8 env=dev engine=postgres sql_hash=0b6e0ccac290 status=SUCCESS
  query_id=run_1cbce4f8b326ef env=dev engine=postgres sql_hash=d29a2424ff9a status=SUCCESS

$ python3 engine/ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

回执里的 `query_id` 是本机回执（`local_receipt`，psql 不回服务端 query id），
但**它在引擎执行账本里**——`evidence verify` 的交叉核对正是为了堵"手写证据文件"。

## 5. 失败案例（都来自这次实跑）

| 现象 | 现场 | 处置 |
| --- | --- | --- |
| 建表被自己的门拦下 | `sql_compliance FAIL: sql.compliance.no_lineage（缺 -- source:）` | **判定为误报并修掉**：第 9 条只该管搬数据的语句（INSERT/CTAS），纯 `CREATE TABLE` 没有上游概念 |
| 写入报 `no partition of relation found for row` | 渲染出的 `PARTITION BY RANGE (ds)` 没有分区可落 | **判定为模板缺陷并修掉**：PostgreSQL 声明式分区必须显式建分区，模板现在附带一个 `DEFAULT` 分区并出 WARN 提示换成真实分区 |
| 语法错 | `SELECT FROM WHERE;` → `psql 退出码 3: syntax error at or near "WHERE"` | 正确：命令族按退出码判成败，真实报错原样抛出 |
| 方言不匹配 | 同一份 `INSERT OVERWRITE` 丢给 `--dialect mysql` → `dialect.mysql.no_insert_overwrite` FAIL | 正确：跨引擎不是"换个 kind 就行"，规则会拦 |

## 6. 真实 P3 出口门（补齐 dp_task + 架构机读块 + 证据键后跑）

```
$ python3 ... stage_gate(3, req, state)    # 引擎里 P3 出口门逐条 validator
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=postgres）：只证明这条 SQL 在本机执行过，没有平台侧 query_id 可回查
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

也就是说：**真引擎上的建表/写入证据被 P3 出口门接受（FAIL=0）**，剩下的三条 WARN 都是如实标注
（缺 `-- table:` 注解、证据级别是本机回执、新表无线上基准）。

这一轮跑门又揪出两个缺陷（都已修 + 加用例）：

| 现象 | 现场 | 处置 |
| --- | --- | --- |
| 所有 `IF NOT EXISTS` 建表都被判「文件名与目标表不一致」 | 目标表解析出的是 `IF` 而不是表名 | **正则修掉**：`CREATE TABLE [IF NOT EXISTS] name`、`INSERT [OVERWRITE\|INTO] [TABLE] name`（`TABLE` 可省略） |
| 证据文件里看不出级别 | `--save-evidence` 写的 `etl_dev_exec.json` 只存 status/query_id/env/executed_at | **补上** `evidence_level` 与 `engine`：门与治理报告都能看到"这是本机回执" |

## 7. 结论与边界

**已证明**：PostgreSQL 16 上建表/写入/读回/证据落账/证据核验整条链路可用；两条缺陷（血缘误报、分区模板）
是这次真跑找出来的，已修并加回归用例；补齐 dp_task/架构/证据键后，**P3 出口门在真引擎项目上 FAIL=0**。

**未证明**：Kerberos/SSL 环境的连接参数、Greenplum 专有语法（分布键/资源组）、大表分区维护实践；
P4/P5/P6 出口门没在真引擎上跑（P4 需要测试用例、P5 需要发布确认），
它们由本机 mock 端到端覆盖（`tests/test_engines.py::MultiEngineEndToEndTests`）。
