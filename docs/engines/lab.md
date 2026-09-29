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
