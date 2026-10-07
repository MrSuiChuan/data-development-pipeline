#!/usr/bin/env bash
# 由 .tmp/live-pg/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实引擎联调（COMPAT-009）：PostgreSQL 16 上跑「建表 → 写入 → 读回 → 证据落账」
# 用法：bash .tmp/live-pg/run.sh
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT=/tmp/ddp-live-pg
export PGPASSWORD=ddp

rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null

echo "=========== 0) 干净起点：删掉上次运行留下的表"
psql -h 127.0.0.1 -p 55432 -U postgres -d ods -c "DROP TABLE IF EXISTS ods_orders_live CASCADE;" 2>&1 | tail -1

python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
cfg = {
    "mode": "live",
    "platform": {
        "kind": "postgres",
        "host": "127.0.0.1", "port": 55432, "user": "postgres", "database": "ods",
        "command": {
            "engine": "postgres",
            "run": ["psql", "-v", "ON_ERROR_STOP=1", "-h", "{host}", "-p", "{port}",
                    "-U", "{user}", "-d", "{database}", "-f", "{file}"],
            "timeout": 300,
        },
    },
    "scheduler": {"kind": "cron"},
}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {
    "table": "ods_orders_live",
    "columns": [
        {"name": "order_id", "type": "bigint", "comment": "订单号"},
        {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
        {"name": "ds", "type": "date"},
    ],
    "partition_by": ["ds"],
    "primary_key": ["order_id"],
    "properties": {},
}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] config + spec written")
PY

echo "=========== 1) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor

echo "=========== 2) ddl render（按 postgres 方言）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >/dev/null
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

echo "=========== 3) 真跑建表（platform run-sql）"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" \
    --env dev --save-evidence --ticket T-LIVE

cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO ods_orders_live (order_id, amt, ds) VALUES
  (1, 10.50, DATE '2026-09-28'),
  (2, 20.25, DATE '2026-09-28'),
  (3, 30.00, DATE '2026-09-28');
SQL

echo "=========== 4) 真跑写入（etl）"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" \
    --env dev --save-evidence --ticket T-LIVE

echo "=========== 5) 读回（真实查询，不是自述）"
psql -h 127.0.0.1 -p 55432 -U postgres -d ods -c "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;"

echo "=========== 6) 执行账本（引擎写的，含 sql_hash）"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} env={run.get('env')} engine={run.get('engine')} sql_hash={str(run.get('sql_hash'))[:12]} status={run.get('status')}")
PY

echo "=========== 7) evidence verify（交叉核对 query_id 在账本中）"
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE

echo "=========== 8) 失败案例：语法错的 SQL 必须被拒"
cat > "$TT/bad.sql" <<'SQL'
SELECT FROM WHERE;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/bad.sql" --env dev 2>&1 | tail -5

echo "=========== 9) 失败案例：方言不匹配（MySQL 协议族不支持 INSERT OVERWRITE）"
cat > "$TT/overwrite.sql" <<'SQL'
-- table: ads_x
-- source: ods_orders_live
INSERT OVERWRITE TABLE ads_x PARTITION (ds='2026-09-28') SELECT order_id FROM ods_orders_live;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" sql check --file "$TT/overwrite.sql" --dialect mysql | tail -20
