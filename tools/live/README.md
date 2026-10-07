# tools/live：真跑驱动脚本（把报告变成可复跑的东西）

`docs/TEST-REPORT-*.md` 记录的是**现场**（证据、数字、坑）；这个目录放的是**驱动**——
照着跑一遍就能复现同一套链路。所有脚本都只依赖：本机 docker、Python 3.10+、以及脚本自己会
检查的外部引擎（缺了会给出明确的安装/启动提示）。

## 约定

- 工作目录：默认 `$REPO/.tmp/live/<engine>`，可用 `DDP_LIVE_WORK` 覆盖（`.tmp` 已在 .gitignore）。
- 仓库根：由脚本自身位置推导（`tools/live/../..`），可用 `DDP_REPO` 覆盖。
- 引擎安装目录：各自用 `<ENGINE>_HOME` 环境变量覆盖，默认值写在脚本头部。
- 每个脚本都以 `p3_gate.sh` 收尾：跑真实的阶段 3 出口门，期望输出 `FAIL=0`。

## 索引

| kind | 脚本 | 依赖 | 报告 |
| --- | --- | --- | --- |
| `clickhouse` | `chain_clickhouse.sh` | `clickhouse/clickhouse-server:24.8` 镜像 | TEST-REPORT-clickhouse-live.md |
| `postgres` | `chain_postgres.sh` | `postgres:16` 镜像 | TEST-REPORT-postgres-live.md |
| `mysql` | `chain_mysql.sh` | `mysql:8.4.4` 镜像 | TEST-REPORT-mysql-live.md |
| `trino` | `chain_trino.sh` | `trinodb/trino:476` 镜像 | TEST-REPORT-trino-live.md |
| `hive` | `chain_hive.sh` | `apache/hive:4.0.0` 镜像 | TEST-REPORT-hive-live.md |
| `spark` | `chain_spark.sh` | Spark 4.1.3 发行包（`SPARK_HOME`） | TEST-REPORT-spark-live.md |
| 湖表 ×4（Spark 宿主） | `chain_lakehouse.sh` | 同 `spark`，另需对应格式的 jar | TEST-REPORT-lakehouse-live.md |
| `doris` | `chain_doris.sh` | Doris 二进制（官方 tarball，`DORIS_HOME`；FE 要 JDK 17） | TEST-REPORT-doris-live-2026-09-30.md |
| `starrocks` | `chain_starrocks.sh` | `starrocks/fe-ubuntu` + `be-ubuntu` 镜像 | TEST-REPORT-starrocks-live.md |
| `flink` + `paimon` | `chain_flink_paimon.sh` | Flink 1.20.3（`FLINK_HOME`）+ paimon jar + hadoop uber jar | TEST-REPORT-flink-paimon-live.md |
| `tidb` | `chain_tidb.sh` | `pingcap/tidb` 镜像（1panel 源最稳） | TEST-REPORT-tidb-live.md |
| `presto` | `chain_presto.sh` | Presto 0.299（`PRESTO_HOME`，`presto_up.sh` 负责起） | TEST-REPORT-presto-live.md |
| `presto`（驱动细节） | `presto_sql.sh` | HTTP `/v1/statement` 客户端（语句尾别带分号） | TEST-REPORT-presto-live.md |
| `greenplum` | `chain_greenplum.sh` | `datagrip/greenplum:6.8` 镜像 | TEST-REPORT-greenplum-live.md |
| `generic-sql` | `chain_generic_sql.sh` | 任意 MySQL 协议库（默认用 `mysql:8.4.4`） | TEST-REPORT-generic-sql-live.md |
| live 六阶段 | `six_phase_live.sh` + `tracker_stub.py` | 同 `flink`，外加 JSON tracker 替身 | TEST-REPORT-live-pipeline-flink-paimon.md |

## 取材来源（本网络实测能用的）

| 东西 | 能用的源 | 备注 |
| --- | --- | --- |
| Doris 二进制 | `apache-doris-releases.oss-accelerate.aliyuncs.com`（北京区亦可） | 单流会掉到 KB/s 级，用 8 路分片；同目录有 `.sha512` |
| Flink / Paimon | `dlcdn.apache.org` + Maven Central（或华为云 Maven 镜像） | Maven Central 直连连续 ranged 请求会被 429 |
| Presto server | **华为云 Maven 镜像**（3.4 MB/s） | 746 MB；PrestoDB 在 Maven Central 没有可执行 CLI jar |
| 容器镜像 | `docker.1panel.live`（多数命名空间可用）、`docker.1ms.run`（部分） | `m.daocloud.io` 对部分命名空间 403 |
| TiDB | `docker.1panel.live/pingcap/tidb` | 官网域名本网络 DNS 不通 |

## 注意

- 这些是**联调脚本**，不是插件的运行时代码：它们不进 hook、不参与门控，只被人工调用。
- 脚本里不含任何内网地址/凭据；跑之前按各脚本头部的默认值确认引擎位置。
