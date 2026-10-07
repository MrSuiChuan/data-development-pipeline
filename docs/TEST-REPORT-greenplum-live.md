# 真实引擎联调报告：Greenplum 6.8.1

**目的**：把矩阵里 greenplum 的"未验证"填实（台账 `CLOSE-008`）。greenplum 与 postgres 共用方言，
但 GP 是 MPP 发行版——这份报告给的是真 GP 上的现场。

| 项 | 值 |
| --- | --- |
| 引擎 | `PostgreSQL 9.4.24 (Greenplum Database 6.8.1 build commit:7118e8aca8...)` |
| 安装方式 | 镜像 `datagrip/greenplum:6.8`（1.16 GB，经 `docker.1panel.live`）；容器内 `gpinitsystem` 初始化单机集群 |
| 端口 | 5432（容器内）→ 宿主 25433 |
| 用户 / 库 | `gpadmin` / `ddp` |
| kind / family / dialect | `greenplum` / `command` / `postgres` |
| 日期 | 2026-10-07 |
| 证据级别 | `local_receipt` |

## 0. 一句话结论

**真跑通过**：`ddl render → 建表 → 写入 3 行 → 读回 3 行 / 60.75 → 账本 → verify →
P3 出口门 FAIL=0（WARN=3）`，并且额外查了一次 `gp_segment_id` 分布（MPP 专有视角）。

## 1. 现场

```
$ psql -d postgres -c "select version()"
PostgreSQL 9.4.24 (Greenplum Database 6.8.1 build commit:7118e8aca825b743dd9477d19406fcc06fa53852) ...

$ ddp.py platform doctor
{"canonical_kind": "greenplum", "family": "command", "dialect": "postgres",
 "note": "与 PostgreSQL 同方言；分布键/存储参数等 GP 专有项未建模"}

$ ddp.py ddl render --spec spec.json          # postgres 方言（主键在列定义里）
$ ddp.py platform run-sql --file ddl … --save-evidence    # 建表成功
$ ddp.py platform run-sql --file etl … --save-evidence    # 写入 3 行
读回：rows_inserted=3  amt_sum=60.75
账本：query_id=run_27126370140ddf / run_902190ad94db60（engine=greenplum, level=local_receipt）
$ ddp.py evidence verify --ticket T-LIVE → {"ok": true, "source": "etl_dev_exec.json"}

真实 P3 出口门：FAIL=0  WARN=3（无表注解 / local_receipt / 新表跳过 code_diff）
$ SELECT gp_segment_id, count(*) FROM ods_orders_live GROUP BY 1 ORDER BY 1;   # MPP 分布视角
```

## 2. 两条接 GP 才踩得到的坑（都写进接入页）

| 现象 | 处置 |
| --- | --- |
| `psql` 直接跑报 `error while loading shared libraries: libpq.so.5` | GP 的客户端依赖自己的 lib 目录：必须先 `source <GP_HOME>/greenplum_path.sh`（本次把 argv 写成 `bash -lc 'source … && exec psql -c "$1"'`，用 `$1` 传 SQL，既保住库路径又不怕引号） |
| 镜像里 psql 在 `/opt/greenplum-db-6.8.1/bin/psql`，**不是** `/usr/local/greenplum-db/bin/psql` | 路径按镜像实际布局写；`gpinitsystem` 首次初始化要几分钟，起来后先 `select version()` 确认 |

## 3. 这一项**没有**证明什么

- 没验证 MPP 专有项：分布键选择、`DISTRIBUTED BY` 模板、资源组/资源队列、`gpexpand`；
- 没验证多 segment 上的真实并行与数据倾斜（本次单机镜像的多 segment 都在同一容器里）；
- 没验证 GP 与 PostgreSQL 在类型/DDL 上的差异（共用一套 postgres 方言规则，属已知近似）；
- `local_receipt` 不等于平台 query_id。

## 4. 取材记一笔

`datagrip/greenplum` 在 `docker.1panel.live` 上有 5.17 / 5.8 / 6.1 / 6.8 / latest；
本次用 **6.8**（1.16 GB，拉取约 8 分钟）。官方 `greenplum-db` 的 GitHub 发行包在本网络超时，
OceanBase 的单机镜像大层反复 Retrying（见台账 `CLOSE-009`）。
