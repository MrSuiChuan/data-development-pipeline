# 真实引擎联调报告：ClickHouse 24.8

**目的**：把矩阵里 clickhouse 的"未联调"填实——真起一个实例，跑通
"建表 → 写入 → 读回 → 证据落账 → evidence verify → P3 出口门"。对应台账 LIVE-001。

**环境**：WSL2 Debian 13 / Docker 26.1.5 / `clickhouse/clickhouse-server:24.8`（实测 24.8.14.39）
端口 9000（native）/ 8123（HTTP）｜**日期**：2026-09-29
**资源纪律**：起它之前已 `docker stop ddp-pg ddp-my`；跑完立刻 `docker stop ddp-ch`

## 1. 为什么客户端放在容器里

宿主没装 `clickhouse-client`，所以 argv 走容器内客户端，并把项目目录按**同一路径**挂进容器
（插件写的 SQL 临时文件要能被客户端读到）：

```json
{
  "kind": "clickhouse",
  "command": {
    "engine": "clickhouse",
    "run": ["docker", "exec", "-i", "-e", "CLICKHOUSE_PASSWORD=ddp", "ddp-ch",
            "clickhouse-client", "--multiquery", "--queries-file", "{file}"]
  }
}
```

> 踩到的坑：**先建项目目录再挂载**。反过来的话 Docker 会以 root 创建挂载点，
> 后面写 SQL 文件就是 `PermissionError`（这一点已写进 `docs/engines/lab.md`）。
> 口令走环境变量（`CLICKHOUSE_PASSWORD`），不放 argv。

## 2. 配置体检

```
$ ddp.py platform doctor
{"ok": true, "kind": "clickhouse", "family": "command", "dialect": "clickhouse",
 "evidence": "local_receipt", "default_port": 9000,
 "warnings": ["该执行族拿不到服务端 query_id：证据级别是 local_receipt…"]}
```

## 3. 建表（模板直接用，没改一个字）

```
$ ddp.py ddl render --spec spec.json --out .../ddl/ods_orders_live.sql
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id Int64 COMMENT '订单号',
  amt Decimal(18, 2),
  ds Date
)
ENGINE = MergeTree
PARTITION BY ds
ORDER BY (order_id);

$ ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_ee1228ee157be6", "engine": "clickhouse", "evidence_level": "local_receipt"}
```

`Int64` / `Decimal(18, 2)` / `Date` 这三处正是类型映射表给出的物理类型——**真引擎接受了**。

## 4. 写入 → 读回 → 证据

```
$ ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_2cf61951465ae5", "engine": "clickhouse"}

$ docker exec ddp-ch clickhouse-client --password ddp --query \
    "SELECT count() AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live"
3	60.75

$ cat .data-dev/runtime/executions.json
  query_id=run_ee1228ee157be6 engine=clickhouse evidence_level=local_receipt sql_hash=ae8d714ac08f
  query_id=run_2cf61951465ae5 engine=clickhouse evidence_level=local_receipt sql_hash=a037edf10fd5
$ ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

## 5. 两条方言规则的真实现场

| 规则 | 真实 ClickHouse 的反应 | 结论 |
| --- | --- | --- |
| `dialect.clickhouse.update_from`（FAIL：不支持 `UPDATE ... FROM`） | `SYNTAX_ERROR`，退出码 62，错误正文列出允许的语句类型 | **规则正确**：这条 FAIL 不是纸面判断 |
| `dialect.clickhouse.mutation_async`（WARN：ALTER…UPDATE 是异步 mutation） | 语句 `rc=0` 立即返回；`SELECT count() FROM system.mutations WHERE is_done=0` → **0** | **规则是有条件的**：小表上 mutation 秒级完成。规则说的是"不等待就返回 + 大表开销"这个风险，属 WARN 而非 FAIL —— 与真跑一致 |

## 6. 真实 P3 出口门

```
$ bash .tmp/live-common/p3_gate.sh <项目> ods_orders_live
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=clickhouse）…
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

## 7. 结论与边界

**已证明**：ClickHouse 24.8 上，插件渲染的建表模板可直接执行；写入/读回/账本/核验/P3 出口门全通
（FAIL=0）。类型映射（Int64/Decimal/Date）与方言规则都得到真引擎背书。

**未证明**：`Distributed`/`Replicated` 表引擎、`mutations_sync` 与大表 mutation 的实际等待、
HTTP 接口（8123）路径、权限模型（本例用 default 用户）。
