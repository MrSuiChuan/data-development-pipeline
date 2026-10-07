# 真实引擎联调报告：Presto 0.299（PrestoDB）

**目的**：把矩阵里 presto 的"未验证"填实（台账 `CLOSE-007`）。presto 与 trino 共用方言规则，
但 PrestoDB 与 Trino 是两个发行版——这份报告给的是 **PrestoDB 0.299** 的现场。

| 项 | 值 |
| --- | --- |
| 引擎 | PrestoDB **0.299**（`0.299-7d50721`，单机 coordinator，Java 17） |
| 安装方式 | `presto-server-0.299.tar.gz`（Maven Central 的 746 MB 构件，经**华为云 Maven 镜像**下载） |
| connector | `memory`（`etc/catalog/memory.properties`），schema `ddp` |
| 端口 | 18080（HTTP statement API） |
| kind / family / dialect | `presto` / `command` / `trino`（共用 Trino 方言规则） |
| 日期 | 2026-10-07 |
| 证据级别 | `local_receipt` |

## 0. 一句话结论

**真跑通过**：`ddl render → 建表 → 写入 3 行 → 读回 3 行 / 60.75 → 账本 → verify →
P3 出口门 FAIL=0（WARN=3）`。真引擎同时给"DELETE 要看 connector 能力"这条 WARN 一个现场：
memory connector 直接回 `This connector does not support deletes`。

## 1. 现场

```
$ bin/launcher start            # 18 秒就绪
$ curl /v1/info → {"nodeVersion":{"version":"0.299-7d50721"},"coordinator":true}

$ ddp.py platform doctor
{"canonical_kind": "presto", "family": "command", "dialect": "trino", "evidence": "local_receipt"}

$ ddp.py ddl render --spec spec.json      # trino 方言：没有分区/属性时**不生成 WITH**（上一轮修过的点）
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE
);                                        # memory connector 直接接受

$ ddp.py platform run-sql --file ddl  --env dev --save-evidence --ticket T-LIVE   # 返回 result=True
$ ddp.py platform run-sql --file etl  --env dev --save-evidence --ticket T-LIVE   # 返回 rows=3
读回：rows_inserted=3  amt_sum=60.75
账本：query_id=run_1abb197e1df098 / run_36ce7e206f540d（engine=presto, level=local_receipt）
$ ddp.py evidence verify --ticket T-LIVE → {"ok": true, "source": "etl_dev_exec.json"}

真实 P3 出口门：FAIL=0  WARN=3（无表注解 / local_receipt / 新表跳过 code_diff）
```

## 2. 两条只有真机才会给的细节

| 现象 | 说明 |
| --- | --- |
| Presto 的 `/v1/statement` **不吃尾部加分号** | `SELECT 1;` → `mismatched input ';'`；`SELECT 1` → 正常。官方 CLI 会替你剥掉分号，**裸 HTTP 客户端要自己剥**（本次驱动脚本据此修正）；注释行不影响 |
| `DELETE` 取决于 connector | 静态规则是 WARN（"确认目标表的 connector 能力"），memory connector 的真实回答是 `ERROR: This connector does not support deletes`——规则的分级是对的，能不能删由 connector 决定 |

## 3. 这一项**没有**证明什么

- 没验证 Hive/JDBC/Delta 等真实 connector（本次只有 memory connector）；
- 没验证 PrestoDB 与 Trino 的行为差异（两边共用一套方言规则，属已知近似）；
- 没有官方 CLI（PrestoDB 未在 Maven Central 发布可执行 CLI），本次用 HTTP statement API 驱动，
  协议是真的、但"客户端"不是官方 CLI；
- `local_receipt` 不等于平台 query_id。

## 4. 取材过程（记一笔）

- `presto-server-0.299.tar.gz` 在 Maven Central 有（746 MB），但**直连连续 ranged 请求会被 429 限流**；
- **华为云 Maven 镜像**（`repo.huaweicloud.com/repository/maven`）实测 3.4 MB/s，746 MB 约 3 分钟拉完；
  阿里云 Maven 镜像 1.3 MB/s 可用，腾讯云 404；
- `presto-cli-0.299-executable.jar` 在 Maven Central **没有**（只有 javadoc/sources/main jar），
  所以驱动改走 HTTP statement API。
