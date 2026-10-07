# Changelog

## [Unreleased]

### Added（真跑驱动脚本入库：tools/live，CLOSE-011）

18 份 `TEST-REPORT-*.md` 之前只有"现场"，没有可复跑的驱动——脚本都躺在 `.tmp`（gitignored）。
现在把验证过的驱动平移进 `tools/live/`（22 个脚本 + README 索引），并做了三件事：

- 路径全部参数化：仓库根由脚本位置推导（`DDP_REPO` 可覆盖），工作目录默认 `$REPO/.tmp/live`
  （`DDP_LIVE_WORK` 可覆盖），引擎目录用 `DORIS_HOME` / `FLINK_HOME` / `PRESTO_HOME` / `DDP_JAVA17` / `DDP_PYLIB`；
- README 里带"kind → 脚本 → 依赖 → 报告"索引，外加一张**取材来源表**（本网络实测能用的下载源：
  Doris 的 OSS 桶、dlcdn、华为云 Maven 镜像、`docker.1panel.live` 等）；
- 防回归用例 `tests/test_live_recipes.py`：脚本必须写进 README 索引、**不许带本机绝对路径**
  （`/mnt/c/Users/...`、`C:\Users\...`、`/home/<name>/`）、行尾必须是 LF、POSIX 上还要过 `bash -n`。

平移时真的踩到两个坑，顺手写成纪律：Windows 上写文件默认 CRLF → bash 直接语法错（移植脚本改成 LF）；
端口/镜像名散在各脚本里 → 统一在 README 索引里点名。落地后用一个引擎端到端复跑验证过
（`chain_generic_sql.sh` → `FAIL=0`）。

## [1.3.0] — 2026-10-07

**主题：把"支持主流大数据组件"从声明变成现场。** 从 1.2.2 到现在，矩阵里除 OceanBase 外所有能在本地
起起来的 kind 都有了真跑记录，插件自己的六阶段流水线也第一次在 live 模式（真引擎）跑完整闭环。

### 谁受影响、要不要迁移

- **使用方**：不需要改配置。新增能力全是可选项。
- **唯一的行为变化（要留意）**：`platform.profiles.<env>.command.run` 以前被**静默忽略**
  （`CommandPlatformClient` 只在初始化时读一次 argv），现在按环境生效。如果你的 prod profile 里
  写过 `command`，升级后 P5 部署会真的用那份 argv——这正是它该有的行为，但请确认那份 argv
  指向的确实是 prod（本次真跑就是在生产链路里发现"声称发 prod、实际跑 dev"的）。
- **升级三步**：拉取 → 非 Windows 跑 `python3 tools/adapt_hooks.py` → 开新会话。

### 已知边界（发布说明里同样如实写）

- **OceanBase 仍未验证**（镜像大层拉不动 + 单机需 2–4GB 内存）：见台账 `CLOSE-009` 的解除条件；
- 引擎真跑都限于**单机/本地形态**：YARN/K8s 提交、远端存储（S3/OSS/HDFS）、多节点/副本 3、
  流式与状态恢复等仍是各引擎页"已知缺口"里列明的未验证项；
- **CI 运行记录未核**：仓库私有且本机无 token，DEVEX-001/003、ENG-002 只有"配置覆盖 + 本地等价命令"作证；
- live 六阶段的工单系统用的是 JSON CLI 替身，KB/DQC 连接器未接真；
- 所有真跑的证据级别是 `local_receipt`（command/dbapi 族拿不到平台 query_id）。

### 本次发布的验证门

`python -m unittest discover -s tests`（数量以输出为准）、`python -m compileall -q engine hooks tests tools`、
`tools/generate_engine_manifest.py` + `git diff --exit-code`、`tests/test_generated_surface.py`、
WSL 侧 `tools/adapt_hooks.py --check`、`git status --short` 为空。

### Added（generic-sql 真跑通过：兜底 kind 的最小 DDL 行为被真库验证，CLOSE-010）

`generic-sql` 是"任何 MySQL 协议兼容库"的兜底 kind（`dialect=generic`，只有基线 17 项规则）。
本轮用本机 MySQL 8.4.4 接了一次：`ddl render` 出的是**最小 DDL**（没有主键子句、没有引擎属性，
generic 不做 MySQL 专属改写）→ 建表 → 写入 3 行 → 读回 **3 行 / 60.75** → 账本 →
`evidence verify` → **P3 出口门 FAIL=0（WARN=3）**。
这条真跑把"generic ≠ mysql"从文档措辞变成了现场：两者的差别在"方言检查有多严"，不在协议。
报告：[docs/TEST-REPORT-generic-sql-live.md](docs/TEST-REPORT-generic-sql-live.md)。

### Added（Greenplum 6.8.1 真跑通过，CLOSE-008）

greenplum 与 postgres 共用方言，但它毕竟是 MPP 发行版，所以单独跑了一份现场：
镜像 `datagrip/greenplum:6.8`（1.16 GB，`docker.1panel.live`）→ 容器内 `gpinitsystem` 初始化单机集群 →
`kind=greenplum`（command 族，dialect=postgres）→ 建表 → 写入 3 行 → 读回 **3 行 / 60.75** →
账本 → `evidence verify` → **P3 出口门 FAIL=0（WARN=3）**，并额外查了一次 `gp_segment_id` 分布。
两个坑写进报告与矩阵：`psql` 必须先 `source <GP_HOME>/greenplum_path.sh`（否则 `libpq.so.5` 缺失），
镜像里 psql 在 `/opt/greenplum-db-6.8.1/bin/psql`。
报告：[docs/TEST-REPORT-greenplum-live.md](docs/TEST-REPORT-greenplum-live.md)。

### Known limit（OceanBase 本轮未跑成，现场留档，CLOSE-009）

`docker.1panel.live/oceanbase/oceanbase-ce` 的 tag 列表可见（3.1.x~4.2.x），但大层反复 Retrying
约 8 分钟未拉全（与当初 StarRocks 的形态相同），已停掉避免占带宽；单机 OB 还需 2-4 GB 内存，
本机 7.84 GB 且常有别的任务在跑。解除条件写在台账 `CLOSE-009`：换稳定网络/给镜像 tar，
或机器空闲出 4 GB+ 再跑一次。oceanbase 走的是 mysql 方言，引擎链路已由 TiDB/MySQL 两轮真跑背书。

### Added（PrestoDB 0.299 真跑通过，CLOSE-007）

矩阵里 presto 一直写"与 Trino 共用方言规则、未验证"——PrestoDB 与 Trino 是两个发行版，这轮用
**PrestoDB 0.299** 真跑了一遍：`kind=presto`（command 族，dialect=trino）→ `ddl render`
（无分区/属性时不生成 `WITH`）→ 建表 → 写入 3 行 → 读回 **3 行 / 60.75** → 账本 → `evidence verify` →
**P3 出口门 FAIL=0（WARN=3）**。

两条真机细节：① Presto 的 HTTP `/v1/statement` **不接受语句尾部分号**（`SELECT 1;` 报
`mismatched input ';'`，官方 CLI 会替你剥、裸客户端必须自己剥）；② memory connector 上 `DELETE`
报 `This connector does not support deletes`，与"能不能删取决于 connector"的 WARN 分级一致。
取材：Maven Central 直连连续 ranged 请求会被 429，换**华为云 Maven 镜像**（3.4 MB/s）三分钟拉完；
PrestoDB 在 Maven Central 没有可执行 CLI jar。
报告：[docs/TEST-REPORT-presto-live.md](docs/TEST-REPORT-presto-live.md)；接入页补在
[docs/engines/trino.md](docs/engines/trino.md)。

### Added（TiDB v7.5.4 真跑通过：矩阵里最后一块能本地起的空白，CLOSE-006）

`kind=tidb` 走 dbapi（pymysql 直连 4000）：官方单机镜像 `pingcap/tidb:v7.5.4`（526 MB，经
`docker.1panel.live`）→ `select version()` = `8.0.11-TiDB-v7.5.4`，10 秒就绪 →
`ddl render`（MySQL 方言：主键在列定义里、`ENGINE=InnoDB`，真引擎接受）→ 写入 3 行 →
读回 **3 行 / 60.75** → 账本 → `evidence verify` → **P3 出口门 FAIL=0（WARN=3）**。
顺带拿到一条真引擎背书：`INSERT OVERWRITE` 在 TiDB 上报 `ERROR 1064` 真语法错，
与 `dialect.mysql.no_insert_overwrite` 的 FAIL 判定一致。
报告：[docs/TEST-REPORT-tidb-live.md](docs/TEST-REPORT-tidb-live.md)；
取材记一笔：`download.pingcap.org` 本网络 DNS 不通，`docker.1ms.run`/`hub.rat.dev`/`dockerpull.org`
拉取超时、`m.daocloud.io` 403，**1panel 能拉通**。

### Added（live 六阶段端到端真实测试：Flink + Paimon，CLOSE-005）

此前真跑都停在"引擎链路"（doctor → ddl render → 建表 → 写入 → 读回 → 证据 → P3 门）。这一轮把**插件自己的
六阶段流水线**放进 live 模式跑在真实引擎上：建单（8 项质量门）→ SDS（八条硬规则 + 贴单回读 + 桥接 + publish）
→ 准入门 → P2 架构门 → P3 真跑（Flink/Paimon 建表 + 聚合 2 行 / 60.75 + 证据绑定）→ P4 机器证据（真 query_id）
→ P5 真发布（prod 环境）→ P6 语义层（用户拍板）→ finish，工单状态走到**已交付**。
报告：[docs/TEST-REPORT-live-pipeline-flink-paimon.md](docs/TEST-REPORT-live-pipeline-flink-paimon.md)。

### Fixed（真流程揪出的两个缺陷：环境隔离被静默忽略 + SDS 终版判定取错评论）

- **P5 声称发布到 prod，实际跑的是 dev**：`CommandPlatformClient` 只在 `__init__` 读一次 argv，
  `profiles.<env>.command.run` 的覆盖被静默忽略（`_effective_config` 也只合并连接类字段）。
  现场证据：`deploy_log.json` 的 stdout 指向 `ddp-dev-init.sql`、数据落进 dev warehouse、prod warehouse 为空。
  现在每次执行按 env 重新解析 argv，profile 可继承或覆盖 `command`；真机复验 `--env prod` 走 prod init、
  `ddp_prod.db` 落在 prod warehouse。用例：`tests/test_sql_platform.py::test_profile_command_override_is_used_per_env`。
- **SDS 终版评论判定取错**：真 tracker 里同一版本号被贴两次时，`max()` 平局取第一条（旧的），
  `ticket verify-latest` 于是永远验不过。现在同版本取**最后一条**，并补用例 `tests/test_spec_latest_comment.py`。
  顺带把 live tracker 的返回字段契约写进 `docs/REPRODUCTION-NOTES.md` 1.1：评论对象是 `anchor` + **`content_md`**
  （只给 `content` 会被当成空评论），`comments --id` 按时间序返回。

### Added（Flink 1.20.3 + Paimon 1.4.1 真跑通过，CLOSE-004）

矩阵里 flink / paimon 两行的"插件侧未真跑"填实：官方 tarball 起单机 Flink（批模式）+ Paimon jar，
走完 `doctor → ddl render → 建表 → 写入 → 读回 3 行 / 60.75 → 账本 → evidence verify → **P3 出口门 FAIL=0**`，
另起一个 sql-client 直接查 Paimon 表复核过（数据真落盘）。
报告：[docs/TEST-REPORT-flink-paimon-live.md](docs/TEST-REPORT-flink-paimon-live.md)；
接入页：[docs/engines/flink.md](docs/engines/flink.md)。

### Fixed（Flink 接线四条真机问题 + 一处文档漂移）

- **`sql-client.sh -f` 出错也返回 0**（实测：SQL 报错、结果模式配错都是 exit 0）。不包一层抓 `[ERROR]`，
  插件会把失败的 SQL 记成 SUCCESS——那是假证据。接入页给出的 argv 模板就是包了这层的形状。
- Paimon filesystem catalog 要 Hadoop 类：补 `flink-shaded-hadoop-2-uber`；
- 单机要 `SET 'execution.target' = 'local';`（默认 remote 会 `Connection refused`）；
- 非交互跑 `SELECT` 要 `SET 'sql-client.execution.result-mode' = 'TABLEAU';`；
- 通用 connector 形式（`WITH ('connector'='paimon','path'=…)`）不建物理表，要用 Paimon catalog 建。
- 文档漂移（CLOSE-003）：`docs/engines/spark.md` 一直写着"`format` 还没有配置字段（COMPAT-011 待办）"，
  而它早就是真字段（`engines.py` / `ddp.py` 都读它）；改成事实陈述，并加
  `tests/test_engine_surface.py::test_engine_pages_do_not_deny_real_config_fields` 防回归。

### Changed（CI 补一步生成物同步门）

`ci.yml` 增加 "generated artifacts must match the repo"：跑 `generate_engine_manifest.py` +
`build_codex_surface.py` 后 `git diff --exit-code`（DEVEX-003 验收原文要求的那一步；单元测试里有等价断言）。

### Added（StarRocks 真跑通过：官方 FE/BE 镜像，WSL-002）

矩阵里 starrocks 那行也从"本轮未验证"变成"真跑通过"。上一轮卡在"镜像层下载反复超时"，
这一轮两个镜像（FE 1.97 GB / BE 3.03 GB）已经在本地，用的是**容器保活 + exec 起进程**的正确姿势：

- 两个镜像的默认 `Cmd` 都是 `/bin/bash`，`docker run -d` 会立刻退出（上一轮就是这么误判成"起不来"）；
- FE 默认堆 8192m，本机必须压到 2048m；BE 起来先 `/api/health`，再 `ALTER SYSTEM ADD BACKEND`；
- 链路跑通：`doctor → ddl render → 建表 → 写入 → 读回 3 行 / 60.75 → 账本 → evidence verify →
  **P3 出口门 FAIL=0**；
- 报告：[docs/TEST-REPORT-starrocks-live.md](docs/TEST-REPORT-starrocks-live.md)；
  接入页：[docs/engines/starrocks.md](docs/engines/starrocks.md)。

### Fixed（StarRocks 真跑揪出的两处：BUCKETS AUTO 与 UPDATE 的表模型）

1. **`BUCKETS AUTO` 是 Doris 的写法**——StarRocks 3.3.9 直接语法错
   （`Unexpected input 'AUTO'，最接近的输入是 INTEGER_VALUE`）。同一实例上三种写法的实测：
   `BUCKETS AUTO` ❌、`BUCKETS 1` ✅、省略 BUCKETS ✅。修正后 Doris 保留 `BUCKETS AUTO`，
   StarRocks 不写 BUCKETS（交回引擎自动分桶），用例
   `tests/test_ddl.py::test_buckets_clause_splits_doris_from_starrocks`。
2. **StarRocks 的 UPDATE 要主键模型**：模板默认生成 `UNIQUE KEY`，而 StarRocks 的 UNIQUE KEY 表
   实测 `UPDATE` 报 `table does not support update`（`DELETE` 可以）；手工建 `PRIMARY KEY` 表则
   `UPDATE`/`DELETE` 都成功。`dialect.starrocks.row_write_needs_key_model` 的文案按实测改精确，
   没有把它升格成"包治"。

顺带留证一条弯路：用 `kind=command` + `engine=mysql` 跑 StarRocks 时 `doctor` 报 `dialect=mysql`，
渲染出列内 `PRIMARY KEY` + `ENGINE = InnoDB`，真引擎报 `No viable statement for input ', PRIMARY'`——
要 StarRocks 方言就用 `kind=starrocks`（已写进接入页与报告）。

### Added（Doris 真跑通过：官方二进制原生 FE+BE，WSL-001）

矩阵里 Doris 那行从"本轮未验证"变成"真跑通过"。上一轮卡住的原因不是网络拿不到包，而是**在猜对象名**：
官方下载页给出的真实地址是 `apache-doris-4.1.4.1-bin-x64.tar.gz`（`.tar.gz`，不是猜的 `.tar.xz`）。
换成"从官方页面取真实地址 → 8 路分片下载（单流会掉到 KB/s 级）→ sha512 校验"之后：

- 官方二进制（4,253,509,898 B）解包后在 WSL 里原生起 FE+BE（**不是 Docker**），
  `show frontends`/`show backends` 均 `Alive: true`，版本 `doris-4.1.4.1-rc02-5dbec55b399`；
- 插件链路跑通：`doctor → ddl render → 建表 → 写入 → 读回 3 行 / 60.75 → 账本 → evidence verify →
  **P3 出口门 FAIL=0**（与 ClickHouse/Trino/Hive/Spark/PG 同一验收口径）；
- 三个只在真机上暴露的环境约束写成了可照抄的处置：FE **硬要求 JDK 17**（宿主 JDK 21 被拒）、
  BE 的 `vm.max_map_count`/swap 前置检查在单机要显式跳过（`SKIP_CHECK_ULIMIT=true`，生产必须配）、
  单 BE 集群要显式 `replication_allocation=tag.location.default:1`。
- 报告：[docs/TEST-REPORT-doris-live-2026-09-30.md](docs/TEST-REPORT-doris-live-2026-09-30.md)；
  接入页：[docs/engines/doris.md](docs/engines/doris.md)。

### Fixed（真跑揪出的 Doris DDL 缺陷：ENGINE 与键子句的顺序）

为 Doris/StarRocks 生成的建表模板把 `ENGINE = OLAP` 写在键子句**之后**，Doris 4.1.4.1 直接语法错：
`mismatched input 'ENGINE' expecting {<EOF>, ';'}`——解析器读完键子句就收尾了。现在 `ENGINE` 在前、
键子句在后（与两家文档的写法一致）。同一个模板还加了一句提示：没给
`replication_num`/`replication_allocation` 时提醒单 BE 集群会因默认副本数 3 建表失败（给了就不提醒）。
回归用例：`tests/test_ddl.py::test_doris_starrocks_key_clause_is_outside_column_list`（顺序）与
`::test_doris_replication_hint_only_when_unspecified`（提示的出现/消失）。
真引擎同时给方言规则背书：唯一模型表 `UPDATE` 成功、`DUPLICATE KEY` 表报
`Only unique table could be updated.`，与 `dialect.doris.row_write_needs_key_model` 这条 WARN 一致。

### Changed（许可证：MIT → Apache-2.0）

用户要求把许可换成 Apache-2.0。这不是换个文件就完事：Apache-2.0 的惯例是 `LICENSE` 只放官方全文
（**不含** Copyright 行），归属信息写在 `NOTICE`。

- `LICENSE` 换成 Apache-2.0 官方全文（与 `https://www.apache.org/licenses/LICENSE-2.0.txt` 一致）；
- `NOTICE` 改成 Apache 风格：`Copyright (c) 2026 AI实战技能圈` + 许可类型 + "分发随带 NOTICE"
  （第 4(d) 条），四条分发边界原样保留；
- 两个 `plugin.json` 的 `license` 从 `MIT` 改成 `Apache-2.0`（`.codex-plugin` 是生成物，改源后重跑
  `tools/build_codex_surface.py`）；
- 机器校验跟着改：`test_oss_readiness` 不再认 LICENSE 首行（Apache 全文首行是空行），改按 SPDX 标识认
  正文，并检查 `NOTICE` 写了同一个标识；`test_repo_invariants` 的署名核对从 LICENSE 移到 NOTICE；
- `README.md` / `README.en.md` / `CONTRIBUTING.md` 的许可段同步改。

对使用者可见的差别：Apache-2.0 比 MIT 多一条**专利授权**与**商标限制**，并要求分发时保留 `NOTICE`；
使用方式不变。

### Added（组件真跑矩阵：逐个本地起、逐个真跑）

上一轮把兼容能力做成了代码与单测，但验证矩阵里只有 PostgreSQL 与 MySQL 是真跑通过。
本轮开始逐个把矩阵填实（台账 `goal#component-live-matrix` 的 LIVE-001…LIVE-010），
纪律是**一次只起必要的组件**，跑完即停（本机 7.8 GB 内存）。

- **ClickHouse 24.8 真跑通过**：渲染模板原样执行成功（`Int64` / `Decimal(18, 2)` / `Date`），
  建表 → 写入 → 读回（3 行 / 60.75）→ 账本 → `evidence verify` → **P3 出口门 FAIL=0**。
  两条方言规则得到真引擎背书：`UPDATE ... FROM` 真报 `SYNTAX_ERROR`（退出码 62）；
  `ALTER ... UPDATE` 立即返回且 `system.mutations` 待办 0（证实是条件性 WARN）。
  报告：[docs/TEST-REPORT-clickhouse-live.md](docs/TEST-REPORT-clickhouse-live.md)。
- **Trino 476 真跑通过**（memory connector）：建表 → 写入 → 读回（3 行 / 60.75）→ 账本 →
  `evidence verify` → **P3 出口门 FAIL=0**。真跑揪出并修掉模板缺陷：Trino 的 `WITH (...)` 键
  **由 connector 定义**，模板原先凭空生成 `format = 'PARQUET'`，memory connector 直接报
  `table property 'format' does not exist`；现在无 properties/partition 就不生成 `WITH`，
  `partitioned_by` 会 WARN 提醒是 connector 属性。`INSERT OVERWRITE` 的真报错
  （`mismatched input 'OVERWRITE'`）也让该 FAIL 规则有了真引擎依据。
  报告：[docs/TEST-REPORT-trino-live.md](docs/TEST-REPORT-trino-live.md)。
- **Hive 4.0 真跑通过**（单机 HiveServer2 + beeline）：建表 → 静态分区写入 →
  读回 **3 行 / 60.75**、`SHOW PARTITIONS` 看到 `ds=2026-09-28` → 账本 → `evidence verify` →
  **P3 出口门 FAIL=0**。修掉模板缺陷：Hive/ODPS 的分区列只能写在 `PARTITIONED BY` 里，
  原模板会把 `ds` 同时留在列定义 → `Duplicate column name`；现在 hive/odps 渲染时把分区列
  移出列定义并给说明性 WARN（ClickHouse/PG/Spark/Trino 仍要求分区键是真实列）。
  非 ACID 表的 `UPDATE` 被真引擎拒绝（`Error 10294`），FAIL 规则有真依据。
  报告：[docs/TEST-REPORT-hive-live.md](docs/TEST-REPORT-hive-live.md)。
- **Spark SQL 4.1.3 真跑通过**（local 模式）：建表 → 写入 → 读回 **3 行 / 60.75** → 账本 →
  `evidence verify` → **P3 出口门 FAIL=0**。方言规则得到真引擎背书：普通 parquet 表上
  `DELETE` 被拒（`UNSUPPORTED_FEATURE.TABLE_OPERATION: does not support DELETE`），
  印证"行级写要靠湖表"。两条环境经验写进 playbook：发行包解压目录带 `-bin-hadoop3` 后缀；
  冷启动约 410s（`timeout` 别按秒级配）。
 报告：[docs/TEST-REPORT-spark-live.md](docs/TEST-REPORT-spark-live.md)。
- **四种湖表格式真跑通过**（Spark 4.1.3 宿主）：Iceberg 1.11.0、Delta 4.4.0、Hudi 1.2.0、Paimon 2.0.0
  各自完成建表 → 写入 → 读回 **3 行 / 60.75** → 账本 → `evidence verify` → **P3 出口门 FAIL=0**。
  三个必须这样配的坑写成可照抄的配置：Delta 要用 `--packages`（单 jar 缺 `delta-storage` 类）、
  Hudi 要 `spark.serializer=KryoSerializer`（否则写入阶段 SparkSubmit 级失败）、
  Paimon 要 `PaimonSparkSessionExtensions`（它自己的检查会报）。
  报告：[docs/TEST-REPORT-lakehouse-live.md](docs/TEST-REPORT-lakehouse-live.md)。
- **Doris / StarRocks 本轮未能起起来：如实标"未验证"**（不是"不兼容"）：
  Doris 的四条路（Hub 直连超时、镜像源 `denied`、Aliyun Apache tarball 404）与 StarRocks 的
  五次尝试（403 / SPA 假 200 / 镜像层反复 retry 后超时）逐条留在
  [TEST-REPORT-doris-unverified.md](docs/TEST-REPORT-doris-unverified.md) 与
  [TEST-REPORT-starrocks-unverified.md](docs/TEST-REPORT-starrocks-unverified.md)；
  历史文件 `TEST-REPORT-doris-live.md`（2026-09-13）保持原样，只作历史记录。
- **ODPS 标注为"不可本地复现"**：`docs/engines/odps.md` 分"能本地验（模板/方言规则/doctor 有单测）"
  与"不能本地验（真跑、odcmd 认证）"两层，并给出跑通所需条件，不再用含糊的"未验证"。
- **新增 `tools/live_probe.py`**：把真跑的引擎无关部分（doctor → ddl render → 建表 → 写入 → 读回 →
  `evidence verify`）固化成一条命令，支持 `--dry-run` 与 `--stop-container`；
  `docs/engines/lab.md` 补上十行"组件配方表"（各引擎的起容器命令与 argv 要点）。
- 新增实验手册 [docs/engines/lab.md](docs/engines/lab.md)：一个组件一次、项目目录先建再挂载、
  项目放 Windows 文件系统（`/tmp` 会随 WSL 重启蒸发）、口令走环境变量、从 `DROP TABLE` 的干净起点开始。

### Added（面向主流大数据组件的任务开发兼容：引擎注册表 + 按方言检查）

离线插件原先只认识两类引擎（MySQL 协议族 + 五个 command 引擎），引擎清单散在
`clients.py` 的两个元组里；更麻烦的是 live 模式下**未登记的 kind 会静默落回
`LivePlatformClient`**——用户写 `kind=clickhouse` 不报错、没提示，拿到的是一个语义
完全不同的客户端，证据级别从服务端 query_id 悄悄降成本地回执。

- **引擎能力注册表 `engine/core/engines.py`（唯一权威）**：每个 kind 登记
  family（dbapi/command/cli/mcp）、方言、必填配置键、默认端口、**证据级别**与可用湖表格式。
  `clients.py` 的两个 kind 元组改为注册表的投影；未登记 kind 一律硬报错并列出可用项。
  本轮登记 19 个 kind：MySQL 协议族（mysql/doris/starrocks/tidb/oceanbase/generic-sql）、
  Hive、Spark SQL、Flink SQL、Paimon、ClickHouse、Trino/Presto、PostgreSQL/Greenplum、
  MaxCompute/ODPS（别名 maxcompute）、通用 command，以及 cli / mcp 两个通道。
- **按方言的 SQL 规则 `engine/core/dialects.py`**：`sql check --dialect <id>`（缺省按
  `platform.kind` 推导）在基线 17 项之上叠加引擎规则。明确写错的是 FAIL
  （Hive 非 ACID 行级写、Trino/Presto 的 INSERT OVERWRITE、MySQL 协议族的 INSERT OVERWRITE、
  MaxCompute/Flink 的 UPDATE/DELETE、ClickHouse 的 UPDATE…FROM），条件性成立的是 WARN + 修复建议
  （Doris/StarRocks 的模型限制、Spark 行级写与整表覆盖、PostgreSQL 的 ALTER TYPE/TRUNCATE、
  ClickHouse 异步 mutation）。**不传方言时行为与改动前完全一致**。
- **配置体检 `platform doctor`**：按注册表逐项判 kind/必填键/profiles 形状/明文口令，
  只判"配没配对"、不判"连不连得上"。分族示例见 `engine/templates/platform.example.json`。
- **类型映射与 DDL 渲染 `engine/core/ddl.py` + `ddp.py ddl render`**：canonical 类型
  （string/bigint/int/double/decimal/boolean/date/timestamp）映到每个方言的物理类型，
  按方言渲染建表模板（Hive/ODPS 的 `STORED AS`、Doris/StarRocks 的 `UNIQUE KEY + DISTRIBUTED BY`、
  ClickHouse 的 `ENGINE/PARTITION BY/ORDER BY`、Trino 的 `WITH (format, partitioned_by)`、
  PostgreSQL 的 `COMMENT ON` 独立语句）；未知类型/未知方言直接报错，
  `--out` 默认不覆盖已有 `ddl/` 文件（`--force` 才覆盖）。渲染产物是**起始模板**，
  仍须过 `sql check` 与真实执行。
- **湖表格式维度 `engine/core/formats.py` + `platform.format`**：Iceberg / Hudi / Paimon / Delta
  登记为与执行引擎**正交**的维度（组合合法性由注册表判定，未登记组合直接报错）。
  每条格式给出写入提示与运维清单（快照过期、compaction、VACUUM、bucket 并发…），
  `ddl render` 按格式插建表片段（spark `USING iceberg`、Flink 注入 `connector`、Trino 说明 catalog），
  `sql check --format` 与 P3 出口门出格式相关的 WARN（Hudi 缺 `primaryKey`、Delta 的 `VACUUM`、
  Iceberg 行级 `DELETE`、Paimon 缺 `bucket`）。格式检查**不阻断**流水线——它们表达"有条件成立"。
- **调度目标 `engine/core/schedulers.py` + `config.scheduler.kind`**：dp_task 的调度注解按目标校验——
  `cron`（默认，与非空 `-- schedule:` 的旧行为逐字一致）、`airflow`/`dolphinscheduler`
  （追加必填 `-- schedule-id:`，建议 `-- schedule-timezone:`）、`dataworks`（建议 `-- schedule-depends:`）、
  `none`（纯执行引擎：不要求调度注解，写了则 WARN 提醒没人消费）；未知 kind FAIL 并列出可用项。
  注解解析同时升级为"取整行"（cron 表达式是多段，旧的单 token 解析会丢信息）。
- **证据级别写进回执**：dbapi/command 族的回执带 `evidence_level=local_receipt`
  （只证明本机执行过，不等于平台 query_id）；CLI 契约族是 `server_query_id`。
- 新增 `tests/test_engines.py`（23 条）：注册表结构自检、投影唯一性、未登记 kind 的硬错误、
  逐 kind 构造适配器、方言规则正反例、doctor 的缺口报告、示例配置可解析、文档不许再抄第二份清单。

### Fixed（多引擎端到端跑出来的两个真缺陷）

### Fixed（真实引擎联调找出来的四个缺陷）

用 Docker 起 PostgreSQL 16 与 MySQL 8.4.4 真跑"建表 → 写入 → 读回 → 证据落账"，
一次揪出四处只有真引擎才会暴露的问题（报告见 `docs/TEST-REPORT-{postgres,mysql}-live.md`）：

- **第 9 条血缘规则误伤纯建表**：`CREATE TABLE` 也被要求 `-- source:`，于是所有新建表 DDL
  在 `platform run-sql` 前就被自己的门拦下。改为只对搬数据的语句（`INSERT` / CTAS）要求血缘。
- **PostgreSQL 分区模板写不出能用的表**：只生成 `PARTITION BY RANGE (ds)`，真写入报
  `no partition of relation found for row`。现在附带一个 `DEFAULT` 分区并出 WARN
  提醒上线前换成真实时间分区。
- **`kind=command` 丢了方言**：方言只看 `platform.kind`，而通用 argv 执行器的真实引擎写在
  `platform.command.engine`。结果 MySQL 建表丢了 `PRIMARY KEY`/`ENGINE`、方言规则一条不生效、
  检查退化成 generic。现在 `dialect_from_config` 与 `platform doctor` 都按 `command.engine` 取方言。
- **执行账本没记证据级别**：`executions.json` 只存 query_id/sql_hash，门与报告看不出这条回执
  是平台 query_id 还是本机回执。现在回执级别随账本一起落盘。
- **`IF NOT EXISTS` 建表被误判**：目标表解析把 `CREATE TABLE IF NOT EXISTS x` 的 `IF` 当表名，
  于是所有带 `IF NOT EXISTS` 的建表都报「文件名与目标表不一致」——而渲染模板恰好全都用
  `IF NOT EXISTS`。现在 `CREATE TABLE [IF NOT EXISTS] name` 与 `INSERT [OVERWRITE|INTO] [TABLE] name`
  都能正确解析（`INSERT INTO t` 不再被误判成"无法识别目标表"，这个旧用例也一并改成新行为）。
- **`--save-evidence` 写的证据文件没有级别**：`etl_dev_exec.json` 只存 status/query_id/env/executed_at，
  出口门里看不到"这是本机回执"。现在一并写 `evidence_level` 与 `engine`。

把 demo 在 7 个引擎 kind 上各跑一遍，暴露了两处只有"换引擎"才会踩到的实现错误：

- **`setup` 会静默清掉用户填好的 config.json**：`context.ensure_app_dir` 每次都用默认值重写
  配置文件，而 demo 内部也会调 setup——也就是说"再跑一次 setup"或"跑个 demo"会把
  `platform` / `knowledge` / `limits` 全部打回默认。现在已存在的配置不再被覆盖
  （只补缺省键、刷新 updated_at），`--mode` 不传就不改已有 mode。
- **P5 按配置 kind 判"文件型平台"，而不是按适配器能力判**：mock 模式下客户端是任务型的，
  但配置写着 `kind=doris/mysql` 时部署代码仍走 SQL 路径，直接
  `AttributeError: 'MockPlatformClient' object has no attribute 'backfill_code'`。
  现在 `PlatformClient.file_sql` 由适配器自己声明（dbapi/command=True，cli/mock=False），
  P5 部署与回滚都按它分流。
- **demo 的执行证据按实标级别**：demo 产物里的 `etl_dev_exec.json` 现在带
  `evidence_level=mock`，治理报告里会显示"证据级别=mock，engine=mock"——
  演示产物不再看起来像真实执行证据。

### Fixed（把"证据"补成"绑定到产物版本的证据"）

对抗式复核发现：引擎已经把 `sql_hash` 写进执行账本，但没有任何出口门读它，
于是"跑过一份 SQL"和"要发布的就是这份 SQL"之间没有约束。实测：P3 出口通过后
把 `etl/` 与 `dp_task/` 的 SQL 整段换成另一个分区口径，P3 出口仍然 FAIL=0 放行。

- **P3/P4 证据绑定产物版本**：新增 `validators.evidence_binds_to_current_sql()`，
  出口门把账本里该 `query_id` 的 `sql_hash` 与当前 `etl/` `dp_task/` 内容比对，
  不一致判 `evidence.binding.stale` FAIL（过门后改代码必须重新真跑）；旧账本缺
  `sql_hash` 时降级 WARN。

### Fixed（补齐后半程的门：数据质量 / 数据回刷）

同一类问题的另外两处复发——证据写下来了、出口门没消费：

- **回刷**：`backfill_runs.json` 原先只有写入方。`validate_phase5_deploy` 现在逐项
  比对 `deploy_plan.json` 的回刷项与账本（key = `表名|start|end`），缺成功记录即
  FAIL；`mode=live` 且适配器不写账本（kind=cli 外部 CLI）时降级 WARN。
  `MockPlatformClient.backfill` 与 `backfill_code` 的无 `bizdate` 单次回刷路径
  一并补上账本写入，避免合法路径被误判。
- **DQC 布防**：`deploy_plan.json` 里每个 `dqc_publish` 项必须在
  `<req_dir>/dqc_results.json` 有 `deployed=true` 的记录，缺即 FAIL。原先 DQC 结果
  只存在于 workflow 返回值与一句自然语言里，既不落盘也不被门消费。
  `workflows/phase5-deploy.js` 的 `next` 与 `agents/dqc-deployer.md` 已同步要求落盘。

### Tests

- 新增 6 个回归用例：P3/P4 证据绑定（一致放行 / 改后拦截）、P5 回刷缺账本、
  回刷范围对不上、DQC 未布防与已布防。

### Fixed（hook 层：修掉"装好了但一道门都不生效"和关键词误伤）

- **非 Windows 装完即静默失效**：`hooks/*.json` 默认用 `py -3`，macOS/Linux 上没有
  这个启动器，hook 命令失败而宿主按 fail-open 放行 → 十几道硬门一道都不生效。
  `tools/adapt_hooks.py` 重写：改为直接改 JSON 里的启动器词（不再靠字符串替换
  `python3 `/`py -3 ` 这种易漏的写法），新增 `--check` 体检模式——启动器不在本机
  PATH 上时返回非零，并打印 `which py / python3 / python` 的实际解析结果。
  README 把"切换启动器"提为安装第 3 步；排障清单加第 0 条（先确认 hook 真的在跑）。
- **CI 现在真的触发 hook**：新增双平台矩阵 job（ubuntu + windows），先 adapt，
  再 `--check`，然后在真 shell 里跑 `tests/test_hook_wiring.py`——它把 `hooks.json`
  里的命令原样取出来、替换 `${CLAUDE_PLUGIN_ROOT}` 后执行，断言"裸调平台 CLI 被拒 /
  只读检索不被误伤 / 无关工具走快速通道"。原 CI 只跑单测，hook 层从来没被真跑过。
- **setup_guard 关键词误伤**：原判定对**整条命令做子串匹配**，命令里出现
  `tracker|platform|quality|openviking` 之一就被当成"未 /setup 裸调平台 CLI"而阻断。
  实测被误拦的都是只读分析：`rg -n "platform" engine/core/sql_platforms.py`、
  `rg -n -i "dqc|quality" ...`、`rg "backfill_runs|platform_tasks" ...`（前一轮评审里
  连续误伤 5 次）。现在新增 `gates.invoked_cli_names()`，只认**段首命令词**，并支持
  `./platform`、`& "C:\tools\quality.exe"`、`FOO=1 sudo tracker`、`echo warmup || platform …`
  等真实调用写法。

### Tests

- 新增 5 个回归用例：段首命令词的 6 种调用写法必须被拦、6 种只读提及必须放行、
  以及 3 个真触发 hook 的接线用例（拒绝 / 放行 / 快速通道）。

### Fixed（CI：跨平台 hook 用例在 ubuntu 上误报失败）

首次把仓库推上 GitHub 后，`test` job 的 ubuntu 两条腿（3.9 / 3.12）红了。
根因不在代码，而在这条用例对"接线坏了"和"还没装好"没做区分：

- `tests/test_hook_wiring.py` 直接执行 `hooks/*.json` 里的命令，而提交的默认值是
  Windows 的 `py -3`。在 ubuntu 上该启动器不存在，命令返回非零、stdout 为空，
  用例的 `assertEqual(proc.returncode, 0)` 就挂了。
- 但"POSIX 上还没跑 `adapt_hooks.py`"是**已知且预期**的状态（README 安装第 3 步），
  不是仓库缺陷。真正该抓的是"跑得起来但决策错了"。

改法：`setUp` 里先看配置的启动器在不在本机 PATH 上，不在就 `skipTest` 并说明找
`tools/adapt_hooks.py --check`。跨平台的真实验证仍在 CI 的 `hooks` job——
它会先 adapt 再跑本用例，ubuntu 与 windows 两条腿都真的执行，不会 skip。

顺手排掉一个同类隐患：`test_repo_invariants` 里的下限语法检查改用
`feature_version=<minor 整数>`（3.8 起的原始 API），不再用元组形式——
这个测试自己要在 3.9 上跑，不能拿旧版本对元组的支持面当赌注。

### Fixed（CI：windows job 的中文输出把 job 打红）

修完 ubuntu 之后 windows 那条腿还是红的，真因是**输出编码**：

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 5-13
  File "...\cp1252.py", line 19, in encode
  print("hook 启动器在本机可用。")
```

Windows 运行器把 stdout 重定向后用的是 **cp1252**，编不出中文；`adapt_hooks.py --check`
的进度行直接把命令打挂（exit 1）。交互式控制台走 PEP 528 的宽字符 API 不受影响，
**所以这个坑只在 CI / 管道 / 重定向时出现**——本机怎么跑都是绿的。

- `tools/{adapt_hooks,reinstall,wrap_marketplace}.py` 启动时把 stdout/stderr 重配成
  UTF-8（`errors="replace"`）。交互式控制台本来就是 UTF-8，这步是空操作；
  被重定向时则从"崩溃"变成"正确输出"。
- 新增回归用例 `ToolOutputEncodingTests`：用 `PYTHONIOENCODING=cp1252` 真跑一次
  `adapt_hooks.py --check`，断言 stderr 里没有 Traceback / UnicodeEncodeError。
  这个条件必须显式造出来，否则本机永远复现不了。
- 本机复现方式（与 CI 报错逐字一致）：
  `$env:PYTHONIOENCODING="cp1252"; py -3 tools/adapt_hooks.py --check`

### Tests（跨插件契约：消费端）

两个仓库都写着"knowledge-base-plugin 是 data-development-plugin 的上游"，但这条链
**一个测试都没有**：本仓库的测试全部用手写的 `viking://resources/...` 字符串，从来没真接过
一次知识库插件的产物。

- 新增 `tests/test_kb_contract.py`（4 个用例）：按知识库插件写出的形状造索引，
  断言 `MockKnowledgeClient.search()` 能按表名命中、`read(uri)` 返回 abstract、
  越界 URI 被拒、消费侧真正读的四个字段（uri/domain/abstract/tables）都在。
- 生产端的对应断言在 knowledge-base-plugin 的
  `tests/test_engine.py::ConsumerContractTests`。两边各自对着同一份 schema 断言，
  两个单仓 CI 都能钉住这条缝，不需要同时 checkout 两个仓库。
- 另做了一次**真跨插件端到端**（两侧各起进程、通过文件系统交接）：知识库插件发布后，
  本仓库确实搜到了 `viking://resources/domains/user/tables/ads_user_act_di.md`。

### Fixed（SECURITY.md 的"已知边界"已经过期，报告渠道是死的）

写 `tests/test_known_limits.py` 时把文档里的声明逐条跑了一遍，结果两个问题：

- **报告渠道指向空气**：原文让人"联系 marketplace 元数据里的邮箱"，而
  `.claude-plugin/plugin.json` 的 `author.email` 是**空字符串**。改为指向 GitHub 的
  **Report a vulnerability**（私密、可贴最小复现），并给了入口不可用时的替代路径；
  维护者须知里写清那个入口要在 Settings → Security 里开启。
- **已知边界清单过期**：原文列的四条"可绕过"实测**三条早就拦住了**——
  `& $c` 间接调用、环境变量拼路径、从别的目录写回。逐条实测后重写成准确版本，
  并新增 `tests/test_known_limits.py`：已拦住的 8 种形态当回归保护，唯一还开着的
  （把写操作放进脚本文件再执行）当"已知上限"钉住——改任一侧都会红。

### Fixed（署名统一 + 英文门面）

- LICENSE 写「AI实战技能圈」、两个清单写「data-development-plugin maintainers」；
  现在统一成 LICENSE 的署名，并删掉空的 `author.email` 字段。
  新增不变量断言：署名三处一致，且 `email` 要么填真值要么不存在——空字符串正是
  SECURITY.md 指向空气的原因。
- `README.en.md` 从 "A Claude Code plugin" 改成双宿主，补了安装三条
  （含 **macOS/Linux 必须跑 `adapt_hooks.py`** 这条），并链到 SECURITY / CONTRIBUTING / CHANGELOG。

### Added（开源标配）

- `.github/ISSUE_TEMPLATE/bug_report.md`：第一段就要求先跑 `adapt_hooks.py --check` 与全量测试
  ——这两条能把"本机没装好"和"插件真坏了"分开，正是这一路反复撞的那类问题。
- `.github/PULL_REQUEST_TEMPLATE.md`：自查项与 CONTRIBUTING 的三道门一一对应。
- CONTRIBUTING 新增 Releasing 一节：版本号唯一来源、cachebuster、新线程生效。

### Fixed（引擎在非 UTF-8 输出下会崩）

`py -3 engine/ddp.py --help` 在 cp1252 下直接 UnicodeEncodeError（实测）——和刚修好的
`adapt_hooks.py` 是同一个坑：HELP 与中文提示在被重定向时编不出来。引擎启动时同样把
stdout/stderr 重配成 UTF-8；回归用例扩展为 `test_engine_help_survives_non_utf8_stdout`。

### Added（补上"改完代码怎么让宿主加载"这条路）

原先仓库里没有一条可走的重新安装路径：`tools/build_codex_surface.py` 的注释要求版本
后缀由 `update_plugin_cachebuster.py` 维护，但那个脚本属于 Codex 的 plugin-creator
系统 skill，**不在本仓库**。clone 下来的人改完代码，宿主仍然跑旧副本。

- 新增 `tools/reinstall.py`：按 `commands/` 重生成 Codex 面 → 校验 marketplace 条目
  是否**指向本仓库** → 把版本换成 `<基础版本>+codex.<UTC 时间戳>` → 跑
  `codex plugin add <插件>@<marketplace>`。默认只预演，`--apply` 才动手；每一步
  不满足都明确报错而不是装作成功。
- 记录一个容易踩的坑：Codex 把 marketplace 的 `source.path` 解析成相对**用户主目录**
  的路径，不是相对 `marketplace.json` 所在目录。按后者解析得到的是
  `.agents/plugins/plugins/...`——那个路径并不存在。本机真实布局是目录联接
  `~/plugins/data-development-plugin -> 本仓库`。
- README 新增「改完插件代码，怎么让宿主加载新版本」一节，写明"重装不影响 hook 信任、
  但要新开线程"。
- 新增 2 个用例锁定 cachebuster 拼接与路径解析口径（这两处错了会静默装一个旧副本）。

### Added（给生成物加同步门）

`tools/build_codex_surface.py` 从 `commands/*.md` 生成 12 个文件（Codex 清单、共用
hooks、9 个 `ddp-*` 入口技能、AGENTS 模板），但 tests 和 CI 里没有任何一处核对它们
同步。改完 command 忘了重跑生成器，两个宿主就会跑出不同的流程，而且不报错。

本次任务里它已经漂过一次：仓库里的 `.codex-plugin/plugin.json` 是 `\uXXXX` 转义，
生成器输出的是 UTF-8 原文——内容等价但字节不同，说明那份清单不是这个生成器写出来的。

- 新增 `tests/test_generated_surface.py`：把生成器输出**截在内存里**（替换 `write`，
  不落盘），与仓库文件逐字节比对，不一致即 fail 并打印是哪几个文件、差多少字符；
  另一个用例断言生成器仍然覆盖全部 12 个文件，防止"少生成几个"也能通过比对。
- 顺带重跑一次生成器，消掉上面那处转义差异。现在 12/12 一致。
- 已做正对照：人为往一个生成物里加一行，测试按预期失败并给出精确文件名，还原后恢复通过。
- CI 主 job 跑 `unittest discover`，自动覆盖本测试。

### Fixed（版本号收敛到单一来源 + 引擎清单进 CI）

**DEVEX-002**：基础版本原先写在四个地方——`tools/build_codex_surface.py` 的
`VERSION` 常量、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、
`CHANGELOG.md`——没有任何检查。加了 `tools/reinstall.py` 之后更危险：它只改
`.codex-plugin` 的版本，另外三处不会跟着动。

- 生成器不再硬编码版本：`build_codex_surface.py` 改从 `.claude-plugin/plugin.json`
  读基础版本（读不到就明确报错），其余位置由它派生。`VERSION` 常量已删除。
- `tests/test_repo_invariants.py` 断言三者一致：Claude 清单版本 == Codex 清单里
  `+` 之前的基础版本 == CHANGELOG **最新已发布**小节（跳过 `[Unreleased]`，
  这点不写清会误报）。另加一条：`+` 后面只允许 `codex.` 前缀的 cachebuster。

**DEVEX-003**：`engine/.engine-manifest.json` 有人生成、没人核对。CONTRIBUTING 要求
改完 engine/ 必须重跑，但 CI 不检查——忘了重跑，每个贡献者 `/setup` 之后的
engine_integrity 自检都会失败，错误暴露在用户侧而不是 CI。

- `tools/generate_engine_manifest.py` 拆出纯函数 `build_manifest()`，`main()` 只负责写盘。
- 同一测试文件里比对清单与 engine/ 当前内容，能分别报出"清单里有但文件没了 /
  文件有但清单没登记 / 内容变了没更新"。

两个检查都**只读不写**——写成"跑生成器再比对"会自己把漂移修好，然后永远通过。
已做正对照：把 `.claude-plugin` 版本改成 9.9.9、往 `engine/core/spec.py` 追加一行，
两条检查都按预期失败并指名问题，还原后恢复通过。

### Fixed（证据保鲜期贯穿全链路，P5 补上绑定）

**PIPE-003**：P3 出口走的是完整的 `execution_evidence_ok`（status + 非空 query_id +
env + `executed_at` 时效 + 账本命中），而 P4 的前置检查只有一行
`exec_ev.get("status") != "SUCCESS"`，P5/P6 完全不看。结果是：一份 24 小时有效期的
证据，只要在 P3 通过的那一刻还没过期，之后搁置多久都能一路放行到发布。

- P4 前置改调 `execution_evidence_ok`，时效与账本命中一并复验；`query_id` 不在账本里
  单独判 `test.precondition.etl_ledger`。
- **P5 补上绑定**：原先 P5 只查 `deploy_plan` / `delivery_summary`，不碰 ETL 证据——
  于是过 P4 之后又改 ETL 照样能部署。现在 P5 也会把账本指纹与当前
  `etl/` `dp_task/` 内容比对，不一致即 FAIL。
- **P5 刻意不重复判时效**，判断依据写进了 `docs/ARCHITECTURE.md` 的阶段处理表：
  时效回答"dev 什么时候跑的"（P4 已判），绑定回答"要部署的是不是被测过的那一份"
  （P5 必须仍然成立）。让 P5 也判时效会把跨天推进的正常工单拦掉，而它要部署的代码
  已经被 P4 验证过。

### Tests

- 新增 2 个用例：证据超过 30 小时（跨过保鲜期）时 P4 必须 FAIL；P4 之后改 ETL 时
  P5 必须 FAIL（改之前 P5 不判绑定，改动确实拦不住）。

### Fixed（引擎清单对行尾免疫——"别人 clone 下来"不再当场报错）

验证"能不能被他人 clone"时实测到的真 bug：**新克隆的 `engine_integrity` 全线不一致**。

```
工作副本: engine/core/validators.py CRLF=745 / 总行数=1165
新克隆  : engine/core/validators.py CRLF=0   / 总行数=1165
新克隆 engine_integrity: 不一致 -> core/clients.py, core/context.py,
                                  core/gates.py, core/spec.py, core/validators.py
```

根因：`.engine-manifest.json` 存的是文件**原始字节**的 sha256，而文件的字节受 git
行尾规范化影响。本机 `core.autocrlf=true` 得到 CRLF，新克隆按 `.gitattributes`
（`eol=lf`）得到 LF，同一份仓库字节不同 → 清单必然对不上。这个仓库最不能出的一类
问题：**声明与事实脱节**，而且错误暴露在别人那里。

- `context.sha256_file()` 改为**行尾先归一为 LF 再哈希**（引擎文件最大 68KB，整读即可；
  分块读会有 `\r\n` 被切在块边界上的风险）。
- `tools/generate_engine_manifest.py` 删掉自己那份内联字节哈希，改为调用
  `context.sha256_file`——生成器和自检必须用同一个函数，否则跨平台必然对不上。
- 新增 2 个用例：同一段内容写成 LF 与 CRLF 必须得到同一个哈希；生成器的输出必须等于
  `context.sha256_file` 的结果。
- 修改 `.gitattributes` 的注释，把"因此必须按归一化后的内容哈希"写清楚，避免以后有人
  把哈希改回字节比较。

### Fixed（架构设计真正约束研发交付物，PIPE-002）

对抗式复核的结论：**P2 对 P3 零约束**。`validate_phase2_architecture` 只检查文档存在、
含 `Lx`、L2/L3 有链路图、无占位符，从不解析表清单；`validate_phase3_data_dev` 只校验
`ddl/` `etl/` `dp_task/` 三个目录**彼此**表名一致，从不读 `architecture_doc.md`。
`rg architecture_doc engine` 也证实：除 P2 门自己和 demo 生成器外没有任何读取方。
结果是设计 A/B/C、实际造 X/Y/Z，两道门全部放过。

- `architecture_doc.md` 必须带一个 ```json 机读块声明交付表清单
  （`{"tables":[{"name":"..."}]}`）。只强制 `name`；`action`/`layer` 允许写但不校验。
- P3 出口做**等值**校验：`dev.arch.table_missing`（声明了没造）/ `dev.arch.table_extra`
  （造了没声明）分别报出；另加 SDS `target_table.name` 必须在交付表里
  （`dev.sds_target_not_built`，用现有数据白捡的一条强校验）。
- 选 JSON 机读块而不是 Markdown 表格：`context.extract_json_block` 已经存在（SDS 终版
  贴单在用），零新解析逻辑、零歧义；表格解析要额外处理表头命名、对齐行、转义竖线三处边界。
  顺带把 `extract_json_block` 从 `ddp.py` 提到 `core/context.py`，两边共用一份。
- **中途增减表必须回跳 P2 改清单**（`advance --allow-back --reason`），不允许在 P3 里
  偷偷改设计。这是有意的节奏变化：架构是契约，砍表也要留记录。
- demo 生成器一并补上机读块；`skills/architecture-design`、`commands/start.md`
  写明要求（改了命令所以重跑了生成器）。

### Tests

- 新增 6 个用例：设计一张造一张必须全绿；P2 缺机读块 / 空数组 / 缺 name / 重复表名 /
  非法 JSON 都要 FAIL；P3 少造、多造、SDS 目标表不在交付表里都要 FAIL。

### Fixed（Python 下限从"声称"变成"验证过的"，ENG-002）

README 写"Python 3.9+"，但没有任何地方机器可读地声明它，**CI 也只跑 3.11**——
属于对外声明了一个没被验证过的下限，和之前版本号三处不一致是同一类问题。

- CI 主 job 改成版本矩阵 `["3.9", "3.12"]`（3.9 是承诺的下限，3.12 对齐主力开发机），
  两端都跑全量用例。原 hooks job 的 3.11 一并统一到 3.12。
- `tests/test_repo_invariants.py` 新增两条：README 声明的下限必须出现在 CI 的
  `python-version` 里（标量与矩阵写法都认）；用下限版本的语法 `ast.parse` 全部 29 个源文件，
  提前拦住 3.10+ 才有的写法。
- 加矩阵之前先做了本地预检，三项都干净：`ast.parse(feature_version=(3,9))` 全过、
  没有 3.10+ 专属的标准库 API（`tomllib` / `itertools.pairwise` / `zip(strict=)` /
  `dataclass(slots=True)` / `datetime.UTC` / `StrEnum` / `bit_count` / `file_digest` …）、
  没有会运行时求值注解的写法（`cast` / `get_type_hints`），且**每个带注解的文件都带
  `from __future__ import annotations`**（这条是 `str | None` 能在 3.9 上安全存在的唯一原因）。
- 没有引入 `pyproject.toml`：这个仓库不是发行包（没人 `pip install` 它），
  加一个 `[project]` 表反而会多出第 4 处手写版本号，正撞 DEVEX-002 的枪口。
  "CI 矩阵 + README + 一致性测试"是等价且更强的方式——它验证而不是声明。

## [1.2.2] — 2026-09-11

### Added
- **双宿主：同一个插件现在也能在 Codex 里跑**。
  - `.codex-plugin/plugin.json`（Codex 清单）+ `hooks/claude-codex-hooks.json`
    （两宿主共用 hook 配置，Codex 与 Claude Code 共用 `PreToolUse` /
    `PostToolUse` / `SessionStart` 契约与 `CLAUDE_PLUGIN_ROOT` 注入）。
  - `skills/ddp-*`：Codex 没有斜杠命令，用户入口是技能，因此每个命令都有一份
    等价入口技能（`/setup` → `ddp-setup`，`/start` → `ddp-start` …）。
  - `tools/build_codex_surface.py`：从 `commands/*.md` 生成 Codex 面（清单、hooks、
    入口技能、`engine/templates/AGENTS.ddp.md`），生成物带「请勿手改」抬头。
  - `engine/templates/AGENTS.ddp.md`：Codex 读 `AGENTS.md`，`/setup` 按宿主
    （`PLUGIN_DATA` 存在即 Codex）注入对应文件名，Codex 跳过 `.claude/settings.json`。
- `tools/adapt_hooks.py` 现在同时适配 `hooks/hooks.json` 与
  `hooks/claude-codex-hooks.json`（POSIX 切 `python3`，Windows 保持 `py -3`）。

### Notes
- 安装方式（本机个人 marketplace）：`codex plugin add data-development-plugin@personal`。
- 宿主差异：Claude Code 的 `Task` 在 Codex 用 `spawn_agent`；
  `AskUserQuestion` 改为对话内直接问。引擎与 hook 层完全共用，硬门语义不变。
- **Codex 侧 hook 需要一次性信任**：技能装好即可用，但宿主会在首次加载时把插件的
  hook 登记到 `config.toml` 的 `[hooks.state."data-development-plugin@personal:…"]`
  并请求授权（在 Codex 应用里新开线程可见）。自动化可用
  `codex exec --dangerously-bypass-hook-trust`。
- `engine/core/gates.py` 的 PreToolUse 分派新增 Codex 工具名支持：
  `apply_patch`（从 `*** Add File:` / `*** Update File:` / `+++ b/` 里抽取目标路径后走同一套
  `decide_file_write`）与 `exec_command` / `local_shell` / `shell_command`（命令类走
  `decide_cli_command`）；新增 5 个 Codex payload 单测。
- 引擎内兜底：不依赖宿主 hook 的关键门控（`platform *` 动词内先 `check_gate`、
  证据 query_id 必须命中执行账本）在两个宿主里都生效，因此"没跑就写证据/手写 query_id"
  这类作弊在 Codex 里同样过不去。

### Fixed（Codex 实机联调第二轮）
- **hook 输出必须严格贴宿主 schema**：Codex 的 `pre-tool-use.command.output` 是
  `additionalProperties: false`，我们在 `hookSpecificOutput` 里多写的
  `decision` / `shouldBlock` / `permissionDecisionDetails` 会让宿主**整条判为非法并静默放行**。
  现在只输出 schema 允许的键（顶层 `decision` + `reason` + `hookSpecificOutput`），
  并改用 exit 0 + JSON 决策（exit 2 只对"把原因写 stderr"的旧协议有效）。
  `sessionstart.py` 同时补上了缺失的 `hookEventName`。
- **hook 超时会导致 fail-open**：Windows 上"python 启动 + 导入整个引擎"实测约 20s
  （裸 `python -c pass` 就要 3.7s，机器负载高时更久），而 hook 超时是 20s/10s →
  超时即放行。超时调整为 PreToolUse/PostToolUse 60s、SessionStart 30s；
  同时 PreToolUse 增加快速通道：只有 Bash/Write/Edit/apply_patch/exec_command 与
  MCP 类工具才惰性导入引擎，其余工具直接放行。
- **Codex `apply_patch` payload 形状**：补丁文本在 `tool_input["command"]`（不是
  `input`/`patch`），且路径是相对路径；门控现在按 payload 的 `cwd` 解析后再判
  `workspace_guard` / `runtime_guard` / `engine_readonly` / `dev_order_guard`。
  新增 2 个用**真实抓取 payload**做的回归测试，并加了"hook 输出键集合不得超出 schema"的测试。

### Notes（Codex hook 信任）
- 修改插件的 hooks 文件（如调超时）会让 Codex 里已登记的信任**失效**——需要在
  Codex 应用里新开线程重新授权；仅重装插件（cachebuster）不会失效。
- 排查用：`DDP_HOOK_TRACE=<file>` 环境变量可把两个宿主发给 hook 的原始 payload 落盘，
  便于对齐宿主差异。

### Added / Fixed（按实机联调建议落地）
- **`code_diff.required` 有了真实触发点**：`platform update-task`（改现有任务）会自动置位，
  不再只有 demo（进程内）能置位——live 场景下"改现有任务必须比对线上基准"这道门不再休眠。
- **live 模式补上"线上基准"通道**：新增 `baseline capture|show`，从数据平台读回当前已发布
  任务代码写入 `.data-dev/runtime/baseline/<工单>/`，`diff` 优先用它比对
  （mock 模式的 `.data-dev/mock/baseline` 保留）。
- **修复纯 CLI 场景的死锁**：`update_task_read_guard` 依赖的 read 账本原先只由 PostToolUse
  hook 写入，没有 hook 的宿主里 `update-task` 永远过不去；现在引擎自己的
  `platform read-task` 也会记读（`gates.mark_read`）。
- **只读 SQL 不再被"目标表"规则误杀**：SELECT/WITH 等只读语句不再强制要求 `-- table:`
  注解、也不再拿文件名比对目标表（P4 用例 `tc_0001.sql` 这类命名从此合法）；写类 SQL
  规则保持不变。
- **SessionStart 提示修正**：版本号改为从插件清单动态读取（原来写死 v1.1.0），提示里的
  命令名按宿主显示（Codex 显示 `ddp-setup`/`ddp-submit`，Claude Code 显示 `/setup`/`/submit`）。

### Fixed（Codex 宿主全流程 P1→P6 实测暴露，第 6 轮）

在 Codex 里用 `ddp-*` 技能连跑完整六阶段（mock 模式）时暴露并修复：

- **P1 缺 CLI 入口**：`state.enter_sds()` 早已存在但没有任何调用方，
  `advance --phase 1` 又被 `unsupported_phase` 挡住。新增 `sds enter --mode verify|generate`。
- **`apply_patch` 内容被误判**：门控把**整段补丁文本**当文件内容做 JSON 校验，
  写 `sds_design.json` 必然报"不是合法 JSON"。新增 `_patch_file_content()`，
  只取该文件段落并剥掉 `+` 前缀。
- **Windows 下 `verification/` 阶段门失效**：`str(Path)` 是反斜杠，前缀比较用正斜杠 →
  归一化为 `Path.as_posix()`。
- **`sds publish` 被锁死**：引擎的 P1 终版贴单与平台发布共用 action token，
  却被要求提供研发期证据键与 `[已确认发布]` token。`submit_guard` 与 `publish_gate`
  现在识别 `ddp.py sds publish` 并跳过。
- **命令串联污染 `--ticket`**：`_extract_flag` 不切分 `;`/`&&`/`||`，短参 `-t` 还会命中
  `update-task`（实测把 ticket 解析成 "ask"）。现在按分隔符切段，且短参要求前面是空白。
- **语义层摘要含假信息**：`verb_semantic` 是写死模板，带与本单无关的模型名和
  "kb-publish: [已发布] OV 可见"。改为从 `sds_design.json` 取目标表/字段/知识源，
  并如实标注 mock 未执行发布、真实可见性以 knowledge-base 插件产物为准。

### Fixed（Codex 宿主复测暴露：shell 写入绕过）

- **Bash 改写机器资产的门控被 PowerShell 绕过**：原有"敏感路径 + 变更动词"检查只认
  `rm/mv/write_text` 一类，实测模型用 `New-Item` + `Set-Content` 直接写
  `.data-dev/runtime/` 成功落盘（先被 apply_patch 拦、再用 shell 绕过）。
  现补齐写文件动词表：`set-content` / `add-content` / `out-file` / `new-item` /
  `clear-content` / `copy-item` / `cp` / `tee` / `touch` / `dd` / `sed -i`，以及
  shell 重定向 `>` `>>`；同时把"shell 写工单工作区外的 `ddl|etl|dp_task`"
  也纳入 `workspace_guard`。读取类命令（`Get-Content` / `Test-Path`）保持放行。
- 复测方式与结论：在 Codex 里明确要求"用 PowerShell 而非 apply_patch"写
  `.data-dev/runtime/retest2.json` → 宿主返回
  `Command blocked by PreToolUse hook: [data-development-plugin:runtime_guard] …`，
  文件未落盘，日志出现 `hook: PreToolUse Blocked`。

> 宿主 hook 状态词的含义（实测）：`Completed` = 放行，`Failed` = hook 自身出错/超时，
> `Blocked` = 决策为 deny 且宿主采纳。排查时先看是 Blocked 还是 Failed。

### Fixed（开源前对抗式验收：第一性原理逐条证伪）

用一套 33 条的对抗脚本（路径变形 / 工具变形 / shell 变形 / 证据伪造）去打硬门，
发现并修复：

- **`apply_patch` 的 `*** Move to:` 目标路径未被检查**：可用
  `Update File: ok.txt` + `Move to: .data-dev/runtime/evil.json` 把文件"搬进"受保护目录；
  反向也能把 `.data-dev/engine/ddp.py` 搬走。现在 Move 的源路径与目标路径都纳入门控
  （`runtime_guard` / `engine_readonly`）。
- 同批验证通过的既有防线：绝对/相对/反斜杠/`./`/`../`/大写/尾随空格等路径变形、
  `Set-Content`/`Out-File`/`New-Item`/`cp`/`mv`/`tee`/`sed -i`/`python -c open(...,'w')`
  等写文件方式、shell 重定向、工作区外 SQL 目录写入，全部 BLOCK；
  只读命令（`Get-Content`/`Test-Path`/`Get-ChildItem`）与常规命令保持 ALLOW；
  在 live 模式下**手写 `etl_dev_exec.json` 的 query_id 会被执行账本交叉核对拒绝**。

### Changed（开源前清理）

- **版本号三处对齐到 1.2.2**：`.claude-plugin/plugin.json` 之前落后在 1.1.0，
  Codex 清单与 CHANGELOG 也不一致；生成器现在只保留 `+codex.<token>` 后缀，
  基础版本统一由 `tools/build_codex_surface.py` 的 `VERSION` 决定。
- **测试报告脱敏**：`docs/TEST-REPORT-doris-live.md` 里的本机用户名、家目录、
  内网地址全部替换为占位符（生成器里加了 `sanitize()`，重跑不会回潮）。
- README 里过时的测试数量（27 / 85）更新为当前的 113。
- 跨平台验证：同一套测试在 **Linux（WSL, Python 3.13）** 下 `demo` 成功、
  113 个单测全绿；Windows 侧同样 113 全绿。

### Fixed（对抗式验收 v2：路径归一化与载荷形状）

第二轮对抗从"宿主会怎么把路径喂进来"下手，又打出一类真实绕过：

- **Windows 路径归一化怪癖**：文件系统会忽略路径分量上的**尾随点与空格**
  （`runtime.` 就是 `runtime`、`_state.json.` 就是 `_state.json`），
  因此 `.data-dev/runtime./x.json`、`.data-dev/tickets/T-0001/_state.json.` 这类
  等价写法能绕过 `runtime_guard` / `state_write_guard`。
  现在对每个路径分量做 `rstrip(" .")` 归一化，再参与比较。
- **载荷形状兜底**：工具名缺失/没见过、或 `tool_input` 不是对象（列表/字符串）时，
  具名分支取不到 `file_path` 会直接放行。现在加了**形状兜底**：
  只要载荷里出现"写哪个文件/跑什么命令"的形状就照常过门；形状异常却提到机器资产时
  保守拦截（这是唯一一处 fail-closed）。
- 新增 3 个回归用例（Windows 尾随点/空格、形状兜底），单测 113 → **116**，
  对抗脚本 v2 **28/28**、v1 **33/33**。

### Fixed（关键词误判：按段匹配）

- 原先"命令里同时出现机器资产路径与写文件动词就拦"是**整条命令**匹配，
  跨行的分析脚本会被误伤（实测被误拦过一次：一行 `shutil`、另一行提到 `_state.json`）。
  现在按 `;` / `&&` / `||` / 换行**切段**，要求写动词与敏感路径落在**同一段**才判违规——
  真实写入命令天然同段，分析脚本不再误伤。新增 2 个回归用例（同段拦截 / 跨段放行），
  单测 116 → **118**。

### Fixed（按段匹配的漏网：变量间接与 cd）

第一版"按段匹配"改完，宿主实测立刻打回一个**自己引入的回归**：

```powershell
$t = Join-Path $PWD '.data-dev\runtime\final-check.json'   # 路径在第 1 段
Set-Content -Path $t -Value '{}'                            # 写命令在第 2 段，只出现变量名
```

两段分开看都"无辜"，于是被放行。补两条规则：

- **变量展开**：把同一条命令里的简单赋值（`$t = ...`）展开到后续段，再做同段判断；
  展开时用 lambda 替换，避免 Windows 路径里的反斜杠被当成正则转义。
- **`cd` 进机器资产目录**：`cd .data-dev/runtime` 之后的任何写操作（相对路径）一律拦，
  因为段里看不到绝对路径但意图明确。

宿主复验（Codex 实机）：无害的多行分析命令 `hook: PreToolUse Completed`（放行），
上面那串变量间接写入 `hook: PreToolUse Blocked` + `runtime_guard`，文件未落盘。
单测 118 → **120**。

### Docs

- `SECURITY.md` 增补「Security model」与「Known limits / non-goals」：
  明确三层防线强度不同（hook = 提高作弊成本、引擎内门控 = 绕不开、执行账本 = 证据可信度的真正来源），
  并如实列出边界（启发式命令判定不是 shell 解释器、宿主看不到的子进程副作用、
  live 适配器只给执行回执、不是沙箱/RBAC），附生产使用建议。

## [1.2.1] — 2026-09-10

真实 Apache Doris 端到端联调（live 模式：tracker = JSON CLI，platform = kind=doris，
dev/prod 双库）暴露并修复的缺陷：

### Fixed
- `ticket create --description-file` 实际不可用：`parse_args` 只把 `--description-file`
  存成连字符键，而 verb 读的是 `description_file`。现在两种键名都写入，`--save-evidence`
  一类连字符读取保持兼容。
- **live 模式账本路径不一致（阻断级）**：引擎把执行账本写到 `.data-dev/.runtime/`，
  而 P3/P4 校验器读 `.data-dev/runtime/`，导致 live 模式出口门恒 FAIL
  （`dev.exec_evidence.no_ledger_store`）。新增 `context.LEDGER_DIR` /
  `context.ledger_dir()`，引擎账本（executions / platform_tasks / backfill_runs /
  sql_runs）统一落在 `.data-dev/runtime/`；hook 账本仍在 `.data-dev/.runtime/`，
  `runtime_guard` 同时拦两处。
- `Pipeline.advance` 把任何非空门控结果都当阻断，WARN（如"新表场景无线上基准"）
  也会卡死转场。现在只有 `FAIL` / `HALTED` 阻断。
- `platform run-sql` / `create-task` / `update-task` 在送门控前把 SQL 截断到
  200/100 字符，长 SQL 必然被误判"括号不配对"并拒绝执行。改为传全文。
- **hook 输出在 Windows 上整段乱码**：`hooks/common.py` 的 `emit()` 用
  `ensure_ascii=False` 直接写 stdout，Windows 下按 cp936 落字节，而 Claude Code
  按 UTF-8 解析 → 拦截原因显示成乱码（实测 `dev_order_guard` 的拒绝信息）。
  改为 ASCII 转义 JSON，宿主 `json.loads` 后还原中文，且与终端编码无关。

### Added
- `cli.<name>` 接受 argv 列表（如 `["py", "-3", "tools/tracker.py"]`）：Windows 上
  脚本类 CLI 若经 `.cmd` 包装器转发，多行内容参数会被 cmd.exe 按换行切断，
  后续参数（例如 `--anchor`）整段丢失；列表形式直接调用解释器可绕开。

## [1.2.0] — 2026-09-10

### Added
- Live knowledge retrieval now speaks the real OpenViking contract:
  `POST /api/v1/search/find` (HTTP direct) or native `ov find … -o json` /
  `ov read … -o json` (CLI). Results are normalized to
  `{uri, domain, layer, abstract, score, level}`.
- `engine/core/ov_knowledge.py` pure-stdlib HTTP client with
  `knowledge.ov.url` / `OV_API_KEY` (env or key file), account/user headers,
  timeout, level and limit controls.
- `knowledge.kind`: `auto` (default; URL set → HTTP, otherwise ov-CLI),
  `http`, `ov-cli`, `custom-cli` (keeps the legacy `search --query` /
  `read --uri` wrapper contract).
- `MockKnowledgeClient` honors configured `knowledge.uri_prefix`, and
  `LiveKnowledgeClient.read` enforces the same prefix before any network call.

## [1.1.0] — 2026-09-10

### Added
- Pluggable SQL data-source adapters: MySQL protocol family
  (mysql/doris/starrocks/tidb/oceanbase), command family
  (paimon/flink/spark/trino/presto), reserved `kind=mcp`.
- Environment profiles (`platform.base_env` + `platform.profiles.<env>`);
  deploy env grading (`dev|test|qa|staging|prod`) with prod-only confirmation.
- `modeling.layer_policy` and `semantic.enabled` flow switches.
- `evidence verify|selfcheck|confirm`, execution ledger
  (`.data-dev/runtime/executions.json`) with cross-checks.
- Backfill resume ledger (`backfill_runs.json`), rollback support
  (`rollback_sql_file` + `ddp.py rollback`).
- `engine sync`, `status --gate`, `report` governance summary.
- Integrity hardening: `state_write_guard`, `runtime_guard`,
  `engine_readonly`, `engine_integrity` manifest self-check.
- `tools/adapt_hooks.py` for POSIX hook launcher.

### Changed
- Renamed/removed vendor branding; connectors are config-driven.
- Strict JSON contract for live CLI (opt out via `live.allow_text=true`).

### Fixed
- Raw `ctx set` no longer writes machine evidence keys.
- `_state.json`/engine/runtime files cannot be modified through tool calls.
