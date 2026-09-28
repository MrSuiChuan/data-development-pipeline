# PostgreSQL / Greenplum 接入

| 项 | 值 |
| --- | --- |
| kind | `postgres`（`greenplum` 同方言） |
| family | `command` |
| dialect | `postgres` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt` |
| 验证状态 | 未在真实 PostgreSQL/Greenplum 上联调 |

## 配置示例

```json
{
  "platform": {
    "kind": "postgres",
    "command": {
      "engine": "postgres",
      "run": ["psql", "-v", "ON_ERROR_STOP=1", "-f", "{file}"],
      "timeout": 900
    }
  }
}
```

**`ON_ERROR_STOP=1` 不能省**：psql 默认遇到报错继续执行并返回 0，
那会把"SQL 失败"记成"执行成功"——命令族是按退出码判成败的。
连接信息走 `PGHOST/PGDATABASE/PGUSER` 环境变量或 `~/.pgpass`，不要把口令写进 argv。

## 方言差异（`sql check --dialect postgres`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.postgres.alter_type_rewrite` | WARN | `ALTER TABLE ... TYPE` 会重写整表并持 ACCESS EXCLUSIVE 锁 |
| `dialect.postgres.truncate` | WARN | `TRUNCATE` 立即清空；离线重跑优先按分区/条件替换 |

## 交付物惯例

- 分区概念靠表继承/声明式分区或按 `ds` 列过滤实现，`ddl/` 里写清；
- 大批量写入用 `COPY`/批量 `INSERT`，并在 `etl/` 里注明批次；
- Greenplum 的分布键（`DISTRIBUTED BY`）与存储参数在 `ddl/` 里显式写——插件不猜分布策略。

## 已知缺口

- 没有原生 DB-API（psycopg）通道：现在只走 `psql` 命令族；要直连得先加驱动与适配器声明；
- Greenplum 的分布键/资源组等专有项未建模，方言规则按 PostgreSQL 走；
- 没验证大事务回滚与锁等待对 P3 真跑的影响。
