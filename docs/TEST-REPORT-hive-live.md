# 真实引擎联调报告：Apache Hive 4.0

**目的**：Hive 是矩阵里"典型 command 族"的代表，也是方言规则里 FAIL 最集中的一档
（UPDATE/DELETE 非 ACID 不支持、PRIMARY KEY 不支持）。本报告把它的"未联调"填实。对应台账 LIVE-003。

**环境**：WSL2 Debian 13 / Docker 26.1.5 / `apache/hive:4.0.0`（单机 HiveServer2 + beeline）
端口 10000（HS2）｜**日期**：2026-09-29
**资源纪律**：起它前停掉 ddp-ch/ddp-trino/ddp-pg/ddp-my；跑完 `docker stop ddp-hive`

## 1. 模板缺陷（先修，再跑）

Hive 的分区列**只**写在 `PARTITIONED BY` 里。原来的渲染是"columns 里的列全进列定义 + 再补 PARTITIONED BY"，
于是同一个 `ds` 出现两次 → Hive 会报 `Duplicate column name`。

```
# 修之前（示意）
CREATE TABLE ods_orders_live (order_id BIGINT, amt DECIMAL(18,2), ds DATE)   -- ds 又在这里
PARTITIONED BY (ds STRING)                                                   -- 又在这里
```

修法：hive/odps 渲染时把分区列从列定义里移出，并给出一条说明性 WARN；
ClickHouse/PostgreSQL/Spark/Trino 保持"分区键必须是真实列"（缺列时给 WARN）。

```
# 修之后（真跑用的就是这份）
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2)
)
PARTITIONED BY (ds STRING)
STORED AS ORC;
```

## 2. 建表 → 写入 → 读回

```
$ ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"engine": "hive", "rc": 0, "evidence_level": "local_receipt", "query_id": "run_73270347aa238f"}

$ ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
# etl 内容是静态分区写入：INSERT INTO ods_orders_live PARTITION (ds='2026-09-28') VALUES (1,10.50),(2,20.25),(3,30.00);
{"engine": "hive", "rc": 0, "query_id": "run_da2abaa1674de0"}

$ docker exec ddp-hive beeline -u 'jdbc:hive2://localhost:10000/default' --outputformat=tsv2 \
    -e 'SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live'
3	60.75

$ docker exec ddp-hive beeline ... -e 'SHOW PARTITIONS ods_orders_live'
ds=2026-09-28

$ ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

## 3. 方言规则的真实现场

```
$ ddp.py platform run-sql --file update.sql --env dev
Error: Error while compiling statement: FAILED: SemanticException [Error 10294]:
  Attempt to do update or delete using transaction manager that does not support these operations.
```

`dialect.hive.no_row_write`（FAIL）**判断正确**：非 ACID 表连编译都过不去。
规则里给的修复建议（改成 `INSERT OVERWRITE` 重写分区）正是 Hive 的常规做法。

## 4. 真实 P3 出口门

```
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=hive）…
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

## 5. 踩到的环境坑（都写进 docs/engines/lab.md）

1. **不要用 `docker exec <容器> bash -lc "beeline …"`**：登入 shell 会重置 PATH，
   直接报 `beeline: command not found`。要么 `docker exec <容器> beeline …`（直接用镜像 ENV 的 PATH），
   要么把 beeline 的绝对路径写进 argv。
2. **容器会随 WSL 发行版重启而退出**：`docker start` 之后要等 HS2 真正就绪
   （判据是"能跑通一条查询"，不是容器 Up）。
3. beeline 会抱怨 `Failed to create directory: /home/hive/.beeline`（历史文件目录不可写）——
   只是噪音，不影响查询结果，但会污染日志，报告里要剔掉。

## 6. 结论与边界

**已证明**：Hive 4.0 单机上，修复后的建表模板可直接执行；静态分区写入 → 读回 **3 行 / 60.75**、
分区 `ds=2026-09-28` 可见；账本与 `evidence verify` 通过；**P3 出口门 FAIL=0**；
非 ACID 表的 UPDATE 被真引擎拒绝（Error 10294），FAIL 规则有真依据。

**未证明**：ACID 事务表上的行级写、Kerberos/Ranger 鉴权环境、Tez/Spark 执行引擎切换、
ORC 之外的文件格式（本例 `STORED AS ORC`）、动态分区写入。
