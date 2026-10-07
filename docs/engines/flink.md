# Flink（+ Paimon）接入

| 项 | 值 |
| --- | --- |
| kind | `flink` |
| family | `command`（把 SQL 文件交给 argv 模板） |
| dialect | `flink` |
| 必填配置键 | `platform.command.run` |
| 湖表格式 | `paimon` / `iceberg` / `hudi` / `none`（`platform.format`） |
| 证据级别 | `local_receipt` |
| 验证状态 | **真跑通过（Flink 1.20.3 + Paimon 1.4.1，2026-10-07）**，见 [TEST-REPORT-flink-paimon-live.md](../TEST-REPORT-flink-paimon-live.md) |

## 本地真跑记录（2026-10-07）

- 起法：官方 tarball 解包即可（**JDK 17**；本机 JDK 21 下 Flink 1.20 不在支持范围）；
  Paimon 要两个 jar 进 `flink/lib`：`paimon-flink-1.20-<ver>.jar` +
  `flink-shaded-hadoop-2-uber-2.8.3-10.0.jar`（filesystem catalog 要 Hadoop 的 `FileSystem` 类）。
- **`sql-client.sh -f` 出错也返回 0**（实测：SQL 报错、结果模式配错，退出码都是 0）。
  所以 argv 必须包一层：日志里出现 `[ERROR]` 就返回非零，否则插件会把失败的 SQL
  记成 SUCCESS——那是**假证据**。
- **单机不要独立集群**：SQL client 默认 `execution.target=remote`（会 `Connection refused`），
  用 `SET 'execution.target' = 'local';` 走进程内 mini-cluster。
- 非交互跑 `SELECT` 要在 SQL 文件里 `SET 'sql-client.execution.result-mode' = 'TABLEAU';`。
- Paimon 表要靠 **Paimon catalog** 建（`CREATE CATALOG paimon WITH ('type'='paimon','warehouse'=…)`）；
  只给通用 connector 形式（`WITH ('connector'='paimon','path'=…)`）时写入会报
  `Schema file not found … Please create table first.`。
- 结果：`ddl render` 的模板建表成功 → 写入 3 行 → 读回 **3 行 / 60.75** → 账本 → `evidence verify` →
  **P3 出口门 FAIL=0**；另起一个 sql-client 直接查 Paimon 表也是 3 行 / 60.75（数据真落盘）。

## 配置示例

```json
{
  "platform": {
    "kind": "flink",
    "format": "paimon",
    "command": {
      "engine": "flink",
      "run": ["bash", "-lc", "<flink>/bin/sql-client.sh -i <init.sql> -f {file} > /tmp/flink-sql.log 2>&1; rc=$?; grep -q '\\[ERROR\\]' /tmp/flink-sql.log && rc=1; tail -40 /tmp/flink-sql.log; exit $rc"],
      "timeout": 1800
    }
  }
}
```

`<init.sql>` 负责环境（本仓库不生成它，按需自己维护）：

```sql
SET 'execution.target' = 'local';
SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'TABLEAU';
CREATE CATALOG paimon WITH ('type' = 'paimon', 'warehouse' = 'file:///path/to/warehouse');
USE CATALOG paimon;
CREATE DATABASE IF NOT EXISTS ods;
USE ods;
```

流式作业去掉 `execution.runtime-mode` 的 batch、改成独立集群地址即可；本次只验证了批模式。

## 方言差异（`sql check --dialect flink`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.flink.primary_key_needs_upsert` | WARN | `PRIMARY KEY` 只在支持 upsert 的 connector 上生效（Paimon 主键表支持） |
| `format.paimon.missing_bucket` | WARN | Paimon 建表没显式 bucket：默认值可能不适合写入并发 |

## 已知缺口

- 没验证流式/持续作业、CDC、checkpoint 与状态恢复；
- 没验证 Hive catalog / S3 / OSS 远端仓库；
- 没验证 Flink on YARN/K8s 与资源调优；
- Flink 1.20 只对 JDK 17 背书。
