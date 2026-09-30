# StarRocks 接入

| 项 | 值 |
| --- | --- |
| kind | `starrocks` |
| family | `dbapi`（pymysql 直连 MySQL 协议） |
| dialect | `starrocks` |
| 必填配置键 | `platform.host` |
| 默认端口 | 9030 |
| 证据级别 | `local_receipt` |
| 验证状态 | **真跑通过（StarRocks 3.3.9-dfae8f9，2026-09-30，官方 FE/BE 镜像）**，见 [TEST-REPORT-starrocks-live.md](../TEST-REPORT-starrocks-live.md) |

## 本地真跑记录（2026-09-30）

- 起法：`starrocks/fe-ubuntu:3.3.9` + `starrocks/be-ubuntu:3.3.9`（FE/BE 分开）。
  两个镜像的默认 `Cmd` 都是 `/bin/bash`——`docker run -d` 会立刻退出，必须让容器保活
  （`tail -f /dev/null`）再 `docker exec` 进去 `nohup ./bin/start_fe.sh` / `start_be.sh`。
- **FE 默认堆 8192m**（`fe.conf` 的 `JAVA_OPTS`）：本机 7.8 GB 必须压到 2048m 才起得来。
- BE 起来后先 `curl 127.0.0.1:8040/api/health`（回 `{"status":"OK","msg":"To Be Added"}`），
  再 `ALTER SYSTEM ADD BACKEND 'starrocks-be:9050'`，等 `show backends` 里 `Alive: true`。
- 单 BE 集群：`spec.properties.replication_num = "1"`（默认副本数 3 会直接建表失败）。
- 结果：`ddl render` 模板建表成功 → 写入三行 → 读回 **3 行 / 60.75** → 账本 → `evidence verify` →
  **P3 出口门 FAIL=0**。
- 真缺陷（本轮修）：`BUCKETS AUTO` 是 Doris 的写法，StarRocks 3.3.9 会语法错
  （`Unexpected input 'AUTO'`）；StarRocks 侧现在不写 BUCKETS（引擎自动分桶）。

## 配置示例

```json
{
  "platform": {
    "kind": "starrocks",
    "host": "127.0.0.1",
    "port": 9030,
    "user": "root",
    "password_env": "DDP_PLATFORM_PASSWORD",
    "database": "ods",
    "base_env": "dev"
  }
}
```

**别用 `kind=command` + `engine=mysql` 冒充 StarRocks**：那样 `platform doctor` 会报
`dialect=mysql`，渲染出列内 `PRIMARY KEY` + `ENGINE = InnoDB`，真引擎直接报
`No viable statement for input ', PRIMARY'`（本轮踩过，报告第 6 节留了现场）。

## 方言差异（`sql check --dialect starrocks`）

| 规则 | 级别 | 说明 | 真引擎背书 |
| --- | --- | --- | --- |
| `dialect.starrocks.row_write_needs_key_model` | WARN | UPDATE 要主键模型；UNIQUE KEY 表不支持 UPDATE；DELETE 在唯一/主键模型上可用；明细/聚合模型都不行 | ✅ 2026-09-30：UNIQUE KEY 表 `UPDATE` 报 `table does not support update`、`DELETE` 成功；`PRIMARY KEY` 表 `UPDATE`/`DELETE` 都成功 |

类型映射与 Doris 不同：StarRocks 用 `DECIMAL(18,2)`，**没有** `DECIMALV3`
（真引擎实测：`Unexpected input 'DECIMALV3'`）。建表除键子句外，StarRocks 也不写 BUCKETS。
