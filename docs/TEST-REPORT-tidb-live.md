# 真实引擎联调报告：TiDB v7.5.4

**目的**：把矩阵里 tidb 的"未验证"填实（台账 `CLOSE-006`），验收口径与其它引擎一致：
`doctor → ddl render → 建表 → 写入 → 读回一致 → 账本 → evidence verify → P3 出口门 FAIL=0`。

| 项 | 值 |
| --- | --- |
| 引擎 | `8.0.11-TiDB-v7.5.4`（`select version()` 回的 MySQL 兼容串） |
| 安装方式 | 官方单机镜像 `pingcap/tidb:v7.5.4`（526 MB，经 `docker.1panel.live` 镜像源） |
| 端口 | 4000（MySQL 协议） |
| kind / family / dialect | `tidb` / `dbapi`（pymysql 直连） / `mysql` |
| 日期 | 2026-10-07 |
| 证据级别 | `local_receipt` |

## 0. 一句话结论

**真跑通过**：`ddl render → 建表 → 写入 3 行 → 读回 3 行 / 60.75 → 账本 → verify →
P3 出口门 FAIL=0（WARN=3）`。同一个实例也给了"MySQL 协议族没有 `INSERT OVERWRITE`"这条
**FAIL 级方言规则**一个真引擎背书。

## 1. 现场

```
$ docker run -d --name tidb-live -p 4000:4000 pingcap/tidb:v7.5.4     # ready ~10s
$ mysql -h127.0.0.1 -P4000 -uroot -e "select version()"
8.0.11-TiDB-v7.5.4

$ ddp.py platform doctor
{"canonical_kind": "tidb", "family": "dbapi", "dialect": "mysql", "evidence": "local_receipt"}

$ ddp.py ddl render --spec spec.json      # tidb → mysql 方言：主键写在列定义里、ENGINE=InnoDB
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE,
  PRIMARY KEY (order_id)
)
ENGINE = InnoDB;                          # 真引擎接受（TiDB 忽略 storage engine）

$ ddp.py platform run-sql --file ddl --env dev --save-evidence --ticket T-LIVE   # affected_rows=0
$ ddp.py platform run-sql --file etl --env dev --save-evidence --ticket T-LIVE   # affected_rows=3
$ mysql -h127.0.0.1 -P4000 -uroot -D ods -e "SELECT count(*), sum(amt) FROM ods_orders_live"
rows_inserted	amt_sum
3	60.75

$ 账本：query_id=run_d1a20caa934182 / run_2f482fe69bc99b（engine=tidb, level=local_receipt）
$ ddp.py evidence verify --ticket T-LIVE → {"ok": true, "source": "etl_dev_exec.json"}

$ 真实 P3 出口门：
[WARN] sql.compliance.no_table_annotation / evidence.level.local_receipt / dev.code_diff.new_task
FAIL=0  WARN=3
```

## 2. 方言规则的真引擎背书

| 场景 | 静态检查（`sql check --dialect mysql`） | 真引擎（TiDB 7.5.4） |
| --- | --- | --- |
| `INSERT OVERWRITE ods_orders_live SELECT ...` | **FAIL** `dialect.mysql.no_insert_overwrite`（"MySQL 协议族没有 INSERT OVERWRITE"） | `ERROR 1064 (42000) ... near "ods_orders_live SELECT * FROM ..."` —— 真语法错 |

规则与真引擎一致：这条 FAIL 不是纸面推断。

## 3. 这一项**没有**证明什么

- 没验证生产形态（多 TiKV/PD 集群、事务隔离级别对 ETL 的影响、大表 DDL 的在线变更）；
- 单机镜像用的是内置 mock 存储（unistore），不是真实的 TiKV 集群；
- `kind=tidb` 目前按 MySQL 方言处理，TiDB 专有语法（如 `ADMIN`、聚簇索引细节）没有单独建模；
- `local_receipt` 不等于平台 query_id。

## 4. 取材过程（记一笔，省得下一个人重踩）

- PingCAP 官网域名 `download.pingcap.org` 在本网络**DNS 解析失败**；
- `docker.1ms.run` / `hub.rat.dev` / `dockerpull.org` 拉 `pingcap/tidb` 多次超时（层在动但没拉全），
  `m.daocloud.io` 对该命名空间 403；
- 最终 **`docker.1panel.live/pingcap/tidb:v7.5.4` 成功**（526 MB，可用作下次的首选源）。
