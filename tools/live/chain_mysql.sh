#!/usr/bin/env bash
# 由 .tmp/live-my/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实引擎联调（COMPAT-009）：MySQL 8.4 上跑「建表 → 写入 → 读回 → 证据落账」
# 优先走 dbapi 族（pymysql 直连，SqlPlatformClient）；装了不成就退回 command 族（mysql 客户端）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT=/tmp/ddp-live-my
MPORT=53306

echo "=========== 0) 环境探测"
docker ps --filter name=ddp-my --format '{{.Names}} {{.Status}}'
if python3 -c "import pymysql" 2>/dev/null; then
  ADAPTER=dbapi
else
  ADAPTER=command
fi
echo "adapter family = $ADAPTER"

rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null

python3 - "$TT" "$ADAPTER" "$MPORT" <<'PY'
import json, sys
tt, adapter, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
platform = {
    "kind": "mysql",
    "host": "127.0.0.1", "port": port, "user": "root", "password_env": "DDP_PLATFORM_PASSWORD",
    "database": "ods", "base_env": "dev",
}
if adapter == "dbapi":
    platform["password"] = ""          # 走 password_env
else:
    platform["kind"] = "command"
    platform["command"] = {
        "engine": "mysql",
        "run": ["mysql", "-h", "{host}", "-P", "{port}", "-u", "{user}", "-D", "{database}", "-e", "{sql}"],
        "timeout": 300,
    }
cfg = {"mode": "live", "platform": platform, "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {
    "table": "ods_orders_live",
    "columns": [
        {"name": "order_id", "type": "bigint", "comment": "订单号"},
        {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
        {"name": "ds", "type": "date"},
    ],
    "partition_by": [],
    "primary_key": ["order_id"],
    "properties": {},
}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] config + spec written, adapter =", adapter)
PY

export DDP_PLATFORM_PASSWORD=ddp
# mysql 客户端从 MYSQL_PWD 读口令（argv 会进进程列表，不放 -p 参数）
export MYSQL_PWD=ddp

echo "=========== 0b) 干净起点：删掉上次运行留下的表"
mysql -h 127.0.0.1 -P 53306 -uroot -D ods -e "DROP TABLE IF EXISTS ods_orders_live;" && echo "dropped (if existed)"

echo "=========== 1) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor

echo "=========== 2) ddl render（按 mysql 方言）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >/dev/null
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

echo "=========== 3) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" \
    --env dev --save-evidence --ticket T-LIVE

cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO ods_orders_live (order_id, amt, ds) VALUES
  (1, 10.50, '2026-09-28'),
  (2, 20.25, '2026-09-28'),
  (3, 30.00, '2026-09-28');
SQL

echo "=========== 4) 真跑写入（etl）"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" \
    --env dev --save-evidence --ticket T-LIVE

echo "=========== 5) 读回（真实查询）"
MYSQL_PWD=ddp mysql -h 127.0.0.1 -P "$MPORT" -uroot -D ods \
    -e "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;"

echo "=========== 6) 执行账本"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} env={run.get('env')} engine={run.get('engine')} "
          f"evidence_level={run.get('evidence_level')} sql_hash={str(run.get('sql_hash'))[:12]}")
PY

echo "=========== 7) evidence verify"
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE

echo "=========== 8) 失败案例：主键冲突（真实引擎报错）"
cat > "$TT/.data-dev/tickets/T-LIVE/etl/bad.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO ods_orders_live (order_id, amt, ds) VALUES (1, 99.99, '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/bad.sql" --env dev 2>&1 | tail -3
