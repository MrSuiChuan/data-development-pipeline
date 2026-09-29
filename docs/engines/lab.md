# 本地真跑实验手册（一次只起一个组件）

这份手册记录"怎么在本机真跑一个引擎"，以及踩过的坑。目标不是把组件装全，而是
**一次只起必要的那个**，跑完就停——本机 WSL 只有 7.8 GB 内存，同时开两个重型组件
会互相拖死，而且失败现场分不清是谁的问题。

## 纪律（都是踩出来的）

1. **一个组件一次**：起下一个之前先 `docker stop` 上一个。脚本开头就停同类容器。
2. **项目目录先建、再挂载**：Docker 会以 root 建挂载点，反过来后面写 SQL 文件就是
   `PermissionError: /tmp/xxx/.data-dev`。
3. **项目目录放在 Windows 文件系统**（`<repo>/.tmp/live-<engine>/proj`）：
   WSL 的 `/tmp` 会随发行版重启清空，把项目放 `/tmp` 会让"刚跑通的证据"蒸发。
4. **口令走环境变量**：`MYSQL_PWD` / `PGPASSWORD` / `CLICKHOUSE_PASSWORD`（容器内用
   `docker exec -e`）。argv 里的 `-p<口令>` 会出现在进程列表里。
5. **从干净状态开始**：每轮先 `DROP TABLE`（否则读回的行数会累计，把"写对了"误读成"数据多了"）。
6. **WSL 会话要活着**：容器与 `/tmp` 都随发行版存亡；长时间作业用一个挂着 sleep 的会话持有，
   或者把整套流程塞进**同一条** bash 命令里跑完。

## 每组件的现场

| 组件 | 镜像 / 版本 | 端口 | 内存预算 | 客户端位置 | 状态 |
| --- | --- | --- | --- | --- | --- |
| ClickHouse | `clickhouse/clickhouse-server:24.8`（实测 24.8.14.39） | 9000 / 8123 | 小（~0.5 GB） | 容器内 `clickhouse-client`（宿主未装） | 真跑通过 2026-09-29 |
| PostgreSQL | `postgres:16` | 55432→5432 | 小 | 宿主 `psql` | 真跑通过 2026-09-28 |
| MySQL | `mysql:8.4.4` | 53306→3306 | 中（~1 GB，首次初始化 60s） | 宿主 `mysql` | 真跑通过 2026-09-28（command 族） |

其余组件（Trino / Hive / Spark / 湖表格式 / Doris / StarRocks）随 LIVE-002…LIVE-007 逐个补进这张表。

## 一次真跑的固定步骤

```bash
# 0) 资源纪律：停掉其他组件
docker stop <上一个组件> 2>/dev/null || true

# 1) 起目标组件（单实例），等就绪
docker run -d --name ddp-<engine> ... <image>

# 2) 建项目目录（先 mkdir、后挂载），初始化 DDP 项目
mkdir -p <proj>/.data-dev/tickets/T-LIVE/{ddl,etl,dp_task}
python3 engine/ddp.py --root <proj> setup --mode live

# 3) 配置 + 体检
#    platform.kind=<engine>（command 族记得写 command.engine）
python3 engine/ddp.py --root <proj> platform doctor

# 4) 渲染建表模板 → 真跑建表 → 真跑写入 → 读回 → 账本 → verify
python3 engine/ddp.py --root <proj> ddl render --spec spec.json --out <proj>/.../ddl/<table>.sql
python3 engine/ddp.py --root <proj> platform run-sql --file ... --env dev --save-evidence --ticket T-LIVE

# 5) 真实 P3 出口门（补 dp_task/架构机读块/证据键后用通用脚本）
bash .tmp/live-common/p3_gate.sh <proj> <table>

# 6) 释放资源
docker stop ddp-<engine>
```

通用脚本：`.tmp/live-common/p3_gate.sh`（补 P3 输入 + 跑真实出口门）。
每组件的完整脚本在 `.tmp/live-<engine>/run.sh`，日志 `out.log`（都在 `.tmp`，不入库）。

## 标准化跑法：`tools/live_probe.py`

引擎无关的那段（doctor → ddl render → 建表 → 写入 → 读回 → evidence verify）已经收进仓库：

```bash
py -3 tools/live_probe.py --project .tmp/live-ch/proj --table ods_orders_live --spec .tmp/live-ch/proj/spec.json
py -3 tools/live_probe.py --project <项目> --table <表> --spec <spec.json> --dry-run      # 只打印命令
py -3 tools/live_probe.py --project <项目> --table <表> --spec <spec.json> --stop-container ddp-ch
```

引擎特有的东西（argv、镜像、端口、湖表 catalog）留在项目配置里——下表就是各组件的"配方"。

## 各组件配方（本轮实测可用）

| 组件 | 怎么起 | argv 要点 |
| --- | --- | --- |
| ClickHouse 24.8 | `docker run -d --name ddp-ch -p 9000:9000 -v <proj>:<proj> -e CLICKHOUSE_PASSWORD=ddp clickhouse/clickhouse-server:24.8` | `docker exec -i -e CLICKHOUSE_PASSWORD=ddp ddp-ch clickhouse-client --multiquery --queries-file {file}` |
| Trino 476 | `docker run -d --name ddp-trino -p 8080:8080 -v <proj>/trino/catalog:/etc/trino/catalog trinodb/trino:476`（catalog 里放 `memory.properties`） | 宿主跑官方 CLI：`java -jar trino-cli-476-executable.jar --server http://127.0.0.1:8080 --catalog memory --file {file}`；**就绪判断要跑真查询** |
| Hive 4.0 | `docker run -d --name ddp-hive -p 10000:10000 -v <proj>:<proj> apache/hive:4.0.0` | `docker exec -i ddp-hive beeline -u jdbc:hive2://localhost:10000/default -f {file}`（**别用 bash -lc 包**） |
| Spark 4.1.3 | 宿主解压 `spark-4.1.3-bin-hadoop3`（注意目录后缀） | `spark-sql --master local[2] --conf spark.sql.warehouse.dir=<lab>/warehouse -f {file}`；冷启动约 410s |
| Spark + Iceberg 1.11.0 | 同上 + `--jars iceberg-spark-runtime-4.1_2.13-1.11.0.jar` | 加 `spark.sql.extensions=…IcebergSparkSessionExtensions`、`spark.sql.catalog.lake=…SparkCatalog`、`…type=hadoop` |
| Spark + Delta 4.4.0 | `--packages io.delta:delta-spark_4.1_2.13:4.4.0 --repositories <aliyun>` | 单 jar 会缺 `delta-storage`；加 DeltaSparkSessionExtension + DeltaCatalog |
| Spark + Hudi 1.2.0 | 同上 + `--jars hudi-spark4.1-bundle_2.13-1.2.0.jar` | **必须** `spark.serializer=KryoSerializer`；表加 `primaryKey/preCombineField/type=COW` |
| Spark + Paimon 2.0.0 | `--packages org.apache.paimon:paimon-spark-4.1_2.13:2.0.0` | **必须** `spark.sql.extensions=…PaimonSparkSessionExtensions` |
| PostgreSQL 16 | `docker run -d --name ddp-pg -e POSTGRES_HOST_AUTH_METHOD=trust -p 55432:5432 postgres:16` | `psql -v ON_ERROR_STOP=1 -h {host} -p {port} -U {user} -d {database} -f {file}` |
| MySQL 8.4 | `docker run -d --name ddp-my -e MYSQL_ROOT_PASSWORD=ddp -p 53306:3306 mysql:8.4.4` | `mysql -h {host} -P {port} -u {user} -D {database} -e {sql}`；口令走 `MYSQL_PWD` |

## 起不来的组件（本轮失败现场）

| 组件 | 为什么起不来 | 现场 |
| --- | --- | --- |
| Apache Doris | 镜像源拒绝该命名空间；Aliyun Apache 镜像的 tarball 路径 404 | 见 `docs/TEST-REPORT-doris-unverified.md`（四条尝试的原始报错） |
| StarRocks | Hub 直连超时、daocloud 镜像层反复 retry（多次尝试仍未拉全） | 见 `docs/TEST-REPORT-starrocks-unverified.md` |

## 把插件本身装到 WSL（验过的路径）

```bash
git clone <仓库> ~/rtd && cd ~/rtd
python3 tools/adapt_hooks.py --check     # 会提示 launchers=[py -3] 不可用
python3 tools/adapt_hooks.py             # 切成本机 python3（README 第 3 步，必做）
python3 tools/adapt_hooks.py --check     # 通过：launchers=[python3]
python3 engine/ddp.py demo --root /tmp/ddp-demo   # 不碰真实组件，跑通 P1→P6 闭环
python3 -m unittest discover -s tests    # 246 OK
```

实测（2026-09-30，WSL2 Debian 13 / Python 3.13.5，克隆到 Linux 文件系统）：
上述步骤全通，`demo` 输出 `{"ok": true, "mode": "mock"}`。
**唯一必做的手工步骤是 `adapt_hooks`**——不做的话 hook 命令会失败，而宿主把 hook 失败当放行，
十几道硬门会静默失效（仓库 README 就写着这条）。
