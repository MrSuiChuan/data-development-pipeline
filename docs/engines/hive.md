# Hive 接入

| 项 | 值 |
| --- | --- |
| kind | `hive` |
| family | `command`（把 SQL 写成文件交给客户端） |
| dialect | `hive` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt`（beeline 不回服务端 query id） |
| 验证状态 | **真跑通过（Hive 4.0 单机 HiveServer2 + beeline，2026-09-29）**，见 docs/TEST-REPORT-hive-live.md |

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

**分区列只写在 `PARTITIONED BY` 里**（真跑踩过）：Hive 的分区列不能同时出现在列定义里，
否则报 `Duplicate column name`。渲染模板现在会自动把 `spec.partition_by` 里的列从列定义中移出，
并给一条说明性 WARN；写入用静态分区更稳（`INSERT INTO t PARTITION (ds='...') VALUES ...`），
动态分区要先开 `hive.exec.dynamic.partition`。

另外两条命令族注意点：

- **不要用 `docker exec <容器> bash -lc "beeline …"`**：登入 shell 重置 PATH，会报 beeline not found；
  直接用 `docker exec <容器> beeline …` 或写绝对路径。
- 容器会随宿主重启退出；`docker start` 后要等"能跑通一条查询"才算就绪。

- `ddl/<table>.sql`：`CREATE TABLE`（分区表写 `PARTITIONED BY`），改列用 `ALTER TABLE ... ADD COLUMNS`；
- `etl/<table>.sql`：一次真跑，产出回执（`local_receipt`）；
- `dp_task/<table>.sql`：带 `-- schedule:` 注解；分区重写是主路径，不做行级更新。

## 已知缺口

- 没验证过 Hive ACID 事务表的行级写路径；
- 没验证 beeline 在 Kerberos 环境下的非交互执行；
- 表格式维度为 `none`（Hive 表就是 Hive 表）。
