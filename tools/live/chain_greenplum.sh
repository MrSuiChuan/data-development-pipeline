#!/usr/bin/env bash
# 由 .tmp/live-close/gp-chain.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试：Greenplum 6.8（kind=greenplum，command 族 + postgres 方言）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-greenplum/proj"
CT=ddp-gp-live
# psql 要先把 GP 自己的 lib 目录加进 LD_LIBRARY_PATH（greenplum_path.sh 干这事），
# 所以走 bash -lc + $1 传 SQL：既不丢库路径，也不怕 SQL 里的引号。
GPRC=/opt/greenplum-db-6.8.1/greenplum_path.sh
psql_run() {  # psql_run <db> <sql>
  docker exec -i -u gpadmin "$CT" bash -lc "source $GPRC && exec psql -v ON_ERROR_STOP=1 -p 5432 -d $1 -c \"\$1\"" ddp-psql "$2"
}
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"

echo "=========== 0) 建库 ddp"
psql_run postgres "CREATE DATABASE ddp" 2>&1 | tail -2
psql_run postgres "select version()" 2>&1 | head -4 | tail -2

echo "=========== 1) DDP 项目（kind=greenplum）"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$CT" "$GPRC" <<'PY'
import json, sys
tt, ct, gprc = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {"mode": "live",
       "platform": {"kind": "greenplum", "host": "127.0.0.1", "port": 5432, "user": "gpadmin",
                    "database": "ddp", "base_env": "dev",
                    "command": {"engine": "greenplum",
                                 "run": ["docker", "exec", "-i", "-u", "gpadmin", ct, "bash", "-lc",
                                         f"source {gprc} && "
                                         "exec psql -v ON_ERROR_STOP=1 -p 5432 -d ddp -c \"$1\"",
                                         "ddp-psql", "{sql}"],
                                "timeout": 600}},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": ["order_id"], "properties": {}}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] kind=greenplum + spec written（psql 走 docker exec，argv 不经过 shell，SQL 原样传）")
PY

echo "=========== 2) doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -9
echo "=========== 3) ddl render（postgres 方言）"
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
  (1, 10.50, DATE '2026-09-28'),
  (2, 20.25, DATE '2026-09-28'),
  (3, 30.00, DATE '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
echo "=========== 6) 读回"
psql_run ddp "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live" 2>&1 | head -5
echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')} hash={str(r.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -4
echo "=========== 8) GP 专有现场：分布键（postgres 方言只覆盖通用部分）"
psql_run ddp "SELECT gp_segment_id, count(*) FROM ods_orders_live GROUP BY 1 ORDER BY 1" 2>&1 | head -7
echo "=========== 9) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE || true
