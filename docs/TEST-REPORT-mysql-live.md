# 真实引擎联调报告：MySQL 8.4.4

**目的**：同 PostgreSQL 报告，第二台真实引擎；这一台走的是 **command 族 + `command.engine=mysql`**，
顺带验证"通用 argv 执行器如何拿到具体引擎的方言"。对应台账 COMPAT-009。

**环境**：WSL2 Debian 13 / Docker 26.1.5 / `mysql:8.4.4` 容器（端口 53306，库 `ods`）
**日期**：2026-09-28 ｜ **脚本**：`.tmp/live-my/run.sh`

## 1. 配置与体检

```json
{
  "platform": {
    "kind": "command",
    "host": "127.0.0.1", "port": 53306, "user": "root", "database": "ods",
    "command": {
      "engine": "mysql",
      "run": ["mysql", "-h", "{host}", "-P", "{port}", "-u", "{user}", "-D", "{database}", "-e", "{sql}"]
    }
  }
}
```

```
$ python3 engine/ddp.py platform doctor
{"ok": true, "kind": "command", "family": "command", "dialect": "mysql", "evidence": "local_receipt"}
```

口令走环境变量（`MYSQL_PWD`）：`-p<口令>` 会出现在进程列表里，插件不替你放。

## 2. 建表 → 写入 → 读回

```
$ python3 engine/ddp.py ddl render --spec spec.json --out .../ddl/ods_orders_live.sql
CREATE TABLE IF NOT EXISTS ods_orders_live (
  order_id BIGINT COMMENT '订单号',
  amt DECIMAL(18,2),
  ds DATE,
  PRIMARY KEY (order_id)
)
ENGINE = InnoDB;

$ python3 engine/ddp.py platform run-sql --file .../ddl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_f4193e049e7460", "engine": "mysql", "evidence_level": "local_receipt"}
$ python3 engine/ddp.py platform run-sql --file .../etl/ods_orders_live.sql --env dev --save-evidence --ticket T-LIVE
{"status": "SUCCESS", "query_id": "run_c574982bb96d00", "engine": "mysql", "evidence_level": "local_receipt"}

$ mysql -h 127.0.0.1 -P 53306 -uroot -D ods -e "SELECT count(*), sum(amt) FROM ods_orders_live;"
3	60.75
```

## 3. 证据链

```
$ cat .data-dev/runtime/executions.json
  query_id=run_f4193e049e7460 env=dev engine=mysql evidence_level=local_receipt sql_hash=a12ce93b1ddc
  query_id=run_c574982bb96d00 env=dev engine=mysql evidence_level=local_receipt sql_hash=a037edf10fd5
$ python3 engine/ddp.py evidence verify --ticket T-LIVE
{"ok": true, "key": "verify_result", "source": "etl_dev_exec.json"}
```

## 4. 失败案例

| 现象 | 现场 | 处置 |
| --- | --- | --- |
| **建表丢了主键、方言退化成 generic** | 首轮 `ddl render` 出来的是 `CREATE TABLE ... (order_id BIGINT, ...);`——没有 `PRIMARY KEY`、没有 `ENGINE` | **判定为真缺陷并修掉**：`kind=command` 时方言必须来自 `platform.command.engine`；只按 kind 推导会把 MySQL/Hive 的 DDL 与检查全退化成 generic |
| 口令没传进去 | `ERROR 1045 Access denied ... (using password: NO)` | 命令族靠环境变量传口令（`MYSQL_PWD`），不在 argv 里放 `-p` |
| 主键冲突 | `ERROR 1062 (23000): Duplicate entry '1' for key 'ods_orders_live.PRIMARY'` | 正确：真实约束生效、错误原样暴露（这条同时证明主键确实建上了） |
| 行数累计 | 第二轮读回 11 行（上一轮残留） | 测试脚本每轮先 `DROP TABLE`；真实联调必须从干净状态开始，否则"读回"会被误读成"写对了" |

## 5. 结论与边界

**已证明**：MySQL 8.4 上建表/写入/读回/证据落账/证据核验可用；`command.engine` 决定方言这条链路已修正并有回归用例
（`tests/test_engines.py::test_command_engine_decides_dialect`）。

**未证明**：`pymysql` 直连路径（dbapi 族）这次没跑——WSL 里没装 `pymysql`，脚本自动退回 command 族；
要验 dbapi 族需要 `pip install pymysql` 或换一台有驱动的机器。MySQL 的字符集/时区参数、
主从/云 RDS 连接串未覆盖。
