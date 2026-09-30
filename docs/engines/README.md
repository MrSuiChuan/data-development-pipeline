# 引擎接入矩阵

这张表是 `engine/core/engines.py` 的**投影**，不是第二份清单：勾选/填写前先照注册表核对，
加引擎时改注册表再回来补这一行（`tests/test_engines.py::test_matrix_covers_every_registered_kind`
会拦住"注册了却没文档"和"文档写了没注册"两种漂移）。

配置体检与方言检查：

```bash
py -3 .data-dev/engine/ddp.py platform doctor                       # 缺键/未登记 kind/明文口令
py -3 .data-dev/engine/ddp.py sql check --file ddl/x.sql --dialect hive
```

## 执行族与证据级别

| family | 适配器 | 执行方式 | 证据级别 | 典型 kind |
| --- | --- | --- | --- | --- |
| dbapi | `SqlPlatformClient` | pymysql 直连，逐条 SQL | `local_receipt` | mysql / doris / starrocks / tidb / oceanbase / generic-sql |
| command | `CommandPlatformClient` | 把 SQL 写成文件交给 argv 模板 | `local_receipt` | hive / spark / flink / paimon / clickhouse / trino / presto / postgres / greenplum / odps / command |
| cli | `LivePlatformClient` | 平台 CLI 的 JSON stdout 契约 | `server_query_id` | cli |
| mcp | 不直调（会话内通道） | 由宿主模型调 `mcp__<server>__<tool>` | `none` | mcp |

`local_receipt` 的含义：回执里的 `query_id` 是**本进程自造**的（`run_<hash>`），
只能证明"这条 SQL 在本机执行过"；出口门与治理报告都会带上这个级别，不会和平台 query_id 混为一谈。

## 矩阵

| kind | family | dialect | 必填配置键 | 默认端口 | 湖表格式 | 验证状态 |
| --- | --- | --- | --- | --- | --- | --- |
| mysql | dbapi | mysql | host | 3306 | none | **真跑通过（经 kind=command + engine=mysql，MySQL 8.4.4，2026-09-28）**；dbapi/pymysql 直连路径未跑 |
| tidb | dbapi | mysql | host | 4000 | none | 未验证（按 MySQL 方言处理） |
| oceanbase | dbapi | mysql | host | 2881 | none | 未验证（MySQL 模式） |
| doris | dbapi | doris | host | 9030 | none | **真跑通过（官方二进制 4.1.4.1-rc02 原生 FE+BE，2026-09-30）**：建表/写入/读回 3 行 60.75、P3 出口门 FAIL=0；真引擎揪出并修掉 DDL 的 ENGINE/键子句顺序缺陷——见 [TEST-REPORT-doris-live-2026-09-30.md](TEST-REPORT-doris-live-2026-09-30.md)（上一轮"未验证"的现场留在 [TEST-REPORT-doris-unverified.md](TEST-REPORT-doris-unverified.md)，更早的全流水线记录在 [TEST-REPORT-doris-live.md](TEST-REPORT-doris-live.md)） |
| starrocks | dbapi | starrocks | host | 9030 | none | **本轮未验证**（镜像拉取多次失败）：见 [TEST-REPORT-starrocks-unverified.md](TEST-REPORT-starrocks-unverified.md) |
| generic-sql | dbapi | generic | host | - | none | 未验证（按 MySQL 协议） |
| hive | command | hive | command.run | - | none | **真跑通过（Hive 4.0 单机，2026-09-29）**：分区列模板修正后建表成功、读回 3 行/60.75、P3 出口门 FAIL=0 |
| spark | command | spark | command.run | - | iceberg/hudi/delta/paimon/none | **真跑通过（Spark 4.1.3 local 模式，2026-09-29）**：建表/写入/读回 3 行 60.75、P3 出口门 FAIL=0 |
| flink | command | flink | command.run | - | paimon/iceberg/hudi/none | 插件侧未真跑（开源栈真跑记录见 realtime-data-plugin 的 docs/oss-lab.md） |
| paimon | command | flink | command.run | - | paimon | 同上（通过 Flink catalog 访问） |
| clickhouse | command | clickhouse | command.run | 9000 | none | **真跑通过（ClickHouse 24.8.14.39，2026-09-29）**：模板直接建表成功、P3 出口门 FAIL=0；UPDATE…FROM 的真报错与 mutation 行为见报告 |
| trino | command | trino | command.run | 8080 | iceberg/hudi/delta/none | **真跑通过（Trino 476 + memory connector，2026-09-29）**：模板改掉 format 缺陷后建表成功、P3 出口门 FAIL=0 |
| presto | command | trino | command.run | 8080 | none | 未验证（与 Trino 共用方言规则） |
| postgres | command | postgres | command.run | 5432 | none | **真跑通过（PostgreSQL 16，2026-09-28）**：建表/写入/读回/证据落账/verify |
| greenplum | command | postgres | command.run | 5432 | none | 未验证（GP 专有项未建模） |
| odps | command | odps | command.run | - | none | **不可本地复现**（云服务）：模板/方言规则/doctor 有单测，真跑需要云账号 + 项目空间，见 [odps.md](odps.md) |
| command | command | generic | command.run | - | none | 自定义 argv，按 generic 方言 |
| cli | cli | generic | -（默认执行名 platform） | - | none | 契约在 docs/REPRODUCTION-NOTES.md 1.2.2，未接真实平台 |
| mcp | mcp | generic | - | - | none | 预留；引擎不直调 |

## 逐引擎页

| kind | 页面 |
| --- | --- |
| hive | [hive.md](hive.md) |
| spark | [spark.md](spark.md) |
| doris | [doris.md](doris.md) |
| clickhouse | [clickhouse.md](clickhouse.md) |
| trino / presto | [trino.md](trino.md) |
| postgres / greenplum | [postgres.md](postgres.md) |
| odps | [odps.md](odps.md) |

其余 kind（MySQL 协议族、flink/paimon、command、cli、mcp）的连接方式见
`docs/REPRODUCTION-NOTES.md` 1.2.1 / 1.2.2 与 `engine/templates/platform.example.json`。

真实引擎联调报告（含命令、原始输出与失败案例）：
[PostgreSQL 16](TEST-REPORT-postgres-live.md) ｜ [MySQL 8.4.4](TEST-REPORT-mysql-live.md) ｜
[ClickHouse 24.8](TEST-REPORT-clickhouse-live.md) ｜ [Trino 476](TEST-REPORT-trino-live.md) ｜
[Hive 4.0](TEST-REPORT-hive-live.md) ｜ [Spark 4.1.3](TEST-REPORT-spark-live.md) ｜
[湖表格式 ×4（Iceberg/Delta/Hudi/Paimon）](TEST-REPORT-lakehouse-live.md) ｜
[Doris 4.1.4.1](TEST-REPORT-doris-live-2026-09-30.md) ｜
[StarRocks（本轮未验证）](TEST-REPORT-starrocks-unverified.md) ｜
[Doris 上一轮未验证的现场](TEST-REPORT-doris-unverified.md) ｜
[Doris 历史记录（2026-09-13）](TEST-REPORT-doris-live.md)。

## 湖表格式（与执行引擎正交）

表格式不是一个 kind，而是挂在引擎上的维度：配置 `platform.format`
（`none` / `iceberg` / `hudi` / `paimon` / `delta`，缺省 `none`），
由注册表判定"这个引擎能不能用这个格式"，组合未登记直接报错。

| 格式 | 可用引擎（注册表登记） | 建表 DDL 关键差异 | 写入/运维要点 |
| --- | --- | --- | --- |
| iceberg | spark / trino / flink | `USING iceberg`（spark）；Trino 由 catalog 决定，DDL 里只写 `format` | 快照要 `expire_snapshots`；分区演进用 `ADD/DROP PARTITION FIELD`。**Spark 真跑通过（1.11.0，2026-09-29）** |
| hudi | spark / trino / flink | `USING hudi` + `TBLPROPERTIES ('primaryKey','preCombineField')` | MOR 要跑 compaction；整表覆盖会绕开 upsert 语义；**Spark 侧还需 `spark.serializer=KryoSerializer`**。**真跑通过（1.2.0，2026-09-29）** |
| paimon | flink / spark | `WITH ('connector'='paimon','bucket'=...)` | compaction 与快照过期决定读性能；bucket 影响写并发；Spark 侧需 `PaimonSparkSessionExtensions`。**真跑通过（2.0.0，2026-09-29）** |
| delta | spark / trino | `USING delta` | `OPTIMIZE` 并小文件、`VACUUM` 不可回滚（默认保留 7 天）；**Spark 侧用 `--packages` 解析传递依赖（单 jar 会 NoClassDefFoundError）**。**真跑通过（4.4.0，2026-09-29）** |

用法：

```bash
py -3 .data-dev/engine/ddp.py platform doctor                      # 校验 kind × format 组合
py -3 .data-dev/engine/ddp.py ddl render --spec ods_x.json --format iceberg
py -3 .data-dev/engine/ddp.py sql check --file etl/ods_x.sql --format hudi
```

格式检查只出 WARN（不阻断流水线）：它们表达的是"这个组合成立有条件"，不是"语法错"。
具体规则见 `engine/core/formats.py`（每条都带修复建议）。
