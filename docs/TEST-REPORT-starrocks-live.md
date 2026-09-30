# 真实引擎联调报告：StarRocks 3.3.9（FE + BE 官方镜像）

**目的**：把矩阵里 starrocks 的"本轮未验证"填实（台账 `WSL-002`），验收口径与 Doris/
ClickHouse/Trino/Hive/Spark/PostgreSQL 完全一致：建表 → 写入 → 读回 → 证据落账 →
`evidence verify` → P3 出口门。

| 项 | 值 |
| --- | --- |
| 引擎 | `3.3.9-dfae8f9`（`show backends` 的 Version 列；FE `select version()` 回 MySQL 兼容串 `5.1.0`） |
| 安装方式 | 官方镜像 `starrocks/fe-ubuntu:3.3.9` + `starrocks/be-ubuntu:3.3.9`（FE/BE 分开，经 `docker.1ms.run` 镜像源拉到本地：FE 1.97 GB、BE 3.03 GB） |
| 端口 | FE 9030→宿主 **19030**（9030/8030 被另一个任务的 Doris FE 占着，所以换端口）、BE 8040→18040 |
| 日期 | 2026-09-30 |
| 证据级别 | `local_receipt` |

## 0. 一句话结论

**真跑通过**：`doctor → ddl render → 建表 → 写入 → 读回 3 行 / 60.75 → 账本 → evidence verify →
P3 出口门 FAIL=0（WARN=3）`。真引擎同时揪出**两个模板缺陷**（`BUCKETS AUTO` 是 Doris 专有写法；
StarRocks 的 UPDATE 要主键模型而模板给的是 UNIQUE KEY），都已按实测结果修正/写清。

## 1. 起容器：镜像默认 Cmd 是 `/bin/bash`

两个镜像的 `Cmd` 都是 `/bin/bash`、`Entrypoint=null`——`docker run -d` 下去立刻退出（Exited 0/1，
日志为空）。正确起法是让容器保活、再进去起进程：

```bash
docker run -d --name ddp-sr-fe --network ddp-sr-net --network-alias starrocks-fe \
  -p 19030:9030 -p 18030:8030 starrocks/fe-ubuntu:3.3.9 tail -f /dev/null
docker exec ddp-sr-fe bash -lc "sed -i 's/-Xmx8192m/-Xmx2048m/' /opt/starrocks/fe/conf/fe.conf; \
  cd /opt/starrocks/fe && nohup ./bin/start_fe.sh > /tmp/fe.out 2>&1 &"
```

两个附带约束：

- **FE 默认堆 8192m**（`fe.conf` 的 `JAVA_OPTS`），这台 7.8 GB 的机器必须压到 2048m；
- BE 同理，用 `docker exec` 进去 `nohup ./bin/start_be.sh`，健康检查 `curl 127.0.0.1:8040/api/health`
  返回 `{"status":"OK","msg":"To Be Added"}`，再 `ALTER SYSTEM ADD BACKEND 'starrocks-be:9050'` 后才 `Alive: true`。

## 2. 缺陷一：`BUCKETS AUTO` 是 Doris 的写法，StarRocks 会语法错

插件原来给 Doris/StarRocks 共用一句 `DISTRIBUTED BY HASH(x) BUCKETS AUTO`。StarRocks 3.3.9 现场：

```
ERROR 1064 (HY000): Getting syntax error at line 8, column 38.
Detail message: Unexpected input 'AUTO', the most similar input is {INTEGER_VALUE}.
```

三种写法在同一实例上的实测：

| 写法 | 结果 |
| --- | --- |
| `DISTRIBUTED BY HASH(id) BUCKETS AUTO` | ❌ 语法错（上） |
| `DISTRIBUTED BY HASH(id) BUCKETS 1` | ✅ 可用 |
| `DISTRIBUTED BY HASH(id)`（省略） | ✅ 可用，`show create table` 也回显省略形态（由引擎自动分桶） |

修正：**Doris 保留 `BUCKETS AUTO`，StarRocks 不写 BUCKETS**（把"分桶数"交回引擎/人工，而不是硬编一个 1）。
回归用例：`tests/test_ddl.py::test_buckets_clause_splits_doris_from_starrocks`。

## 3. 建表 → 写入 → 读回

```sql
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE
)
ENGINE = OLAP
UNIQUE KEY (order_id)
DISTRIBUTED BY HASH(order_id)
PROPERTIES ('replication_num'='1');
```

```
建表 affected_rows=0 → 写入 affected_rows=3 → 读回 rows_inserted=3 / amt_sum=60.75
账本：query_id=run_063332d7f11dbf（建表）、run_5d20a2550160d8（写入），engine=starrocks，level=local_receipt
evidence verify：{"ok": true, "source": "etl_dev_exec.json"}
```

两处细节都有真引擎依据：

- `DECIMAL(18,2)` 是 starrocks 方言的类型映射（**不是** Doris 的 `DECIMALV3`）——同一轮里把
  `DECIMALV3` 打进 StarRocks 会报 `Unexpected input 'DECIMALV3'，相似的有 DECIMAL/DECIMALV2/DECIMAL32/…`，
  正好反证两家类型不能共用；
- `PROPERTIES ('replication_num'='1')`：单 BE 集群默认副本数 3 建表会失败（与 Doris 同类约束）。

## 4. 缺陷二（写清而不是猜）：StarRocks 的 UPDATE 要主键模型

| 表模型 | `UPDATE` | `DELETE` |
| --- | --- | --- |
| `UNIQUE KEY(order_id)`（模板默认生成） | ❌ `ERROR 1064: table ods_orders_live does not support update` | ✅ 成功（读回 2 行 / 40.50） |
| `PRIMARY KEY(order_id)`（手工建） | ✅ 成功 | ✅ 成功（读回 1 行 / 11.50） |

所以 `dialect.starrocks.row_write_needs_key_model` 这条 WARN 的文案按实测改精确了：
**UPDATE 要主键模型；UNIQUE KEY 表不支持 UPDATE；DELETE 在唯一/主键模型上可用；明细/聚合模型都不行**。
静态检查（`sql check --dialect starrocks`）与真引擎结论一致，没有把"提示"升级成"包治"。

## 5. P3 出口门

```
[WARN] sql.compliance.no_table_annotation: 建议加 -- table: 注解
[WARN] evidence.level.local_receipt: 执行回执是本地回执（engine=starrocks）……
[WARN] dev.code_diff.new_task: 无线上基准（新表场景），跳过 code_diff 强校验
FAIL=0  WARN=3
```

## 6. 走错的一步也留证：`kind=command + engine=mysql` 不是 StarRocks

第一次跑用的是 `kind=command` + `engine=mysql`（沿用了上一轮的续跑配方），`platform doctor` 明确报
`canonical_kind=command, dialect=mysql`，于是 DDL 按 MySQL 渲染出列内 `PRIMARY KEY` + `ENGINE = InnoDB`，
StarRocks 直接拒绝：

```
ERROR 1064: No viable statement for input ', PRIMARY'
```

结论：**要跑 StarRocks 方言就用 `kind=starrocks`**（dbapi 族，pymysql 直连 9030）；`kind=command` 只是
"把 SQL 交给某个客户端"的通用通道，它不会替你换成 starrocks 方言。这一条已写进本报告，避免下一个人复现同样的弯路。

## 7. 这一项**没有**证明什么

- 没有验证 StarRocks 的生产形态：多 BE、副本 3、分区、物化视图、Remote Storage；
- 没有验证其它 StarRocks 版本（本报告只对 3.3.9 有真引擎背书；3.3.9 起 `BUCKETS AUTO` 的缺失是版本事实）；
- 单机联调用的是 `replication_num=1`，与生产默认（3）不同；
- FE 堆被压到 2048m、且宿主还跑着别的任务的 Doris FE——资源受限下的结论只覆盖功能，不覆盖性能。

## 8. 复现命令（要点）

```bash
# ① 拉两个镜像（FE 1.97 GB / BE 3.03 GB；本网络经 docker.1ms.run 可达）
docker pull docker.1ms.run/starrocks/fe-ubuntu:3.3.9
docker pull docker.1ms.run/starrocks/be-ubuntu:3.3.9

# ② 起 FE/BE（容器保活 + exec 起进程；FE 堆压到 2048m）
#    见第 1 节

# ③ 注册 BE
mysql -h127.0.0.1 -P19030 -uroot -e "ALTER SYSTEM ADD BACKEND 'starrocks-be:9050'"
mysql -h127.0.0.1 -P19030 -uroot -e "show backends\G"   # 等 Alive: true

# ④ 插件链路（kind=starrocks，dbapi；单 BE 记得 replication_num=1）
python3 engine/ddp.py --root <项目> platform doctor
python3 engine/ddp.py --root <项目> ddl render --spec <项目>/spec.json --out <...>/ddl/ods_orders_live.sql
python3 engine/ddp.py --root <项目> platform run-sql --file <ddl> --env dev --save-evidence --ticket T-LIVE
python3 engine/ddp.py --root <项目> platform run-sql --file <etl> --env dev --save-evidence --ticket T-LIVE
python3 engine/ddp.py --root <项目> evidence verify --ticket T-LIVE
```
