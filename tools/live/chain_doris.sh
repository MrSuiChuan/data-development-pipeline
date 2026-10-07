#!/usr/bin/env bash
# 由 .tmp/live-doris/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# WSL-001 真跑：Doris 4.1.4.1（原生 FE+BE，不是 Docker）
# 链路与 LIVE-001…007 完全一致：doctor → ddl render → 建表 → 写入 → 读回一致 → 账本 → verify → P3 门
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-doris/proj"
H=127.0.0.1; P=9030
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"

echo "=========== 0) 干净起点"
mysql -h$H -P$P -uroot -e "DROP DATABASE IF EXISTS ods; CREATE DATABASE ods;" && echo "ods 库已重建"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null

python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
cfg = {
    "mode": "live",
    "platform": {
        "kind": "doris",
        "host": "127.0.0.1", "port": 9030, "user": "root", "password": "",
        "password_env": "DDP_PLATFORM_PASSWORD", "database": "ods", "base_env": "dev",
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
    # 真引擎要求：单 BE 本地集群默认副本数 3 建不出表，显式给 1 个副本
    "properties": {"replication_allocation": "tag.location.default:1"},
}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] config(kind=doris) + spec written")
PY

echo "=========== 1) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor 2>&1 | tail -12

echo "=========== 2) ddl render（doris 方言）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" 2>&1 | tail -6
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

echo "=========== 3) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" \
    --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -6

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
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" \
    --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -6

echo "=========== 5) 读回（真实查询）"
mysql -h$H -P$P -uroot -D ods -e \
  "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;"

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
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -8

echo "=========== 8) 方言规则的真实现场：UPDATE / DELETE 与表模型"
cat > "$TT/update_from.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
UPDATE ods_orders_live SET amt = 1 FROM ods_orders_raw WHERE ods_orders_live.order_id = ods_orders_raw.order_id;
SQL
echo "-- 8a) UPDATE ... FROM（MySQL 协议族不支持这种写法）"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/update_from.sql" --env dev 2>&1 | tail -3

echo "-- 8b) 唯一模型表上的 UPDATE（应当成功）"
mysql -h$H -P$P -uroot -D ods -e "UPDATE ods_orders_live SET amt = 11.50 WHERE order_id = 1;" 2>/dev/null \
  && echo "UPDATE ok" || echo "UPDATE failed"
mysql -h$H -P$P -uroot -D ods -e "SELECT order_id, amt FROM ods_orders_live ORDER BY order_id;" 2>/dev/null

echo "-- 8c) 明细模型（DUPLICATE KEY）上的 UPDATE（规则说不行，看真引擎）"
mysql -h$H -P$P -uroot -D ods -e "DROP TABLE IF EXISTS ods_orders_dup;" 2>/dev/null
mysql -h$H -P$P -uroot -D ods -e "CREATE TABLE ods_orders_dup (order_id BIGINT, amt DECIMALV3(18,2), ds DATE) ENGINE=OLAP DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS AUTO PROPERTIES('replication_allocation'='tag.location.default:1');" 2>&1 | grep -v WARNING | tail -3
mysql -h$H -P$P -uroot -D ods -e "INSERT INTO ods_orders_dup VALUES (1, 10.50, '2026-09-28');" 2>&1 | tail -2
mysql -h$H -P$P -uroot -D ods -e "UPDATE ods_orders_dup SET amt = 99.99 WHERE order_id = 1;" 2>&1 | grep -v WARNING | tail -3

echo "-- 8d) 静态侧：sql check --dialect doris 对同一份 UPDATE 怎么判"
python3 "$REPO/engine/ddp.py" --root "$TT" sql check --file "$TT/update_from.sql" --dialect doris 2>&1 | tail -12
echo "-- 8e) 交互式查询读回（UPDATE 之后）"
mysql -h$H -P$P -uroot -D ods -e "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live;" 2>/dev/null

echo "=========== 9) 真实 P3 出口门"
cat > "$TT/p3_insert.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- schedule: 0 2 * * *
-- env: dev
INSERT INTO ods_orders_live (order_id, amt, ds) VALUES
  (4, 40.00, '2026-09-29'),
  (5, 50.00, '2026-09-29');
SQL
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE "$TT/p3_insert.sql" || true
