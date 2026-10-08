# 整仓知识入库 + 本地 Doris 真跑报告：data-warehouse-learning × OpenViking × Doris

**环境**

| 项 | 值 |
| --- | --- |
| OpenViking | `http://<OPENVIKING_HOST>:1933`（服务端 0.4.x，`/openapi.json` 可读，105 个路径） |
| API Key | `~\.openviking\api.key`（110 B；**只以文件路径出现在配置里，内容不进任何产物**） |
| 语料 | `MrSuiChuan/data-warehouse-learning`（872 份 SQL，739 份含 `CREATE TABLE`）→ 195 张逻辑表、2373 列 |
| 知识库运行时 | 记忆插件 `knowledge-base-plugin` 1.3.0（`.tmp/lr/kb`），域 `dw-learning` |
| 平台 | 本地单机 Apache Doris `4.1.4.1-rc02`（WSL Debian，FE + BE 各一） |
| 被测 | 离线数开插件 `data-development-plugin` v1.3.0+ |
| 日期 | 2026-10-08 ｜ 台账 `goal#dw-learning-ingest-doris`（`DW-001…DW-004`） |

> 本轮的产物是**知识**和**运行环境**，不是代码改动：仓库里没有源码变更，全部现场脚本在
> `.tmp/dw/`（已被 `.gitignore` 排除，与以往几轮的做法一致）。

---

## 0. 结论速览

| 项 | 结果 |
| --- | --- |
| 整仓 195 张表进 OpenViking | ✅ 202 个对象，**201 个逐字节比对一致**，`tables/` 目录 `fs/ls` 数出来正好 195 |
| 语义检索可用 | ✅ 业务词（商品/支付/退款/秒杀/用户拉新）都命中对应真实表文档 |
| Doris 本地起来 | ✅ FE master `Alive: true`，BE `Alive: true`，端口 9030，库 `ddp_dev` |
| 数开插件真连 Doris | ✅ `platform run-sql` 返回 `status: SUCCESS`，读回 `live_probe = (1,1.50) (2,2.50)` |
| 数开插件真读知识库 | ✅ `kb read` 读到 `dwd_trade_order_detail_inc` 的 9 章节正文（含字段语义与建表 SQL） |
| 自测工程就绪 | ✅ `~\Documents\ChatGPT\dw-learning-live` 已配好，可直接 `ddp-submit → ddp-start` |
| 未覆盖 | 195 张表**没有**灌进 Doris（只进了知识库）；Doris 里目前只有冒烟表（见第 5 节） |

---

## 1. 把整个仓库记录到 OpenViking

### 1.1 做了什么

1. 解析语料 872 份 SQL → 195 张逻辑表的快照（`ods 47 / dwd 50 / dws 50 / ads 40 / dim 8`），
   其中 127 张有 hudi/paimon/iceberg 湖表变体，记录在 `BASIC_INFO.variants`；
2. 记忆插件走"骨架 5 步 → 生成 → 审计"：`generate --domain dw-learning` 产出 **195 篇表文档
   + navigation + index**，缺项如实标 `placeholder_count=195`（不编内容）；
3. `audit --mode publish --level strict` → `passed: true`；
4. `publish --domain dw-learning` 双轨发布（git + OpenViking）。

### 1.2 结果（对着远端数出来的，不是采信返回值）

```
manifest 末次发布：version=kb-v8  domain=dw-learning  uris=202  ov_sync=remote
                    at=2026-10-08T14:21:40Z  reason=整仓知识录入（快通道：wait=false）

逐字节核对（download_raw 与本地文件比对，201 个对象并发核）：
  一致 = 201 / 201    不一致 = 0

远端目录：
  domains/dw-learning/tables/      fs/ls → 195  （正好 195 张表）
  domains/dw-learning/references/  repo README + 两个模块 README
  domains/dw-learning/index.md  navigation.md  skeleton.json
```

语义检索抽样（`POST /api/v1/search/find`，target 限定到该域）：

| 查询 | 命中 |
| --- | --- |
| 商品信息 | `dwd_product_sku_info_full`（0.403）、`dwd_product_spu_info_full`（0.393） |
| 支付 | `ods_payment_info_full`（0.353）、`dws_trade_user_payment_td`（0.353） |
| 退款 | `ods_refund_payment_full`（0.409）、`dwd_trade_order_refund_inc`（0.392） |
| 秒杀 | `dwd_marketing_seckill_full`（0.379） |
| 用户拉新 | `ads_user_retention`（0.409）、`ads_user_change`（0.402） |

### 1.3 卡住过一次，根因是 `wait=true`

第一轮发布跑了 **51 分钟只推上去 64 个对象**，日志里没有任何进度，看起来像挂了。实测端点延迟：

| 调用 | 耗时 |
| --- | --- |
| `POST /api/v1/content/write`，`wait: true` | **19.1 s** |
| `POST /api/v1/content/write`，`wait: false` | **0.14 s** |
| `wait:false` 写完后立刻 `content/read` | ✅ 内容已在，能读到 |
| 8 个并发只读请求 | 0.56 s（服务端不怕并发） |

`wait=true` 会让服务端**同步**做完语义/向量处理才返回；202 个对象按 19 s 算就是 1 小时以上的下限，
实际大文档更慢。记忆插件的 `ov.wait` 缺省是 `true`，所以**批量入库默认会慢到像卡死**。

处置：把 `.kb/config.json` 里 `ov.wait` 设为 `false` 后重跑，**2 分 23 秒**推完 202 个对象，
并按第 1.2 节做了逐字节回读校验——内容完整性没有因为 `wait=false` 打折，只是向量索引改成后台补。

---

## 2. 本地 Doris 起来（三个叠在一起的原因）

### 2.1 症状

FE 进程活着、`9010` 在监听，但 `9030` 永远连不上；`fe.log` 每 10 秒打一行：

```
Env.waitForReady():1239  wait catalog to be ready. feType:UNKNOWN isReady:false, counter:1 → 8601 …
```

### 2.2 原因一：`priority_networks` 没配，FE 每次绑到不同的 docker 网桥

从 BDB JE 日志（`fe/doris-meta/bdb/je.info.0`）能直接看到，三次启动绑了三个地址：

| 启动时间 | BDB 服务地址 | 结果 |
| --- | --- | --- |
| 2026-09-30 | `<LAN-IP>:9010` | 首个节点，直接当选 MASTER（把节点登记在这个地址上） |
| 2026-10-07 | `<LAN-IP>:9010` | `Existing node … querying for a current master` → 选举发起后不结束 |
| 2026-10-08 | `<LAN-IP>:9010` | 同上，卡 `UNKNOWN` |

WSL 里 `lo / eth0 / br-*` 一堆网卡，未设 `priority_networks` 时 Doris 取第一个非 loopback，
拿到的往往是某个 docker 网桥（`172.22/23/24.0.1`）。这个地址每次都变，而 BDB 组里登记的
还是首次启动那个，地址对不上 → 选举永远完不成。

修复：把 `priority_networks` 钉到 `eth0`（`fe.conf` 与 `be.conf` 都写），并把旧元数据挪走重建。

### 2.3 原因二：内存不够，BE 直接拒绝干活

这台 WSL 一共 7.8 GB，当时常驻：<OTHER-PROJECT> 任务的容器（OpenSearch/MySQL/Redis/Postgres，
**未改动**）、ActiveMQ、以及一个 **没人管的 Flink session**（JM + 2 TM ≈ 2 GB）。
FE 起来后 BE 的 `INSERT` 报：

```
[MEM_ALLOC_FAILED] Cannot alloc:4.00 KB … limit 6.89 GB, soft limit 6.20 GB,
sys available memory 165.48 MB
```

那个 Flink 的 pid 文件早就是死的（`stop-cluster.sh` 回 "No taskexecutor daemon … is running anymore"），
实际进程父级是已消失的 Relay 包装（孤儿），且 REST 显示 **0 个在跑作业、2 个空 slot**。
按你之前说的"不要同时启动，每次只启动必要的组件"，收掉它：

```
可用内存：167 MB → 1860 MB
```

之后 Doris 的写入一次通过。

### 2.4 原因三（我自己踩的）：`SELECT 1` 不能当就绪探针

Doris 4.x 里连 `SELECT 1` 都要 BE 才能执行，BE 没起来时会报
`No backend available as scan node`，于是把**已经就绪**的 FE 误判成没起来。
探针换成只查元数据的 `SHOW FRONTENDS`。这条写进了 `.tmp/dw/fix_and_start_doris.sh` 的注释。

### 2.5 起来之后

```
FE  Host=<LAN-IP>  IsMaster=true   Alive=true   doris-4.1.4.1-rc02-5dbec55b399
BE  Host=127.0.0.1     Alive=true      BackendId=1791468152642
库  ddp_dev；表 smoke_ddp、live_probe
mysql -h127.0.0.1 -P9030 -uroot -D ddp_dev -e 'select * from live_probe;'
   1  1.50
   2  2.50
```

启动/停止脚本：`.tmp/dw/fix_and_start_doris.sh`（`--reset` 重建元数据、`--free-flink` 先回收内存）、
`.tmp/dw/start_be_and_smoke.sh`（FE 已在时单跑）。

---

## 3. 给你自测用的工程（已配好）

路径 `~\Documents\ChatGPT\dw-learning-live`，`.data-dev/config.json` 关键段：

```json
{"platform": {"kind": "doris", "host": "127.0.0.1", "port": 9030, "user": "root",
              "password": "", "database": "ddp_dev", "base_env": "dev"},
 "knowledge": {"kind": "http", "uri_prefix": "viking://resources/domains/dw-learning/",
               "limit": 10, "level": 2,
               "ov": {"url": "http://<OPENVIKING_HOST>:1933",
                      "api_key_file": "~/.openviking/api.key"}}}
```

跑之前先让 Doris 的驱动可见（本机 pymysql 已经放在 `~/doris-live/pylibs`，系统 python 没装 pip）：

```bash
export PYTHONPATH=~/doris-live/pylibs
cd ~/Documents/ChatGPT/data-development-plugin

# 体检 + 真连一次
python3 engine/ddp.py --root ~/Documents/ChatGPT/dw-learning-live platform doctor

# 检索 dw-learning 域
python3 engine/ddp.py --root …/dw-learning-live kb search --query "订单明细"
python3 engine/ddp.py --root …/dw-learning-live kb read \
  --uri viking://resources/domains/dw-learning/tables/dwd_trade_order_detail_inc.md

# 走一遍任务开发
python3 engine/ddp.py --root …/dw-learning-live ticket create --title '…' --description '…'
python3 engine/ddp.py --root …/dw-learning-live start
```

本轮实测证据：

| 动作 | 结果 |
| --- | --- |
| `platform doctor` | `ok:true`，`canonical_kind=doris`，`dialect=doris`，警告：DBAPI 族证据级别是 `local_receipt`（拿不到服务端 query_id） |
| `platform run-sql`（建表+写入+聚合） | `{"status":"SUCCESS","query_id":"run_b359d39aa74539","duration_s":0.702}` |
| `kb search --query 订单明细` | 命中 `dwd_inventory_order_task_detail_inc`(0.409)、`dwd_inventory_order_task_inc`(0.408) |
| `kb read --uri …/dwd_trade_order_detail_inc.md` | 返回完整 9 章节（字段语义表含 `split_total_amount DECIMAL(16,2) 最终价格分摊`） |
| `sql check --dialect doris` | 正常返回规则结果（本样例报 `WARN rule 15：建议加 -- table 注解`） |

---

## 4. 发现的缺陷与摩擦点

都在**记忆插件**（`knowledge-base-plugin`）那侧，本轮没改它的代码，按此前几轮的做法如实记录：

1. **`ov.wait` 缺省 `true` 让批量入库事实上不可用**（最严重）。19.1 s/对象 vs 0.14 s/对象，
   202 个对象就是"看起来卡死"。建议：批量（比如 >20 个对象）自动降 `wait=false` 并在完成后
   报告"远端内容已写入，向量索引后台补齐"；至少要在开始前打印对象数 × 预估耗时。
2. **`publish` 没有进度输出**。51 分钟里 stdout 只有两行，让人无法判断是慢还是死。
3. **`publish` 成功时不回报远端结果**（上一轮 OV-003 已记录）：`remote_enabled=false`（只提 git）
   与 `true`（真上云）返回值一样，只有失败才给 `gate=ov_remote_sync`。
4. **远端客户端没有删除能力**。`ov_remote.py` 只有 stat/mkdir/write/read/download；真实删除接口是
   `DELETE /api/v1/fs?uri=…`（**uri 放 query，不能放 body**；删目录要 `recursive=true`）。
   结果：一次误入库没法用插件自己回滚——本轮我用 curl 手工清掉了探针文件。
5. **`fs/stat` 的 `count` 字段会误导**。195 个文件的 `tables/` 目录，`count` 只报 60。
   做完整性判断要用 `fs/ls` 或逐字节回读，别用 `count`。
6. **上游向量模型会 429**：连续检索时报
   `RESOURCE_EXHAUSTED … Upstream model quota or rate limit exceeded (HTTP 429)`。
   语义检索偶发失败要能重试，别当成"知识库里没有"。
7. **`content/write` 用 `mode=replace` 打不存在的文件返回 404**，新文件必须 `mode=create`
   （插件内部已处理，直接调 API 时要注意）。

---

## 5. 未覆盖与风险（别当成已完成）

1. **195 张表没有灌进 Doris**：它们进的是**知识库**，Doris 里只有冒烟表 `smoke_ddp`、`live_probe`。
   你要测"任务开发"，得先让任务自己建表，或者另外把这批 DDL 转成 Doris 语法灌进去
   （语料是 Hive 口径，含 `STRING`、`hudi_/paimon_` 变体，不是逐字可跑的 Doris DDL）。
2. **本地 Doris 是易失的**：WSL 重启后 `eth0` 地址一变，FE 又会卡在同一个选举问题上。
   重新跑 `fix_and_start_doris.sh --reset` 能恢复，但 `--reset` 会把旧元数据挪到
   `doris-meta.bak-<时间戳>`（可回滚文件，不等于数据还在）。请把本地 Doris 当一次性环境用。
3. **内存余量仍然小**：收掉 Flink 后可用约 1.8 GB，够跑轻量任务；同时再起 Flink / Spark 会再次
   触发 `MEM_ALLOC_FAILED`。要并行测多引擎得先加内存或错峰起停。
4. **收掉的 Flink session 没有重启**：它是孤儿进程（pid 文件无效、0 个在跑作业），我用 `TERM` 停的。
   需要时用 `~/oss/flink-2.2.0/bin/start-cluster.sh` 重新拉起即可。
5. **<OTHER-PROJECT> 的四个容器全程没动**（OpenSearch / MySQL / Redis / Postgres）。
6. **表的业务语境没补**：195 篇文档的"业务语境/上游口径/FAQ"是占位符，这是生成器的既定行为
   （`placeholder_count=195`），要人工或另跑一轮补。

---

## 6. 复跑命令

```bash
# 语料
git clone https://github.com/MrSuiChuan/data-warehouse-learning

# 整仓知识入库（快通道）
bash .tmp/dw/ingest_dw_learning.sh
bash .tmp/dw/fast_republish.sh        # 内含 ov.wait=false + 重发布
bash .tmp/dw/verify_all.py            # 逐字节核对 201 个对象

# 远端现状
bash .tmp/dw/publish_status.sh
bash .tmp/dw/verify_ingest.sh

# Doris
bash .tmp/dw/fix_and_start_doris.sh --reset --free-flink
bash .tmp/dw/start_be_and_smoke.sh

# 自测工程
bash .tmp/dw/prepare_live_project.sh
bash .tmp/dw/verify_live_project.sh
```

密钥只放在 `~\.openviking\api.key`，配置里写 `api_key_file` 路径；
本报告与本轮所有产物都不含密钥内容。
