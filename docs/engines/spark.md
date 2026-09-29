# Spark SQL 接入

| 项 | 值 |
| --- | --- |
| kind | `spark` |
| family | `command` |
| dialect | `spark` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt` |
| 湖表格式 | `iceberg` / `hudi` / `delta` / `paimon` / `none` |
| 验证状态 | **真跑通过（Spark 4.1.3 local 模式，2026-09-29）**，见 docs/TEST-REPORT-spark-live.md；YARN/K8s 未验 |

## 配置示例

```json
{
  "platform": {
    "kind": "spark",
    "command": {
      "engine": "spark",
      "run": ["spark-sql", "--master", "yarn", "-f", "{file}"],
      "timeout": 1800
    }
  }
}
```

`--conf` 之类的 catalog 参数（Iceberg/Hudi/Paimon）按集群实际配置写在 `run` 里，
插件不猜；换集群只改这一行。

两个真跑踩到的点：

1. **发行包解压目录带后缀**：`spark-4.1.3-bin-hadoop3/`，不是 `spark-4.1.3/`；
   路径写错的表现是 `spark-sql: No such file or directory`。
2. **冷启动按分钟计**：local 模式首个 `spark-sql -f` 约 410s（JVM + metastore 初始化），
   之后每条语句 20-40s；`timeout` 别按秒级配。

## 方言差异（`sql check --dialect spark`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.spark.row_write_needs_lakehouse` | WARN | `UPDATE/DELETE/MERGE` 只在 Delta/Hudi/Iceberg 或 ACID 表上成立；普通 Parquet/Hive 表不支持 |
| `dialect.spark.overwrite_without_partition` | WARN | `INSERT OVERWRITE` 不带 `PARTITION` 会覆盖整表 |

## 交付物惯例

- 分区重写是主路径（`INSERT OVERWRITE ... PARTITION (ds=...)`）；
- 需要行级写时先在 SDS 里说清表格式（iceberg/hudi/delta）与主键，否则 P3 会以 WARN 提示；
- `ddl/` 用 `CREATE TABLE ... USING <format>`（湖表）或 `USING parquet`（文件表）。

## 已知缺口

- 没验证过 YARN/K8s 上 `spark-sql` 的非交互执行与超时行为；
- `format` 目前只是注册表里的维度与文档约定，**还没有配置字段**（COMPAT-011 待办）；
- 没验证 Spark Thrift Server（JDBC）通道。
