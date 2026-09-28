# Hive 接入

| 项 | 值 |
| --- | --- |
| kind | `hive` |
| family | `command`（把 SQL 写成文件交给客户端） |
| dialect | `hive` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt`（beeline 不回服务端 query id） |
| 验证状态 | 未在真实 Hive 上联调；方言规则与配置体检有单测 |

## 配置示例

```json
{
  "platform": {
    "kind": "hive",
    "command": {
      "engine": "hive",
      "run": ["beeline", "-n", "{user}", "-f", "{file}"],
      "check": ["beeline", "-n", "{user}", "-e", "EXPLAIN {sql}"],
      "timeout": 900
    },
    "profiles": {"prod": {"command": {"engine": "hive", "run": ["beeline", "-n", "{user}", "-f", "{file}"]}}}
  }
}
```

凭证不要写进命令行（进程列表可见）：优先在 beeline 侧配置 Kerberos/凭据缓存，
或让 `{user}` 指向环境里已有的身份。

## 方言差异（`sql check --dialect hive`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.hive.no_row_write` | FAIL | Hive 非 ACID 表不支持 UPDATE/DELETE；离线改写用 `INSERT OVERWRITE ... PARTITION` |
| `dialect.hive.primary_key` | WARN | Hive 不支持 PRIMARY KEY 约束（仅 ACID 事务表通过 TBLPROPERTIES 开启） |
| `dialect.hive.merge_into` | WARN | MERGE 仅 Hive 4 事务表支持，低版本直接语法报错 |

## 交付物惯例

- `ddl/<table>.sql`：`CREATE TABLE`（分区表写 `PARTITIONED BY`），改列用 `ALTER TABLE ... ADD COLUMNS`；
- `etl/<table>.sql`：一次真跑，产出回执（`local_receipt`）；
- `dp_task/<table>.sql`：带 `-- schedule:` 注解；分区重写是主路径，不做行级更新。

## 已知缺口

- 没验证过 Hive ACID 事务表的行级写路径；
- 没验证 beeline 在 Kerberos 环境下的非交互执行；
- 表格式维度为 `none`（Hive 表就是 Hive 表）。
