#!/usr/bin/env bash
# 由 .tmp/live-trino/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# LIVE-002：Trino 单组件真跑（memory connector；起它之前先停掉其他组件）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-trino/proj"
TR=ddp-trino
IMAGE=trinodb/trino:476

echo "=========== 0) 资源纪律：停掉其他组件容器"
docker stop ddp-ch ddp-pg ddp-my >/dev/null 2>&1 || true
docker rm -f "$TR" >/dev/null 2>&1 || true
docker ps --format '{{.Names}} {{.Status}}'
free -m | sed -n '1,2p'

echo "=========== 0b) 起 Trino（memory catalog，堆上限 1.5G）"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
mkdir -p "$TT/trino/catalog"
printf 'connector.name=memory\n' > "$TT/trino/catalog/memory.properties"

# 官方镜像里没有 CLI；用阿里云 Maven 镜像下官方 trino-cli（宿主 Java 21 直接跑）
JAR="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-trino/trino-cli.jar"
if [ ! -s "$JAR" ]; then
  echo "下载 trino-cli-476-executable.jar（阿里云 Maven 镜像）"
  curl -sfL -o "$JAR" "https://maven.aliyun.com/repository/public/io/trino/trino-cli/476/trino-cli-476-executable.jar" \
    && ls -lh "$JAR"
fi
java -version 2>&1 | head -1
docker run -d --name "$TR" -p 8080:8080 \
  -v "$TT:$TT" \
  -v "$TT/trino/catalog:/etc/trino/catalog" \
  -e JAVA_TOOL_OPTIONS="-Xmx1500m" \
  "$IMAGE" >/dev/null
# 就绪判断必须是"能跑一条真查询"：/v1/info 在 coordinator 还初始化时就返回 200，
# 那时提交 DDL 会得到 "Trino server is still initializing"。
for i in $(seq 1 40); do
  if java -jar "$JAR" --server http://127.0.0.1:8080 --execute "SELECT 1" >/dev/null 2>&1; then
    echo "trino ready (query ok) after ${i}0s"; break
  fi
  sleep 10
done
  curl -s http://127.0.0.1:8080/v1/info | head -3
  echo "--- 宿主 CLI 自检"
  java -jar "$JAR" --server http://127.0.0.1:8080 --execute "SELECT 1" 2>&1 | tail -3

echo "=========== 1) DDP 项目（kind=trino）"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$JAR" <<'PY'
import json, sys
tt, jar = sys.argv[1], sys.argv[2]
cfg = {
    "mode": "live",
    "platform": {
        "kind": "trino",
        "command": {
            "engine": "trino",
            "run": ["java", "-jar", "%JAR%",
                    "--server", "http://127.0.0.1:8080", "--catalog", "memory", "--schema", "default",
                    "--file", "{file}"],
            "timeout": 300,
        },
    },
    "scheduler": {"kind": "cron"},
}
cfg["platform"]["command"]["run"] = [a.replace("%JAR%", jar) for a in cfg["platform"]["command"]["run"]]
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
spec = {
    "table": "ods_orders_live",
    "columns": [
        {"name": "order_id", "type": "bigint", "comment": "订单号"},
        {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
        {"name": "ds", "type": "date"},
    ],
    "partition_by": [],
    "primary_key": [],
    "properties": {},
}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print("[setup] config + spec written")
PY

echo "=========== 2) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor

echo "=========== 3) ddl render（trino 方言：WITH format/partitioned_by）"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >/dev/null
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

echo "=========== 4) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE | tail -8

echo "=========== 5) 真跑写入"
cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT INTO memory.default.ods_orders_live (order_id, amt, ds) VALUES
  (1, 10.50, DATE '2026-09-28'),
  (2, 20.25, DATE '2026-09-28'),
  (3, 30.00, DATE '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE | tail -8

echo "=========== 6) 读回（真实查询）"
java -jar "$JAR" --server http://127.0.0.1:8080 --catalog memory --schema default \
  --execute "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live" 2>&1 | tail -3

echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} engine={run.get('engine')} evidence_level={run.get('evidence_level')} sql_hash={str(run.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE

echo "=========== 8) 失败案例：INSERT OVERWRITE（方言规则说 Trino 没有这条）"
cat > "$TT/overwrite.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
INSERT OVERWRITE TABLE memory.default.ods_orders_live SELECT 9, 9.99, DATE '2026-09-28';
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/overwrite.sql" --env dev 2>&1 | tail -4

echo "=========== 9) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live || true

echo "=========== 10) 释放资源"
docker stop "$TR" >/dev/null 2>&1 && echo "ddp-trino stopped"
docker ps --format '{{.Names}} {{.Status}}'
