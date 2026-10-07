#!/usr/bin/env bash
# 由 .tmp/live-starrocks/chain2.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# WSL-002 链路（正路）：kind=starrocks（dbapi + pymysql），方言才会是 starrocks
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-starrocks/proj-split"
FE_PORT=19030
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"

echo "=========== 0) 干净起点"
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -e "CREATE DATABASE IF NOT EXISTS ods;" 2>&1 | grep -v WARNING | tail -1
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$FE_PORT" <<'PY'
import json, sys
tt, port = sys.argv[1], int(sys.argv[2])
cfg = {"mode": "live",
       "platform": {"kind": "starrocks", "host": "127.0.0.1", "port": port, "user": "root",
                    "password": "", "password_env": "DDP_PLATFORM_PASSWORD",
                    "database": "ods", "base_env": "dev"},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": ["order_id"],
        "properties": {"replication_num": "1"}}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] kind=starrocks(dbapi) + spec written, port =", port)
PY

echo "=========== 1) doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -10
echo "=========== 2) ddl render（starrocks 方言）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" 2>&1 | tail -4
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"
echo "=========== 3) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
echo "=========== 4) 真跑写入"
cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO ods_orders_live (order_id, amt, ds) VALUES
  (1, 10.50, '2026-09-28'),
  (2, 20.25, '2026-09-28'),
  (3, 30.00, '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
echo "=========== 5) 读回"
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e \
  "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;" 2>&1 | tail -3
echo "=========== 6) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')} hash={str(r.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -5
echo "=========== 7) 方言现场：唯一模型 UPDATE/DELETE vs 明细模型"
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e "UPDATE ods_orders_live SET amt = 11.50 WHERE order_id = 1;" 2>&1 | grep -v WARNING | tail -2 && echo "  UPDATE ok"
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e "DELETE FROM ods_orders_live WHERE order_id = 2;" 2>&1 | grep -v WARNING | tail -2 && echo "  DELETE ok"
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e "SELECT count(*) AS rows_now, sum(amt) AS amt_sum FROM ods_orders_live;" 2>&1 | tail -2
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e "CREATE TABLE IF NOT EXISTS ods_orders_dup (order_id BIGINT, amt DECIMAL(18,2), ds DATE) ENGINE=OLAP DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1 PROPERTIES('replication_num'='1');" 2>&1 | grep -v WARNING | tail -2
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e "INSERT INTO ods_orders_dup VALUES (1, 10.50, '2026-09-28');" 2>&1 | grep -v WARNING | tail -2
echo "  明细模型上的 DELETE（规则说不行）："
mysql -h 127.0.0.1 -P "$FE_PORT" -uroot -D ods -e "DELETE FROM ods_orders_dup WHERE order_id = 1;" 2>&1 | grep -v WARNING | tail -3
echo "=========== 8) 静态侧：sql check --dialect starrocks"
cat > "$TT/delete.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
DELETE FROM ods_orders_live WHERE order_id = 1;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" sql check --file "$TT/delete.sql" --dialect starrocks 2>&1 | tail -12
echo "=========== 9) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE || true
