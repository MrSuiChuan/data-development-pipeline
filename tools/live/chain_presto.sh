#!/usr/bin/env bash
# 由 .tmp/live-close/presto-chain.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试：Presto 0.299（kind=presto，command 族，dialect=trino）与其它引擎同一验收口径
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-presto/proj"
RUNNER="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-close/presto-sql.sh"
SRV=http://127.0.0.1:18080
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"

echo "=========== 0) 建 schema（memory connector 不会自动建）"
printf 'CREATE SCHEMA IF NOT EXISTS memory.ddp\n' > /tmp/presto-schema.sql
bash "$RUNNER" "$SRV" memory ddp /tmp/presto-schema.sql 2>&1 | tail -3
curl -sS -m 10 "$SRV/v1/info" | head -c 120; echo

echo "=========== 1) DDP 项目（kind=presto）"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$RUNNER" <<'PY'
import json, sys
tt, runner = sys.argv[1], sys.argv[2]
cfg = {"mode": "live",
       "platform": {"kind": "presto", "host": "127.0.0.1", "port": 18080, "user": "ddp",
                    "database": "ddp", "base_env": "dev",
                    "command": {"engine": "presto",
                                "run": ["bash", runner, "http://127.0.0.1:18080", "memory", "ddp", "{file}"],
                                "timeout": 600}},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": [], "properties": {}}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] kind=presto + spec written（memory connector 无分区/主键）")
PY

echo "=========== 2) doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -10
echo "=========== 3) ddl render（trino 方言，presto 共用）"
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
cat > /tmp/presto-readback.sql <<'SQL'
SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live
SQL
bash "$RUNNER" "$SRV" memory ddp /tmp/presto-readback.sql 2>&1 | tail -4
echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')} hash={str(r.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -4
echo "=========== 8) 方言现场：Trino/Presto 的 DELETE / MERGE（规则说要看 connector）"
cat > "$TT/delete.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
DELETE FROM ods_orders_live WHERE order_id = 1;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" sql check --file "$TT/delete.sql" --dialect trino 2>&1 | tail -8
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/delete.sql" --env dev 2>&1 | tail -3
echo "=========== 9) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE || true
