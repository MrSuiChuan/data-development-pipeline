#!/usr/bin/env bash
# 由 .tmp/live-close/generic-sql-chain.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试：generic-sql（任意 MySQL 协议库；dialect=generic 只有基线 17 项规则）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-generic-sql/proj"
CT=ddp-mysql-generic
PORT=23307
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"

echo "=========== 0) 起 MySQL 8.4（本机已有镜像）"
if ! docker ps --filter name="$CT" --format '{{.Names}}' | grep -q .; then
  docker rm -f "$CT" >/dev/null 2>&1 || true
  docker run -d --name "$CT" -p $PORT:3306 -e MYSQL_ROOT_PASSWORD=ddp -e MYSQL_DATABASE=ods mysql:8.4.4 >/dev/null
fi
for i in $(seq 1 40); do
  if mysql -h127.0.0.1 -P$PORT -uroot -pddp -e 'select 1' >/dev/null 2>&1; then echo "  MySQL ready after ~$((i*3))s"; break; fi
  sleep 3
done
mysql -h127.0.0.1 -P$PORT -uroot -pddp -e 'select version() as v' 2>/dev/null | tail -2

echo "=========== 1) DDP 项目（kind=generic-sql）"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$PORT" <<'PY'
import json, sys
tt, port = sys.argv[1], int(sys.argv[2])
cfg = {"mode": "live",
       "platform": {"kind": "generic-sql", "host": "127.0.0.1", "port": port, "user": "root",
                    "password": "ddp", "password_env": "DDP_PLATFORM_PASSWORD",
                    "database": "ods", "base_env": "dev"},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": ["order_id"], "properties": {}}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] kind=generic-sql + spec written")
PY

echo "=========== 2) doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -9
echo "=========== 3) ddl render（generic 方言：基线 17 项，按 MySQL 形状出）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" 2>&1 | tail -3
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"
echo "=========== 4) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -3
echo "=========== 5) 真跑写入"
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
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -3
echo "=========== 6) 读回"
mysql -h127.0.0.1 -P$PORT -uroot -pddp -D ods -e \
  "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;" 2>/dev/null | tail -2
echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -4
echo "=========== 8) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE || true
