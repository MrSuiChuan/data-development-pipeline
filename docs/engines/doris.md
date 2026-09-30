# Doris 接入

| 项 | 值 |
| --- | --- |
| kind | `doris` |
| family | `dbapi`（pymysql 直连 MySQL 协议） |
| dialect | `doris` |
| 必填配置键 | `platform.host` |
| 默认端口 | 9030 |
| 证据级别 | `local_receipt` |
| 验证状态 | **真跑通过（Doris 4.1.4.1-rc02，2026-09-30，官方二进制原生 FE+BE）**，见 [TEST-REPORT-doris-live-2026-09-30.md](../TEST-REPORT-doris-live-2026-09-30.md) |

## 本地真跑记录（2026-09-30）

- 起法（不走 Docker，绕开被拒的镜像源）：官方二进制 `apache-doris-4.1.4.1-bin-x64.tar.gz`
  （3.96 GiB，sha512 校验）解包 → 起 FE → `CREATE DATABASE` → `ALTER SYSTEM ADD BACKEND` → 起 BE。
- **FE 硬性要求 JDK 17**：宿主是 JDK 21 时 `start_fe.sh` 直接拒绝
  （`ERROR: The jdk_version is 21, must be 17.`）。
- **BE 的前置检查在单机 WSL 上过不去**：`vm.max_map_count` 要 2000000（非 root 改不了）且有 swap 就拒启；
  官方留的口子是 `SKIP_CHECK_ULIMIT=true`。跳过是有意识地跳过，生产必须按官方要求配。
- **单 BE 集群的副本数**：默认 3 → 建表直接失败
  （`replication num should be less than the number of available backends`），
  本地联调在 spec 里给 `properties.replication_allocation = "tag.location.default:1"`。
- 结果：`ddl render` 的模板建表成功 → 写入三行 → 读回 **3 行 / 60.75** → 账本 → `evidence verify` →
  **P3 出口门 FAIL=0**。
- 真缺陷（本轮修）：Doris 的 DDL 里 **`ENGINE = OLAP` 必须写在键子句之前**，
  反着写给会得到 `mismatched input 'ENGINE' expecting {<EOF>, ';'}`。

## 配置示例

```json
{
  "platform": {
    "kind": "doris",
    "host": "127.0.0.1",
    "port": 9030,
    "user": "root",
    "password_env": "DDP_PLATFORM_PASSWORD",
    "database": "ods",
    "base_env": "dev",
    "profiles": {"prod": {"host": "prod-doris.internal", "database": "ods"}}
  }
}
```

口令走 `password_env`（默认 `DDP_PLATFORM_PASSWORD`），不落明文；dbapi 族需要 `pymysql`：

```
py -3 -m pip install pymysql
```

## 方言差异（`sql check --dialect doris`）

| 规则 | 级别 | 说明 | 真引擎背书 |
| --- | --- | --- | --- |
| `dialect.doris.row_write_needs_key_model` | WARN | Doris 的 `UPDATE`/`DELETE` 只支持主键/唯一模型，聚合与明细模型不支持 | ✅ 2026-09-30：唯一模型表 `UPDATE` 成功；`DUPLICATE KEY` 表报 `Only unique table could be updated.` |

`starrocks` 与 Doris 共用这段模板与同一族方言规则，但**没有在真引擎上验证**：
引擎注册表里也写着"与 Doris 方言相近但不等价（版本特性差异未逐条验证）"。

## 交付物惯例

与其它 kind 一致：DDL 渲染到 `.data-dev/tickets/<票>/ddl/<表>.sql`，ETL 到 `etl/`，
执行回执进 `.data-dev/runtime/executions.json`，再走 `evidence verify` 与 P3 出口门。
