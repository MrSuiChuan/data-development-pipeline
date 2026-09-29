# 真实引擎联调报告：Spark SQL 4.1.3（local 模式）

**目的**：Spark 是湖表格式（LIVE-005）的宿主，也是"spark 方言规则"的验证对象
（行级写只对湖表成立、`INSERT OVERWRITE` 不带 PARTITION 覆盖整表）。对应台账 LIVE-004。

**环境**：WSL2 Debian 13 / OpenJDK 21 / `~/oss/spark-4.1.3-bin-hadoop3`（USTC 镜像，547 MB 包）
`--master local[2]`，warehouse 指向项目目录｜**日期**：2026-09-29
**资源纪律**：Spark 跑在宿主（不起容器），跑之前已停掉 ddp-hive/ddp-ch/ddp-trino/ddp-pg/ddp-my

## 1. 配置（argv 就是官方 spark-sql）

```json
{"kind": "spark",
 "command": {"engine": "spark",
   "run": ["~/oss/spark-4.1.3-bin-hadoop3/bin/spark-sql", "--master", "local[2]",
           "--conf", "spark.ui.enabled=false", "--conf", "spark.driver.memory=1g",
           "--conf", "spark.sql.warehouse.dir=<项目>/warehouse", "-f", "{file}"],
   "timeout": 900}}
```

**踩到的坑**：发行包解压目录带 `-bin-hadoop3` 后缀（`spark-4.1.3-bin-hadoop3`），
不是 `spark-4.1.3`——写错路径的表现是 `spark-sql: No such file or directory`。

## 2. 建表 → 写入 → 读回

```
$ ddp.py ddl render --spec spec.json --out .../ddl/ods_orders_live.sql
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE
)
USING parquet;                       -- 未指定 format 时按 parquet 占位（并给出 WARN）

$ ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
# 首轮耗时 410s（Spark 冷启动 + derby metastore 初始化），rc=0，query_id=run_…

$ ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
# INSERT INTO ... VALUES（三行），rc=0

$ ~/oss/spark-4.1.3-bin-hadoop3/bin/spark-sql --master local[2] \
    -e "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live"
3	60.75

$ ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

## 3. 方言规则的真实现场

```
$ ddp.py platform run-sql --file delete.sql --env dev
[UNSUPPORTED_FEATURE.TABLE_OPERATION] The feature is not supported:
  Table `spark_catalog`.`default`.`ods_orders_live` does not support DELETE.
  SQLSTATE: 0A000
```

`dialect.spark.row_write_needs_lakehouse`（WARN）**描述准确**：普通 parquet 表根本不做行级写，
要用 `DELETE/MERGE` 得换成 Delta/Hudi/Iceberg 这类支持行级操作的湖表——这正是 LIVE-005 要验的。

## 4. 真实 P3 出口门

```
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=spark）…
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

## 5. 性能与边界（如实记录）

- **冷启动很贵**：首个 `spark-sql -f` 用了约 410s（JVM + metastore + 会话初始化），
  后续语句 20-40s。真跑预算要按分钟级给，别拿 ClickHouse 的秒级经验套。
- 未验证：YARN/K8s 提交模式、`spark-sql --master yarn`、分区表的 `INSERT ... PARTITION` 语义、
  Thrift Server（JDBC）路径、Spark 与 Hive metastore 共用。
