#!/usr/bin/env bash
# 由 .tmp/live-close/tidb-chain.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试：TiDB（kind=tidb，dbapi + pymysql）走与其它引擎同一验收口径
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-tidb/proj"
CT=tidb-live
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"
H=127.0.0.1; P=4000

echo "=========== 0) 起 TiDB（单机，unistore）"
if ! docker ps --filter name="$CT" --format '{{.Names}}' | grep -q .; then
  docker rm -f "$CT" >/dev/null 2>&1 || true
  docker run -d --name "$CT" -p 4000:4000 docker.1panel.live/pingcap/tidb:v7.5.4 >/dev/null
fi
for i in $(seq 1 30); do
  if mysql -h$H -P$P -uroot -e 'select 1' >/dev/null 2>&1; then echo "  TiDB ready after ~$((i*5))s"; break; fi
  sleep 5
done
mysql -h$H -P$P -uroot -e 'select version() as tidb_version' 2>&1 | tail -2
docker ps --filter name="$CT" --format '  {{.Names}} {{.Status}}'

echo "=========== 1) DDP 项目（kind=tidb）"
mysql -h$H -P$P -uroot -e "CREATE DATABASE IF NOT EXISTS ods;" 2>&1 | grep -v WARNING | tail -1
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
cfg = {"mode": "live",
       "platform": {"kind": "tidb", "host": "127.0.0.1", "port": 4000, "user": "root",
                    "password": "", "password_env": "DDP_PLATFORM_PASSWORD",
                    "database": "ods", "base_env": "dev"},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": ["order_id"], "properties": {}}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] kind=tidb + spec written")
PY

echo "=========== 2) doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -10
echo "=========== 3) ddl render（tidb→mysql 方言）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" 2>&1 | tail -3
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"
echo "=========== 4) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
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
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
echo "=========== 6) 读回"
mysql -h$H -P$P -uroot -D ods -e \
  "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;" 2>&1 | tail -3
echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')} hash={str(r.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -4
echo "=========== 8) 方言现场：MySQL 协议族没有 INSERT OVERWRITE（静态是 FAIL，看真引擎）"
cat > "$TT/overwrite.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT OVERWRITE ods_orders_live SELECT * FROM ods_orders_live;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" sql check --file "$TT/overwrite.sql" --dialect mysql 2>&1 | tail -8
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/overwrite.sql" --env dev 2>&1 | tail -3
echo "=========== 9) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE || true
