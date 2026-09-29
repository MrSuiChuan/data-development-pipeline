# 真实引擎联调报告：湖表格式（Iceberg / Delta / Hudi / Paimon on Spark 4.1.3）

**目的**：COMPAT-011 把四种湖表格式登记成与引擎正交的维度，但当时一条真跑都没有。
本报告在宿主 Spark 4.1.3（local 模式）上把四种格式逐个跑通"建表 → 写入 → 读回 → 证据落账 → P3 出口门"。
对应台账 LIVE-005。

**环境**：WSL2 Debian 13 / OpenJDK 21 / `~/oss/spark-4.1.3-bin-hadoop3`
构件走阿里云 Maven 镜像｜**日期**：2026-09-29
**纪律**：一次只跑一个格式；每个格式一个独立工作目录（见第 4 节的教训）

## 1. 结果总览（四个格式全部 FAIL=0）

| 格式 | 版本 | 表名 | 读回 | P3 出口门 | 关键配置 |
| --- | --- | --- | --- | --- | --- |
| Iceberg | 1.11.0（`iceberg-spark-runtime-4.1_2.13`） | `lake.default.ods_orders_live` | **3 行 / 60.75** | **FAIL=0** | hadoop catalog：`spark.sql.catalog.lake.type=hadoop` + warehouse |
| Delta | 4.4.0 | `default.ods_orders_live` | **3 行 / 60.75** | **FAIL=0** | `--packages io.delta:delta-spark_4.1_2.13:4.4.0` + DeltaSparkSessionExtension |
| Hudi | 1.2.0（bundle） | `default.ods_orders_live` | **3 行 / 60.75** | **FAIL=0** | `USING hudi` + `TBLPROPERTIES('primaryKey','preCombineField','type'='COW')` + **Kryo** |
| Paimon | 2.0.0 | `paimon.db.ods_orders_live` | **3 行 / 60.75** | **FAIL=0** | `--packages org.apache.paimon:paimon-spark-4.1_2.13:2.0.0` + PaimonSparkSessionExtensions |

每个格式都是同一套动作：`platform doctor` → `ddl render`（`USING <fmt>` 由注册表与模板给出）
→ `platform run-sql --save-evidence` 建表 → 同样方式写入三行 → 用 spark-sql 直接查回
→ 检查执行账本与 `evidence verify` → 跑真实 P3 出口门（补 dp_task/架构机读块/证据键后）。

## 2. 三个"必须这样配"的坑（都是真跑撞出来的）

1. **Delta 4.x 不能只挂一个 jar**：单 `--jars delta-spark…jar` 建表时报
   `NoClassDefFoundError: io/delta/storage/commit/actions/AbstractProtocol`——Delta 把
   `delta-storage` 等拆成了独立构件。改用 `--packages` 让 Spark 解析传递依赖后一次通过。
2. **Hudi 1.2 要 Kryo**：不加 `spark.serializer=org.apache.spark.serializer.KryoSerializer` 时，
   建表能过、**写入阶段直接 SparkSubmit 级失败**（0 行、账本里只有建表记录）。
3. **Paimon 2.0 强制检查扩展**：catalog 初始化会抛
   `RuntimeException: … it is necessary to configure spark.sql.extensions and ensure that it includes
   org.apache.paimon.spark.extensions.PaimonSparkSessionExtensions`——把扩展配上即可（它自己给了提示）。

## 3. 模板与注册表的表现

- `ddl render` 对 `spark + <fmt>` 输出的就是 `USING iceberg|delta|hudi|paimon`，
  加上 spec 里的 TBLPROPERTIES（Hudi 的主键/预合并字段、Paimon 的 bucket/primary-key）——**真引擎全部接受**；
- `platform doctor` 会显示 `format` 与 `dialects/format 组合`校验结果；
- 格式相关的 WARN（如"Paimon 建表没显式 bucket"）在配置了 properties 后不再触发，与规则描述一致。

## 4. 一条 lab 级教训：Spark 的 metastore 是"按当前目录"的

第一次跑 Delta 时读回是 **6 行 / 121.50**（应为 3 行）：Spark 默认 catalog 用 **cwd 下的 Derby
metastore**，而前一个（普通 Spark）格式的项目已经把 `default.ods_orders_live` 注册在同一个
metastore 里——于是 `CREATE TABLE IF NOT EXISTS … USING delta` 变成 no-op，INSERT 追到了那张
Parquet 表上。修法：**每个格式一个独立工作目录**（本项目用 `~/oss/lab/<fmt>`），
`spark.sql.warehouse.dir` 也指到那里；注意这个目录要在 Linux 文件系统上——
放在 `/mnt/c`（drvfs）上 Derby 直接起不来。

这条也解释了"为什么 3 行的表会变 6 行"这类看起来像插件重复执行的假象：
**账本里每次 SQL 只有一条记录**（sql_hash 计数可查），插件没有重复执行。

## 5. 结论与边界

**已证明**：四种湖表格式在 Spark 4.1.3 上都能用插件渲染的模板完成建表+写入+读回，
账本与 `evidence verify` 通过，P3 出口门 FAIL=0；三种"单 jar 不够/B需要 Kryo/需要扩展"的坑
成为可照抄的配置。

**未证明**：Trino/Flink 侧的湖表（Trino 需要 iceberg/hudi/delta connector 的 catalog 配置；
Flink+Paimon 见 realtime-data-plugin 的开源栈记录）、表格式的运维动作（compaction、snapshot 过期、
VACUUM、分区演进）、行级写（MERGE/DELETE）在各格式上的实际行为。
