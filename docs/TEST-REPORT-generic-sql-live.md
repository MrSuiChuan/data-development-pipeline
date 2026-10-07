# 真实引擎联调报告：generic-sql（MySQL 8.4.4 接入）

**目的**：把矩阵里 generic-sql 的"未验证（按 MySQL 协议）"填实（台账 `CLOSE-010`）。
generic-sql 是"任何 MySQL 协议兼容库"的兜底 kind：**方言是 `generic`，只有基线 17 项规则**——
它和 `mysql`/`tidb` 的差别不在协议，而在"方言检查有多严"。

| 项 | 值 |
| --- | --- |
| 引擎 | MySQL **8.4.4**（本机镜像，容器 `ddp-mysql-generic`，宿主端口 23307） |
| kind / family / dialect | `generic-sql` / `dbapi`（pymysql 直连） / `generic` |
| 日期 | 2026-10-07 |
| 证据级别 | `local_receipt` |

## 0. 一句话结论

**真跑通过**：`ddl render → 建表 → 写入 3 行 → 读回 3 行 / 60.75 → 账本 → verify →
P3 出口门 FAIL=0（WARN=3）`。同时印证了 generic 的定位：**渲染出的 DDL 是最小形态**
（没有主键子句、没有引擎属性），因为 generic 方言只承诺基线 17 项，不做引擎专属改写。

## 1. 现场

```
$ docker run -d --name ddp-mysql-generic -p 23307:3306 -e MYSQL_ROOT_PASSWORD=ddp -e MYSQL_DATABASE=ods mysql:8.4.4
MySQL ready after ~51s

$ ddp.py platform doctor
{"canonical_kind": "generic-sql", "family": "dbapi", "dialect": "generic", "evidence": "local_receipt"}

$ ddp.py ddl render --spec spec.json        # generic：最小 DDL，不做 mysql 专属改写
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT,
  amt DECIMAL(18,2),
  ds DATE
);                                          # 真引擎接受（MySQL 8.4.4）

$ 建表 → 写入 3 行 → 读回：rows_inserted=3  amt_sum=60.75
$ 账本：query_id=run_ca927cb14b04c7 / run_20c21c97493ed4（engine=generic-sql，level=local_receipt）
$ ddp.py evidence verify --ticket T-LIVE → ok
$ 真实 P3 出口门：FAIL=0  WARN=3（无表注解 / local_receipt / 新表跳过 code_diff）
```

## 2. 这一项证明了什么、没证明什么

证明的：

- `generic-sql` 这条兜底通道在真库上能建表、能写入、能读回、证据能落账、P3 门能过；
- 它和 `mysql` kind 的行为差异被真实验证：**DDL 是最小形态**（generic 不做 MySQL 的
  `ENGINE=InnoDB`/主键内联改写），符合"只承诺基线 17 项"的定位。

没证明的：

- 没验证非 MySQL 协议的兼容库（generic 的契约本来就是 MySQL 协议族）；
- 没验证 generic 之外的方言规则（它本来就只有基线）；
- `local_receipt` 不等于平台 query_id。
