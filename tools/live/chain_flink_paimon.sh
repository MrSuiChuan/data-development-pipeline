#!/usr/bin/env bash
# 由 .tmp/live-close/flink-chain.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# CLOSE-004：Flink 1.20.3 + Paimon 1.4.1 的真跑链路（kind=flink, format=paimon）
# 关键点：sql-client 出错也返回 0，所以 argv 里包一层"抓 ERROR 就返回非零"。
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-flink/proj"
export JAVA_HOME="${DDP_JAVA17:-$HOME/doris-live/jdk17}"
export PATH="$JAVA_HOME/bin:$PATH"
FLINK_DIR="${FLINK_HOME:-$HOME/flink-live/flink-1.20.3}"
WARE="/tmp/flink-warehouse-live-$(date +%H%M%S)"
INIT=/tmp/ddp-flink-init.sql

cat > "$INIT" <<SQL
SET 'execution.target' = 'local';
SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'tableau';
CREATE CATALOG paimon WITH ('type' = 'paimon', 'warehouse' = 'file://$WARE');
USE CATALOG paimon;
CREATE DATABASE IF NOT EXISTS ods;
USE ods;
SQL
echo "warehouse = $WARE"

rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$FLINK_DIR" "$INIT" <<'PY'
import json, sys
tt, flink_dir, init = sys.argv[1], sys.argv[2], sys.argv[3]
# sql-client 出错也 exit 0：包一层，日志里出现 [ERROR] 就返回非零
runner = (
    f"{flink_dir}/bin/sql-client.sh -i {init} -f {{file}} > /tmp/ddp-flink-sql.log 2>&1; "
    "rc=$?; if grep -q '\\[ERROR\\]' /tmp/ddp-flink-sql.log; then rc=1; fi; "
    "tail -40 /tmp/ddp-flink-sql.log; exit $rc"
)
cfg = {"mode": "live",
       "platform": {"kind": "flink", "format": "paimon",
                    "command": {"engine": "flink", "run": ["bash", "-lc", runner], "timeout": 900}},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": ["order_id"], "properties": {}}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] kind=flink format=paimon + spec written")
PY

echo "=========== 1) doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -12
echo "=========== 2) ddl render（flink + paimon）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" 2>&1 | tail -4
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"
echo "=========== 3) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -6
echo "=========== 4) 真跑写入"
cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO ods_orders_live VALUES
  (1, 10.50, DATE '2026-09-28'),
  (2, 20.25, DATE '2026-09-28'),
  (3, 30.00, DATE '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -6
echo "=========== 5) 读回（走插件通道）"
cat > "$TT/select.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/select.sql" --env dev 2>&1 | grep -E 'rows_inserted|amt_sum|[0-9]+\.?[0-9]*' | tail -6
echo "=========== 6) 读回（绕开插件，直接 sql-client 再查一次，证明落盘了）"
cat > /tmp/flink-verify.sql <<'SQL'
SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;
SQL
cd "$FLINK_DIR" && "$FLINK_DIR/bin/sql-client.sh" -i "$INIT" -f /tmp/flink-verify.sql 2>&1 | grep -E 'rows_inserted|amt_sum|^\|' | tail -5
echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')} hash={str(r.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -5
echo "=========== 8) 失败案例：真跑一条错 SQL，看包装后能不能判失败"
cat > "$TT/bad.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
SELECT * FROM 不存在的表;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/bad.sql" --env dev 2>&1 | tail -4
echo "=========== 9) 真实 P3 出口门"
cat > "$TT/p3_insert.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- schedule: 0 2 * * *
-- env: dev
INSERT INTO ods_orders_live VALUES
  (4, 40.00, DATE '2026-09-29'),
  (5, 50.00, DATE '2026-09-29');
SQL
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE "$TT/p3_insert.sql" || true
