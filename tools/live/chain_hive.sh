#!/usr/bin/env bash
# 由 .tmp/live-hive/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# LIVE-003：Hive 单组件真跑（apache/hive 单机 + beeline；内存大头，起它之前先停其他组件）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-hive/proj"
HV=ddp-hive
IMAGE=apache/hive:4.0.0

echo "=========== 0) 资源纪律：停掉其他组件容器"
docker stop ddp-ch ddp-trino ddp-pg ddp-my >/dev/null 2>&1 || true
docker rm -f "$HV" >/dev/null 2>&1 || true
docker ps --format '{{.Names}} {{.Status}}'
free -m | sed -n '1,2p'

echo "=========== 0b) 起 Hive（单机，项目目录按同路径挂进去给 beeline 读 SQL 文件）"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
docker run -d --name "$HV" -p 10000:10000 -p 10002:10002 \
  -v "$TT:$TT" \
  -e SERVICE_NAME=hiveserver2 \
  "$IMAGE" >/dev/null
for i in $(seq 1 40); do
  if docker exec "$HV" bash -lc "beeline -u 'jdbc:hive2://localhost:10000/default' -e 'select 1' >/dev/null 2>&1"; then
    echo "hive ready (query ok) after ${i}0s"; break
  fi
  sleep 10
done
docker ps --filter name="$HV" --format '{{.Names}} {{.Status}} {{.Image}}'
docker exec "$HV" bash -lc "beeline -u 'jdbc:hive2://localhost:10000/default' -e 'select version()' 2>/dev/null | tail -3"

echo "=========== 1) DDP 项目（kind=hive，command 族，客户端在容器内）"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
cfg = {
    "mode": "live",
    "platform": {
        "kind": "hive",
        "command": {
            "engine": "hive",
            "run": ["docker", "exec", "-i", "ddp-hive", "beeline", "-u",
                    "jdbc:hive2://localhost:10000/default", "-f", "{file}"],
            "timeout": 600,
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
    "primary_key": [],
    "properties": {},
}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] config + spec written")
PY

echo "=========== 2) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor

echo "=========== 3) ddl render（hive 方言：分区列只进 PARTITIONED BY）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >/dev/null
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

echo "=========== 4) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE | tail -8

echo "=========== 5) 真跑写入（静态分区）"
cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO ods_orders_live PARTITION (ds='2026-09-28') VALUES
  (1, 10.50), (2, 20.25), (3, 30.00);
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE | tail -8

echo "=========== 6) 读回（真实查询）"
docker exec "$HV" beeline -u 'jdbc:hive2://localhost:10000/default' --outputformat=tsv2 \
  -e 'SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live' 2>&1 \
  | grep -vE 'WARNING|No such file|^$|^Closing|jdbc:hive2' | tail -4
echo "--- 分区清单"
docker exec "$HV" beeline -u 'jdbc:hive2://localhost:10000/default' --outputformat=tsv2 \
  -e 'SHOW PARTITIONS ods_orders_live' 2>&1 \
  | grep -vE 'WARNING|No such file|^$|^Closing|jdbc:hive2' | tail -4

echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} engine={run.get('engine')} evidence_level={run.get('evidence_level')} sql_hash={str(run.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE

echo "=========== 8) 失败案例：非 ACID 表上的 UPDATE（方言规则说 Hive 不支持）"
cat > "$TT/update.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
UPDATE ods_orders_live SET amt = 1.00 WHERE order_id = 1;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/update.sql" --env dev 2>&1 | tail -5

echo "=========== 9) 真实 P3 出口门（dp_task 用静态分区写法）"
cat > "$TT/dp_task_insert.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- schedule: 0 2 * * *
-- env: dev
INSERT INTO ods_orders_live PARTITION (ds='2026-09-29') VALUES (4, 40.00), (5, 50.00);
SQL
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live T-LIVE "$TT/dp_task_insert.sql" || true

echo "=========== 10) 释放资源"
docker stop "$HV" >/dev/null 2>&1 && echo "ddp-hive stopped"
docker ps --format '{{.Names}} {{.Status}}'
