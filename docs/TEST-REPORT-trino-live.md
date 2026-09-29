# 真实引擎联调报告：Trino 476

**目的**：Trino 的建表语法与 Spark 不同（属性写在 `WITH (...)` 里），上一轮只按文档写了模板、
没有真跑过。本报告把它填实。对应台账 LIVE-002。

**环境**：WSL2 Debian 13 / Docker 26.1.5 / `trinodb/trino:476`（宿主 Java 21 跑官方 CLI）
memory connector（`/etc/trino/catalog/memory.properties` = `connector.name=memory`）｜**日期**：2026-09-29
**资源纪律**：起它前停掉 ddp-ch/ddp-pg/ddp-my；跑完 `docker stop ddp-trino`

## 1. 客户端与就绪判断（两个坑）

1. **官方 server 镜像里没有 CLI**：`/usr/lib/trino/bin/trino` 不存在（`OCI runtime exec failed`）。
   改为下载官方 `trino-cli-476-executable.jar`（阿里云 Maven 镜像可用；USTC maven-central 404），
   宿主 Java 21 直接跑：

   ```json
   {"kind": "trino",
    "command": {"engine": "trino",
      "run": ["java", "-jar", "<repo>/.tmp/live-trino/trino-cli.jar",
              "--server", "http://127.0.0.1:8080", "--catalog", "memory", "--schema", "default",
              "--file", "{file}"]}}
   ```

2. **就绪判断不能只看 `/v1/info`**：它在 coordinator 还在初始化时就返回 200，此时提交 DDL 得到
   `Trino server is still initializing`。判据必须是"能跑通一条真查询"（脚本里改成循环跑
   `--execute "SELECT 1"`，实测 60s 就绪）。

## 2. 模板缺陷（真跑揪出来的）

第一轮真跑时模板生成的是：

```
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE
)
WITH (
  format = 'PARQUET'
);
```

真 Trino 直接拒绝：

```
Catalog 'memory' table property 'format' does not exist
LINE 7:   format = 'PARQUET'
```

**判定为模板缺陷并修掉**：Trino 的 `WITH (...)` 键**由 connector 定义**，
`format` 只是 Hive connector 的文件格式键，不是通用属性。现在：

- 没有显式 `properties`、也没有 `partition_by` → **不生成 WITH 子句**（用 connector 默认）；
- 有 `partition_by` → 生成 `partitioned_by = ARRAY[...]`，并 WARN "这是 connector 属性（Hive/Iceberg 认，memory/JDBC 不认）"；
- `format` 非 none 时 WARN "表格式由 catalog/connector 决定，DDL 里不生成 format"。

修完之后的模板与真跑结果：

```
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE
);

$ ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_f27b30450a1c44", "engine": "trino", "evidence_level": "local_receipt"}
```

## 3. 写入 → 读回 → 证据

```
$ ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_43491be9d08594", "engine": "trino"}

$ java -jar trino-cli.jar --execute "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live"
"3","60.75"

$ cat .data-dev/runtime/executions.json
  query_id=run_f27b30450a1c44 engine=trino evidence_level=local_receipt sql_hash=3d6b559ba598
  query_id=run_43491be9d08594 engine=trino evidence_level=local_receipt sql_hash=940b00774200
$ ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

## 4. 方言规则的真实现场

```
$ ddp.py platform run-sql --file overwrite.sql --env dev
Query ... failed: line 4:8: mismatched input 'OVERWRITE'. Expecting: 'INTO'
```

`dialect.trino.no_insert_overwrite`（FAIL）**判断正确**：真 Trino 连语法都不认。
另外这两次失败也顺手验证了命令族的成败判据——**Trino CLI 查询失败时退出码是 1**，
插件据此报错（而不是把失败当成功）。

## 5. 真实 P3 出口门

```
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=trino）…
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

## 6. 结论与边界

**已证明**：Trino 476 + memory connector 上，插件渲染的建表模板可执行（修掉 `format` 缺陷后），
写入/读回（3 行 / 60.75）/账本/verify/P3 出口门全通。Trino 的"没有 INSERT OVERWRITE"与
"WITH 属性属于 connector"两条认知都被真引擎证实。

**未证明**：Hive/Iceberg/Delta connector 上的 `partitioned_by` 与文件格式属性、
Trino 对 Hive ACID 表的 UPDATE/DELETE 支持、集群模式（本例是单节点 coordinator）。
