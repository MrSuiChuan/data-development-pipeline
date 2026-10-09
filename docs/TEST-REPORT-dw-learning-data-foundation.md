# data-warehouse-learning 数据底座打通报告（作为数开插件的测试地基）

**环境**

| 项 | 值 |
| --- | --- |
| 语料 | `MrSuiChuan/data-warehouse-learning`（Java + Flink + Doris + SeaTunnel + DolphinScheduler） |
| 业务库 | 本项目自建 `dw-mysql` 容器（`mysql:8.0.39`，3306，`--restart unless-stopped`），库 `gmall` 47 张表 |
| 数仓 | 本地单机 Doris `4.1.4.1-rc02`（WSL Debian，FE+BE 各一，9030） |
| 同步 | Doris 自带 JDBC Catalog 直读 MySQL（本地等效，未装 SeaTunnel） |
| 被测 | `data-development-plugin` v1.3.0+ |
| 日期 | 2026-10-09 ｜ 台账 `goal#dw-learning-data-foundation`（`F-001…F-009`） |

> 所有现场脚本在插件仓库 `.tmp/dw/`（已被 `.gitignore` 排除）。**仓库里的 SQL 一个字节都没有改** ——
> 与 Doris 4.x 不兼容的地方一律用"装载时翻译"处理。

---

## 0. 结论速览

| 项 | 结果 |
| --- | --- |
| 三部分代码通读 | ✅ Doris 建表在 `doris/dml/**`、装载逻辑在 `doris/logical/**`、造数在 `warehouse/datageneration/**`、同步在 `warehouse/seatunnel/**` |
| 业务库 + 造数 | ✅ `gmall` 47 表；仓库自己的 `business_code` 有界跑 300s → `order_info` 1300、`payment_info` 1300、`user_info` 130 |
| 建表 | ✅ **127 张表全部建成**（ods 46 / dim 6 / dwd 30 / dws 25 / ads 20） |
| 同步 | ✅ 44/46 张 ODS 有数据（另 2 张已定性：日志源是 Kafka、成本表源本身为空） |
| 分层加工 | ✅ 脚本 78/81 成功；dim 592 行、dwd 9754 行、dws 11230 行、ads 2194 行 |
| 一键重建 | ✅ `rebuild_all.sh` 单条命令跑通，重跑得到**完全一致**的终态 |
| 插件真跑 | ✅ `platform run-sql` 在真实 `dws` 数据上建派生表并回读：93 行、金额合计 1063278.23 |
| 未覆盖 | 流量/日志链路（需 Kafka）、支付链路（码值不匹配）、3 个脚本仍报错（见第 7 节） |

---

## 1. 代码结构（通读结论）

| 部分 | 位置 | 规模 |
| --- | --- | --- |
| Doris 建表 DDL | `doris/dml/{ods,dwd,dws,ads,dim}` | 46 + 30 + 25 + 20 + 6 |
| Doris 装载逻辑 | `doris/logical/{dim,dwd,dws,ads}` | 6 + 43 + 39 + 20 |
| MySQL 造数 | `warehouse/datageneration/business_code`（写 MySQL）+ `userlog_code`（写 Kafka） | 17 个生成器 |
| 同步 | `warehouse/seatunnel/ods/*.conf` | 92 份 = 46 表 × full/cdc，另有一套 FlinkCDC 在 `learn_flinkcdc/` |

两个反直觉的点，先记下来免得后人踩：**`dml/` 放的是建表 DDL，`logical/` 放的才是装载逻辑**；
`business_jar/` 里那个 jar 是**第三方预编译包**（MANIFEST：atguigu 的 `gmall2020-mock-db`、
Spring Boot 2.0.7、Build-Jdk 1.8），不是本仓库源码构建出来的，本机只有 JDK 17/21，
所以走源码路径（pom 是 Spring Boot 2.7.18，官方支持到 17）。

---

## 2. 基线：1623 → 5

接手时 `git status` 有 1623 条变更。先证明再动手：`git diff --ignore-cr-at-eol --numstat` 输出 0 行，
即 **1619 个文件里真实内容改动为 0**，纯粹是行尾（索引 LF、工作副本 CRLF，而仓库没有 `.gitattributes`）。
补上 `.gitattributes` 后 `git restore`，工作区降到 5 条且全是未跟踪产物。

## 3. 业务库与造数

本机没有 MySQL 服务端（`ps` 里那个 `mysqld` 是 <OTHER-PROJECT> 的容器，host 端口 23306，全程未动），
用 `mysql:8.0.39` 起了本项目自己的 `dw-mysql`，灌 `gmall.sql` → 47 张表（注意它自带种子数据，
例如 `sku_info` 35 行）。造数用仓库自己的源码，`java -cp` 直跑 `BusinessApplication`
并**必须用 `timeout` 兜住**（它是 `while(true)` 无限造数）；数据源用命令行
`--spring.datasource.*` 覆盖指向 127.0.0.1，不改仓库的 `application.yml`。

## 4. 建表与同步

**建表**：原样 DDL 在 Doris 4.x 上 **0 成功 / 46 失败**。逐键清点后发现 11 个 PROPERTIES 键里只有
4 个是 4.x 不认的（`compaction_policy`、`enable_single_replica_compaction`、`max_filter_ratio`、
`data_property.compression_type`）。处置是**装载时翻译**（剔键），仓库 SQL 不动。

**同步**：不装 SeaTunnel（要额外 JDK8 + 一整条链路 + `-Xms4g` 的 JVM 预算），改用 Doris 自带
JDBC Catalog 直读 MySQL —— 注意 4.x 建 catalog **必须显式给 `driver_url`**，否则报
`Invalid driver URL format`。

## 5. 分层加工与那个最关键的坑

第一轮跑完，四层落数惨淡：dwd 只有 10/30 张表有数据、dws 9/25、ads **只有 2 张、共 2 行**。
查下来不是数据天数不够，而是 **`${pdate}` 没被替换**：

```sql
WHERE k1 >= date_add(date('${pdate}'), -29) AND k1 <= date('${pdate}')
```

`${pdate}` 由 DolphinScheduler 注入；脱离调度器直接跑时 `date('${pdate}')` = NULL，条件恒不成立 ——
**脚本不报错，但一行都不落**。扫描发现全仓只有这一个模板变量，却出现 **294 次、涉及 77 份脚本**
（dwd 28、dws 25、ads 20、dim 4），所以远不止 `_nd` 在静默空跑。

处置：装载时把 `${pdate}` 注入成业务日期（当天），仍然不改仓库 SQL；重跑前先 TRUNCATE 上层 81 张表
保证可复现。落数随即全面上升：

| 层 | 有数据的表（前→后） | 行数（前→后） |
| --- | --- | --- |
| dwd | 10 → **19** / 30 | 6362 → **9754** |
| dws | 9 → **18** / 25 | 5595 → **11230** |
| ads | 2 → **16** / 20 | 2 → **2194** |

`ads_trade_stats` 现在有 3 行（1/7/30 日三档）—— 正是"每天的销售额"要的那张表。

## 6. 事故与本地轻量档

按仓库原始 DDL 建上层表时，灌到第 80 张表**整机 OOM**，内核直接杀掉 `doris_be`
（`dmesg`：`Out of memory: Killed process (doris_be) … anon-rss:2591336kB`），连带 Docker 重启，
`dw-mysql` 因为没有重启策略停在 Exited 255。量化原因很清楚：原 DDL 是**集群档** ——
每张表 64 个动态分区 × 32 桶 ≈ 2048 个 tablet，46 张 ODS 就接近 9.4 万，再叠 81 张必爆。

改用**本地轻量档**后稳定：只改物理参数、不动 schema ——
`dynamic_partition.buckets 32→4`、`start -60→-7`、`end 3→1`、`DISTRIBUTED BY ... BUCKETS 32→4`，
`k1` 的夹取窗口同步改成 7 天。

## 7. 一键重建与可重复性

`bash .tmp/dw/rebuild_all.sh`（加 `--reset` 则连 Doris 元数据一起从零来）：

```
1/6 MySQL 容器 → 2/6 Doris FE+BE → 3/6 建 127 张表（轻量档）
→ 4/6 建 JDBC catalog → 5/6 灌 ODS → 6/6 按层重算（注入业务日期）
```

实测单条命令跑通，重跑后逐层行数与上一次**完全一致**（dim 592 / dwd 9754 / dws 11230 / ads 2194），
脚本成功率同为 78/81。**可反复重建成立。**

> 一个测量陷阱：`information_schema.tables.table_rows` 是缓存估算值，刚灌完就查会显示 0。
> 核对必须用 `count(*)`（重建脚本第 6 步就踩了这个，后来用真实计数复核对上）。

## 8. 插件在真实数据上做任务开发

把 `data-development-plugin` 的工程指向这套数仓（`platform.kind=doris`），
以真实的 `dws.dws_trade_user_order_1d`（93 行）为上游开发一张派生表：

```
platform doctor            → canonical_kind=doris / dialect=doris / family=dbapi
platform run-sql           → {"status":"SUCCESS","query_id":"run_01b1d89f8f50c3","duration_s":0.804,"affected_rows":93}
回读（直接查 Doris）        → 93 行，amount 合计 1063278.23，最大单笔 28883.47
```

重建后再跑一次，结果**逐字相同**。这就是这个目标的终点：插件确实能在有真实数据、可重复重建的表上
做任务开发与真跑验证。

## 9. 语料自身的缺陷清单（本轮实证）

1. **SeaTunnel conf 与 ODS DDL 字段对不上**：`ods_order_info_full` 目标列是
   `final_amount/original_amount/user_name`，conf 选的却是 `total_amount/original_total_amount`，
   照 conf 灌直接报列数不符。
2. **`${pdate}` 未替换导致 77 份脚本静默空跑**（第 5 节，最严重）。
3. **Doris 版本漂移**：4 个 PROPERTIES 键在 4.x 不存在；README 写的是 Doris 2.0.4。
4. **动态分区窗口撞种子数据**：ODS 用 `start=-60` 相对建表时刻，而 `gmall.sql` 自带 2020 年的种子行，
   落在窗口外报 `no partition for this tuple`；动态分区表又禁止手工 `ADD PARTITION`。
5. **码值与标签不匹配**：`dwd_trade_pay_detail_suc_inc` 过滤 `payment_status='1602'`，
   而 mock 数据里实际是字符串 `'PAID'`（1300 行全是 PAID），支付链路因此全空。
6. **方言误用**：同一脚本用 `date_format(callback_time,'yyyy-MM-dd')`（Spark/Hive 格式串），
   Doris 要 `'%Y-%m-%d'`。
7. **DDL 与逻辑不一致/命名瑕疵**：`dwd_inventory_order_task_inc`、`dwd_inventory_stock_full`
   引用了 DDL 里不存在的 `k1`；`dws_trade_tm_category1_cat_nd` 跨表列名对不上（`sku_id`）；
   `ods_sku_sale_attr_value_full..sql` 是双点文件名；3 份 `*_inc.sql` 建出来的表名其实是 `*_full`。
8. **配置无人读**：造数器的 `application.yml` 里 `generation.*` 整段在源码里没有任何读取方。

## 10. 未覆盖与风险

1. **流量/日志链路 11 张表空着**：`dwd_traffic_*` 5 张、`dwd_user_login_inc`、
   `dws_traffic_page_visitor_page_view_nd`、`ads_page_path`、`ads_traffic_stats_by_channel`、
   `ads_user_retention`、`ads_new_buyer_stats` —— 源是 `ods_log_full`，而它的上游是 **Kafka 用户日志**，
   本地没有 Kafka（实测 `ods_log_full` 0 行）。
2. **`dim_date` 空**（日期维度需单独生成）、**`dwd_trade_cancel_detail_inc` 空**（mock 不产生取消单）。
3. **3 个脚本仍报错**（第 9 节第 7 条），属语料缺陷，未在本地绕过。
4. **本地环境不稳**：本轮出现过一次整机 OOM（已改用轻量档规避）与数次 WSL 发行版重启
   （`/tmp` 会被清空，脚本已改为不依赖 `/tmp` 状态）。**不要把本地 Doris 当持久环境**；
   重建用 `rebuild_all.sh`，误重置可从 `fe/doris-meta.bak-<时间戳>` 回退（本轮实测可回退成功）。
5. **未装 SeaTunnel / DolphinScheduler / Kafka**：同步用 Doris JDBC Catalog 等效替代，
   调度语义（含 `${pdate}`）由本地脚本注入，不是真调度。

## 11. 复跑命令

```bash
# 一条命令重建整套底座（首次或环境重启后都可用）
bash .tmp/dw/rebuild_all.sh

# 逐层核对行数
bash .tmp/dw/verify_layers.sh

# 证明插件能在真实数据上开发任务
bash .tmp/dw/plugin_on_real_data.sh

# 连接串
mysql -h127.0.0.1 -P9030 -uroot            # Doris（无密码）
mysql -h127.0.0.1 -P3306 -uroot            # MySQL（无密码，库 gmall）
```
