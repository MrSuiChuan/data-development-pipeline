# ClickHouse 接入

| 项 | 值 |
| --- | --- |
| kind | `clickhouse` |
| family | `command` |
| dialect | `clickhouse` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt` |
| 验证状态 | **真跑通过（ClickHouse 24.8.14.39，2026-09-29）**，见 docs/TEST-REPORT-clickhouse-live.md |

## 本地真跑记录（2026-09-29）

- 起法：`docker run -d --name ddp-ch -p 9000:9000 -p 8123:8123 -v <项目>:<项目> -e CLICKHOUSE_PASSWORD=ddp clickhouse/clickhouse-server:24.8`；
  **先建项目目录再挂载**（反过来 Docker 会用 root 建挂载点，写 SQL 就 PermissionError）。
- 客户端：宿主没装 `clickhouse-client`，argv 走容器内客户端 `docker exec -i -e CLICKHOUSE_PASSWORD=ddp ddp-ch clickhouse-client --multiquery --queries-file {file}`。
- 结果：`ddl render` 的模板原样建表成功（`Int64`/`Decimal(18, 2)`/`Date` 三个物理类型被真引擎接受）→ 写入三行 →
  读回 **3 行 / 60.75** → 账本 → `evidence verify` → **P3 出口门 FAIL=0**。
- 规则背书：`UPDATE … FROM` 真报 `SYNTAX_ERROR`（退出码 62）；`ALTER … UPDATE` 立即返回且
  `system.mutations` 待办 0（证实是"条件性 WARN"而不是语法错）。

## 配置示例

```json
{
  "platform": {
    "kind": "clickhouse",
    "command": {
      "engine": "clickhouse",
      "run": ["clickhouse-client", "--multiquery", "--queries-file", "{file}"],
      "timeout": 900
    }
  }
}
```

连接参数（`--host/--user/--secure`）写在 `run` 里；口令用 `--password` 会进进程列表，
优先走客户端配置文件或环境变量。ClickHouse 的 MySQL 协议端口（9004）也能用
`kind=mysql` 接，但 SQL 语义受限（下方规则按 clickhouse 方言检查，不会生效）。

## 方言差异（`sql check --dialect clickhouse`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.clickhouse.update_from` | FAIL | 不支持 `UPDATE ... FROM`；用 `ALTER TABLE ... UPDATE` 或按分区重建 |
| `dialect.clickhouse.mutation_async` | WARN | `ALTER ... UPDATE/DELETE` 是异步 mutation：需 `mutations_sync=1` 或轮询 `system.mutations`，大表开销高 |
| `dialect.clickhouse.engine_required` | WARN | 建表通常要显式 `ENGINE`（MergeTree 还需 `ORDER BY`）；CTAS 可忽略 |

## 交付物惯例

- 大表重刷写 `INSERT INTO ... SELECT` 按分区重建，不用 mutation；
- `ddl/` 带 `ENGINE = MergeTree()` + `ORDER BY` + `PARTITION BY`；
- 回刷按分区删除 + 重写，别指望 `ALTER TABLE ... DELETE` 立即完成。

## 已知缺口

- 没验证 `mutations_sync` 与长耗时 mutation 的超时交互；
- 没验证 Distributed / Replicated 表引擎的建表模板；
- 退出码：`clickhouse-client` 语句失败会返回非零，命令族据此判定成败（未实测多语句中途失败的行为）。
