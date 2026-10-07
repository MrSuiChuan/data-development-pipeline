# 真实引擎联调报告：Flink 1.20.3 + Paimon 1.4.1

**目的**：把矩阵里 flink / paimon 两行的"插件侧未真跑"填实（台账 `CLOSE-004`），
验收口径与其它引擎一致：`doctor → ddl render → 建表 → 写入 → 读回一致 → 账本 → evidence verify → P3 出口门 FAIL=0`。

| 项 | 值 |
| --- | --- |
| 引擎 | Apache Flink **1.20.3**（官方 `flink-1.20.3-bin-scala_2.12.tgz`，461 MB，Apache 官方下载站） |
| 湖表格式 | Apache Paimon **1.4.1**（`paimon-flink-1.20-1.4.1.jar` 放进 `flink/lib`） |
| 附加依赖 | `flink-shaded-hadoop-2-uber-2.8.3-10.0.jar`（Paimon 的 filesystem catalog 要 Hadoop 的 FileSystem 类，Flink 1.20 不再自带） |
| JDK | 17（Flink 1.20 不支持本机的 JDK 21） |
| 运行方式 | `sql-client.sh -i <init> -f <file>`，`execution.target=local`（不起独立集群） |
| 日期 | 2026-10-07 |
| 证据级别 | `local_receipt`（command 族） |

## 0. 一句话结论

**真跑通过**：`ddl render → 建表 → 写入 3 行 → 读回 3 行 / 60.75 → 账本 → verify → P3 出口门 FAIL=0（WARN=5）`。
真引擎同时暴露 **4 条只有在真机上才会撞到的接线问题**（第 2 节），都已写进
[docs/engines/flink.md](engines/flink.md) 的接入页。

## 1. 链路现场

```
$ ddp.py platform doctor      # kind=flink, format=paimon, family=command, evidence=local_receipt
$ ddp.py ddl render --spec spec.json
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE,
  PRIMARY KEY (order_id) NOT ENFORCED
)
WITH ('connector'='paimon');

$ ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --save-evidence --ticket T-LIVE
  rc=0  duration_s=27.857   →  [INFO] Execute statement succeeded.
$ ddp.py platform run-sql --file .../etl/ods_orders_live.sql --save-evidence --ticket T-LIVE
  rc=0  duration_s=23.969   →  Job ID: 44fde7d44d0ccf4475a837ef3c07c8f8（INSERT 提交成功）

$ 读回（走插件通道）        |             3 |   60.75 |
$ 读回（绕开插件，另起 sql-client 直接查 Paimon 表）
                            |             3 |   60.75 |     ← 证明数据真的落盘
$ ddp.py evidence verify --ticket T-LIVE → {"ok": true, "source": "etl_dev_exec.json"}
$ 账本：query_id=run_38608891ef6c2b / run_b890b3dd5e3681 / run_2057bcf465ba36（engine=flink, level=local_receipt）
$ 真实 P3 出口门：
[WARN] sql.compliance.no_table_annotation / dialect.flink.primary_key_needs_upsert /
       format.paimon.missing_bucket / evidence.level.local_receipt / dev.code_diff.new_task
FAIL=0  WARN=5
```

Paimon 的 schema 确实落在仓库目录里：`<warehouse>/ods.db/ods_orders_live/schema`。

## 2. 四条真机才暴露的接线问题（都写进了接入页）

| # | 现象（原文） | 处置 |
| --- | --- | --- |
| 1 | `sql-client.sh -f` **出错也返回 0**：SQL 报 `Object '不存在的表' not found`、结果模式配错，进程退出码都是 0 | argv 包一层：把输出重定向到日志，`grep -q '\[ERROR\]'` 命中就返回非零。不包的话插件会把失败的 SQL 记成 SUCCESS —— **假证据** |
| 2 | `Could not execute SQL statement: java.lang.ClassNotFoundException: org.apache.hadoop.conf.Configuration` | Paimon 的 filesystem catalog 要 Hadoop 类：把 `flink-shaded-hadoop-2-uber-2.8.3-10.0.jar` 放进 `flink/lib` |
| 3 | `java.net.ConnectException: Connection refused`（写入时） | SQL client 默认 `execution.target=remote`，要独立集群；单机联调用 `SET 'execution.target' = 'local';` |
| 4 | 非交互模式跑 `SELECT` 报 `In non-interactive mode, it only supports to use TABLEAU as value of sql-client.execution.result-mode` | init 文件里补 `SET 'sql-client.execution.result-mode' = 'TABLEAU';` |

另外一条建模事实：**通用 connector 形式（`WITH ('connector'='paimon','path'=…)`）不会创建物理表**——
写入时报 `Schema file not found in location … Please create table first.`；Paimon 表要通过 Paimon catalog
（`CREATE CATALOG paimon WITH ('type'='paimon','warehouse'=…)` + `USE CATALOG paimon`）建。
插件的模板生成的 `WITH ('connector'='paimon')` 在 catalog 上下文里被接受（实测），
所以接入页给的 init 文件模板里必须带 `CREATE CATALOG paimon`。

## 3. 这一项**没有**证明什么

- 没验证流式/持续作业（本次按批模式跑，作业提交后即结束；CDC、checkpoint、状态恢复都没碰）；
- 没验证 Hive catalog / S3 / OSS 等远端仓库形态，只验证了本地 filesystem catalog；
- 没验证 Flink on YARN/K8s、细粒度资源与并行度调优；
- Flink 1.20 只对 JDK 17 背书（本机 JDK 21 未试）；
- `local_receipt` 不等于平台 query_id（doctor 的告警原样保留）。

## 4. 复现命令（要点）

```bash
# ① 取件（Apache 官方站点 / Maven Central，本网络都很快）
curl -O https://dlcdn.apache.org/flink/flink-1.20.3/flink-1.20.3-bin-scala_2.12.tgz && tar xzf …
curl -O https://repo1.maven.org/maven2/org/apache/paimon/paimon-flink-1.20/1.4.1/paimon-flink-1.20-1.4.1.jar
curl -O https://repo1.maven.org/maven2/org/apache/flink/flink-shaded-hadoop-2-uber/2.8.3-10.0/flink-shaded-hadoop-2-uber-2.8.3-10.0.jar
cp paimon-flink-1.20-1.4.1.jar flink-shaded-hadoop-2-uber-2.8.3-10.0.jar flink-1.20.3/lib/

# ② init 文件（每次执行都带 -i，保证 catalog 在）
#    SET 'execution.target'='local'; SET 'execution.runtime-mode'='batch';
#    SET 'sql-client.execution.result-mode'='TABLEAU';
#    CREATE CATALOG paimon WITH ('type'='paimon','warehouse'='file://<本地目录>');
#    USE CATALOG paimon; CREATE DATABASE IF NOT EXISTS ods; USE ods;

# ③ 插件侧 argv（关键是包一层，别让错误 SQL 记成 SUCCESS）
#    ["bash","-lc","<flink>/bin/sql-client.sh -i <init> -f {file} > /tmp/x.log 2>&1; rc=$?;
#      grep -q '\\[ERROR\\]' /tmp/x.log && rc=1; tail -40 /tmp/x.log; exit $rc"]

# ④ 项目配置：kind=flink + format=paimon；spec 里可给 properties（例如 paimon 的 bucket）
python3 engine/ddp.py --root <项目> platform doctor
python3 engine/ddp.py --root <项目> ddl render --spec <项目>/spec.json --out <项目>/…/ddl/ods_orders_live.sql
python3 engine/ddp.py --root <项目> platform run-sql --file <ddl> --env dev --save-evidence --ticket T-LIVE
python3 engine/ddp.py --root <项目> platform run-sql --file <etl> --env dev --save-evidence --ticket T-LIVE
python3 engine/ddp.py --root <项目> evidence verify --ticket T-LIVE
```
