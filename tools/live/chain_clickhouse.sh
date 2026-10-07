#!/usr/bin/env bash
# 由 .tmp/live-ch/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# LIVE-001：ClickHouse 单个组件真跑（起它之前先把其他组件容器停掉）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-ch/proj"
CH=ddp-ch
IMAGE=clickhouse/clickhouse-server:24.8

echo "=========== 0) 资源纪律：停掉其他组件容器，只留 ClickHouse"
docker stop ddp-pg ddp-my >/dev/null 2>&1 || true
docker rm -f "$CH" >/dev/null 2>&1 || true
docker ps --format '{{.Names}} {{.Status}}'
free -m | sed -n '1,2p'

echo "=========== 0b) 起 ClickHouse（单实例）"
# 先把项目目录建好（属主是我），再把它按同一路径挂进容器——
# 反过来会让 Docker 以 root 创建挂载点，后面写 SQL 文件就会 Permission denied。
rm -rf "$TT"; mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
docker run -d --name "$CH" -p 9000:9000 -p 8123:8123 \
  -v "$TT:$TT" \
  -e CLICKHOUSE_USER=default -e CLICKHOUSE_PASSWORD=ddp -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  "$IMAGE" >/dev/null
for i in $(seq 1 30); do
  if docker exec "$CH" clickhouse-client --password ddp --query "select 1" >/dev/null 2>&1; then
    echo "clickhouse ready after ${i}0s"; break
  fi
  sleep 10
done
docker ps --filter name="$CH" --format '{{.Names}} {{.Status}} {{.Image}}'
docker exec "$CH" clickhouse-client --password ddp --query "select version()" 2>/dev/null | tail -1

echo "=========== 1) DDP 项目（kind=clickhouse，command 族，客户端在容器内）"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
cfg = {
    "mode": "live",
    "platform": {
        "kind": "clickhouse",
        "command": {
            "engine": "clickhouse",
            "run": ["docker", "exec", "-i", "-e", "CLICKHOUSE_PASSWORD=ddp", "ddp-ch",
                    "clickhouse-client", "--multiquery", "--queries-file", "{file}"],
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

echo "=========== 2) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor

echo "=========== 3) ddl render（clickhouse 方言）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >/dev/null
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

echo "=========== 4) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE

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
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE

echo "=========== 6) 读回（真实查询）"
docker exec "$CH" clickhouse-client --password ddp --query \
  "SELECT count() AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live"

echo "=========== 7) 执行账本 + evidence verify"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} engine={run.get('engine')} evidence_level={run.get('evidence_level')} sql_hash={str(run.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE

echo "=========== 8) 失败案例一：UPDATE ... FROM（方言规则说它 FAIL，看真引擎）"
cat > "$TT/update_from.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
UPDATE ods_orders_live SET amt = 1 FROM ods_orders_raw WHERE ods_orders_live.order_id = ods_orders_raw.order_id;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/update_from.sql" --env dev 2>&1 | tail -4

echo "=========== 9) 失败案例二：ALTER ... UPDATE（异步 mutation，规则说 WARN）"
cat > "$TT/mutation.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
ALTER TABLE ods_orders_live UPDATE amt = 99.99 WHERE order_id = 1;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/mutation.sql" --env dev 2>&1 | tail -6
docker exec "$CH" clickhouse-client --password ddp --query \
  "SELECT count() AS pending_mutations FROM system.mutations WHERE is_done = 0" 2>/dev/null

echo "=========== 10) 方言检查（同一份 UPDATE...FROM 走 sql check --dialect clickhouse）"
python3 "$REPO/engine/ddp.py" --root "$TT" sql check --file "$TT/update_from.sql" --dialect clickhouse | tail -18

echo "=========== 11) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live || true

echo "=========== 12) 释放资源：停掉 ClickHouse"
docker stop "$CH" >/dev/null 2>&1 && echo "ddp-ch stopped"
docker ps --format '{{.Names}} {{.Status}}'
