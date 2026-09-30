# 真实引擎联调报告：Apache Doris 4.1.4.1（官方二进制，原生 FE+BE）

**目的**：把矩阵里 doris 的"本轮未验证"填实。这一轮不再走镜像（Hub 直连超时、镜像源对
`apache/doris` 命名空间 denied），改用**官方二进制包**在 WSL 里原生起 FE+BE，跑通
"建表 → 写入 → 读回 → 证据落账 → `evidence verify` → P3 出口门"。对应台账 `WSL-001`。

| 项 | 值 |
| --- | --- |
| 引擎 | `doris-4.1.4.1-rc02-5dbec55b399`（FE 与 BE 同一版本） |
| 安装方式 | 官方二进制 `apache-doris-4.1.4.1-bin-x64.tar.gz`，**原生 FE+BE**（不是 Docker） |
| 包大小 / 校验 | 4,253,509,898 B（≈3.96 GiB）；sha512 `114a86ac83e7561a145ef21aa2728b8bbb207b87ae5c289ea759f11a9a91282b725132ea6b7b30096cb5892900601eec374c9104b0280b8f0914f8fcd7fa7fd8`（与官方 `.sha512` 逐字节一致） |
| JDK | 自备 JDK 17（Zulu 17.0.13）——FE **硬性要求 17**，宿主原来的 JDK 21 会被直接拒绝 |
| 端口 | FE 9030（MySQL 协议）/ 8030（Web）、BE 8040（Web）/ 9060 / 9050（心跳） |
| 环境 | WSL2 Debian 13、8 核、内存 7.8 GB（FE 堆压到 2 G、BE RSS ≈1.4 G，合计约 2.7 G） |
| 日期 | 2026-09-30 |
| 证据级别 | `local_receipt`（dbapi 族：回执里的 `query_id` 是插件自造的 `run_<hash>`，不是平台 query_id） |

> 与 2026-09-13 那份报告的关系：那份是**更早环境**（Doris 4.0.8）里跑 P1–P6 全流水线的记录，
> 保留在 [TEST-REPORT-doris-live.md](TEST-REPORT-doris-live.md)；本文件是本轮（4.1.4.1）的标准链路现场。

## 0. 一句话结论

**真跑通过**：`doctor → ddl render → 建表 → 写入 → 读回 3 行 / 60.75 → 账本 → evidence verify →
P3 出口门 FAIL=0（WARN=3）`。真引擎同时**揪出一个真缺陷并已修**：Doris 的 DDL 模板把
`ENGINE = OLAP` 写在键子句之后，Doris 直接报语法错（详见第 3 节）；另有三个只在真机上才暴露的
环境约束被记录成可照抄的处置（第 2 节）。

## 1. 包是怎么拿到的（并更正上一轮的结论）

上一轮记的是"官方 OSS 桶 11 个版本名全 404 → 这个网络拿不到 Doris"。真因是**在猜对象名**：
官方下载页 `https://doris.apache.org/download/`（200）直接给出真实地址，扩展名与版本都跟猜的不一样。

| 环节 | 现场 |
| --- | --- |
| 真实地址 | `https://apache-doris-releases.oss-accelerate.aliyuncs.com/apache-doris-4.1.4.1-bin-x64.tar.gz`（北京区同名对象亦可） |
| 可达性 | `HTTP 206`，`Content-Range: bytes 0-1023/4253509898` → 整包 3.96 GiB |
| 单流 | 这条链路上会趴：实测 90 秒只拿到 233 KB（≈3 KB/s 并继续掉），不是包的问题 |
| 分片 | 8 路 Range 并发（可断点续传）→ 稳定 ~5 MB/s，**约 19 分钟**下完 |
| 校验 | 同目录 `.sha512` 比对 → `VERIFY_OK`（size 4,253,509,898 精确匹配） |

## 2. 起 FE/BE：三个只有真机才暴露的约束

| 现象 | 真实现场（原文） | 处置 |
| --- | --- | --- |
| FE 拒绝启动 | `ERROR: The jdk_version is 21, must be 17.` | 装 JDK 17（宿主只有 21；这里用 Zulu 17.0.13 官方 tarball） |
| BE 起来就退出 | `Set kernel parameter 'vm.max_map_count' to a value greater than 2000000` + `Disable swap memory before starting be` | 非 root 改不了内核参数，用官方留的口子 `SKIP_CHECK_ULIMIT=true` 放行——**这是有意识地跳过检查**，生产必须按官方要求把 `vm.max_map_count` 配到 ≥ 2000000 |
| 建表失败 | `replication num should be less than the number of available backends. replication num is 3, available backend num is 1` | 单 BE 本地集群要显式 `PROPERTIES('replication_allocation'='tag.location.default:1')`。这是**环境属性**，不该由模板硬编码 → 现在渲染时会给提示（第 3 节） |

起完之后：`show frontends` / `show backends` 都是 `Alive: true`，FE 为 `IsMaster: true`。

## 3. 建表：模板被真引擎揪出一处真缺陷（已修）

插件原来为 Doris/StarRocks 生成的顺序是"键子句 → ENGINE"，Doris 4.1.4.1 直接语法错：

```
errCode = 2, detailMessage =
mismatched input 'ENGINE' expecting {<EOF>, ';'}(line 7, pos 0)
```

原因是 Doris 的解析器读完键子句就收尾了，`ENGINE` 必须写在键子句**之前**。修复后同一份 spec 渲染出：

```sql
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMALV3(18,2),
  ds DATE
)
ENGINE = OLAP
UNIQUE KEY (order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS AUTO
PROPERTIES ('replication_allocation'='tag.location.default:1');
```

真引擎接受了这份 DDL（建表 4.7 秒）。两处随模板一起给：

- `DECIMALV3(18,2)` 是类型映射表给出的物理类型（Doris 专有），真引擎接受；
- 没给 `replication_num`/`replication_allocation` 时渲染会带一句提示（单 BE 会失败），给了就闭嘴；
  回归用例：`tests/test_ddl.py::test_doris_starrocks_key_clause_is_outside_column_list`（顺序）
  与 `::test_doris_replication_hint_only_when_unspecified`（提示只在缺参数时出现）。

> 诚实边界：本次只真跑了 **Doris**。StarRocks 用的是同一段模板（`ENGINE=OLAP` 在前），
> 但没在真引擎上验证——报告不替它背书。

## 4. 写入 → 读回 → 证据落账

```
$ ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
  status=SUCCESS duration_s=4.719 affected_rows=0
$ ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
  status=SUCCESS duration_s=23.065 affected_rows=3

$ mysql -h127.0.0.1 -P9030 -uroot -D ods -e \
    "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;"
rows_inserted	amt_sum
3	60.75

$ cat .data-dev/runtime/executions.json
  query_id=run_c4ddd63a36dd3d engine=doris evidence_level=local_receipt sql_hash=bbc81a15992d
  query_id=run_24794f49fd4679 engine=doris evidence_level=local_receipt sql_hash=a037edf10fd5

$ ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

## 5. 方言规则的真实现场（`dialect.doris.row_write_needs_key_model`）

这条规则说"Doris 的 UPDATE/DELETE 只支持主键/唯一模型"，本轮在真引擎上把正反两面都跑了：

| 场景 | 真引擎结果 | 静态检查（`sql check --dialect doris`） |
| --- | --- | --- |
| 唯一模型（`UNIQUE KEY(id)`）表上 `UPDATE` | **成功**（`amt` 10.50 → 11.50；读回 3 行 / **61.75**） | 同一条规则给 **WARN**（条件性），与事实一致 |
| 明细模型（`DUPLICATE KEY(id)`）表上 `UPDATE` | **失败**：`errCode = 2, detailMessage = Only unique table could be updated.` | 同上 |
| `UPDATE ... FROM ...` | `Table [ods_orders_raw] does not exist in database [ods]`（Doris 不认这种写法，先在解析阶段就挂） | — |

即：规则从"待验证的 WARN"升级为**有真引擎背书的条件性 WARN**（唯一模型可改、明细/聚合模型不可改）。

## 6. P3 出口门

```
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=doris）……
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

与 ClickHouse / Trino / Hive / Spark / PostgreSQL 的验收口径完全一致（FAIL=0）。

## 7. 这一项**没有**证明什么

- 没有跑 StarRocks（台账 `WSL-002` 仍是 blocked，原因见 [TEST-REPORT-starrocks-unverified.md](TEST-REPORT-starrocks-unverified.md)）；
- 没有验证生产形态：多 BE、副本 3、真实分区、远端存储（S3/HDFS）、权限模型；
- `BUCKETS AUTO` 与分布键只是"最小可用"，性能含义未测；
- 本次**有意识地跳过**了 BE 的 `vm.max_map_count`/swap 前置检查（单机联调），生产必须按官方要求配；
- `local_receipt` 不等于平台 query_id（doctor 的告警原样保留，没有抬高证据级别）。

## 8. 复现命令（可照抄）

```bash
# ① 取包（分片并发；单流会掉到 KB/s 级）
curl -O https://apache-doris-releases.oss-cn-beijing.aliyuncs.com/apache-doris-4.1.4.1-bin-x64.tar.gz.sha512
# 8 路 Range 下完 → cat part.* > apache-doris-4.1.4.1-bin-x64.tar.gz → sha512sum -c

# ② JDK 17（FE 硬要求）
#   Zulu/GPL 的 17 官方 tarball 解到 ~/doris-live/jdk17 即可

# ③ 起 FE（堆从默认 8192m 压到 2048m，否则这台 7.8 G 的机器起不来）
export JAVA_HOME=~/doris-live/jdk17
cd <doris>/fe && ./bin/start_fe.sh --daemon
mysql -h127.0.0.1 -P9030 -uroot -e "CREATE DATABASE IF NOT EXISTS ods;"

# ④ 注册并起 BE（非 root 改不了内核参数 → 走官方口子）
mysql -h127.0.0.1 -P9030 -uroot -e 'ALTER SYSTEM ADD BACKEND "127.0.0.1:9050";'
cd <doris>/be && SKIP_CHECK_ULIMIT=true ./bin/start_be.sh --daemon
mysql -h127.0.0.1 -P9030 -uroot -e 'show backends\G'   # 等 Alive: true

# ⑤ 跑插件链路（dbapi 族要 pymysql）
export PYTHONPATH=<pymysql 所在目录>
python3 engine/ddp.py --root <项目> platform doctor
python3 engine/ddp.py --root <项目> ddl render --spec <项目>/spec.json --out <项目>/..../ddl/ods_orders_live.sql
python3 engine/ddp.py --root <项目> platform run-sql --file <ddl> --env dev --save-evidence --ticket T-LIVE
python3 engine/ddp.py --root <项目> platform run-sql --file <etl> --env dev --save-evidence --ticket T-LIVE
python3 engine/ddp.py --root <项目> evidence verify --ticket T-LIVE
```
