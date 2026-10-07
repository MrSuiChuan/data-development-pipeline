#!/usr/bin/env bash
# 由 .tmp/live-spark/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# LIVE-004：Spark SQL 真跑（宿主解压发行包，local 模式；不进容器）
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-spark/proj"
OSS="$HOME/oss"
VER=4.1.3
SPARK="$OSS/spark-$VER-bin-hadoop3"   # 发行包解压目录带 -bin-hadoop3 后缀
TGZ="$OSS/spark-$VER-bin-hadoop3.tgz"
MIRROR=https://mirrors.ustc.edu.cn/apache/spark/spark-$VER

echo "=========== 0) 资源纪律：停掉其他组件容器（Spark 跑在宿主，不占容器内存）"
docker stop ddp-hive ddp-ch ddp-trino ddp-pg ddp-my >/dev/null 2>&1 || true
docker ps --format '{{.Names}} {{.Status}}'
free -m | sed -n '1,2p'

echo "=========== 0b) 准备 Spark $VER"
mkdir -p "$OSS"
if [ ! -d "$SPARK" ]; then
  [ -s "$TGZ" ] || curl -fL --retry 3 -o "$TGZ" "$MIRROR/spark-$VER-bin-hadoop3.tgz"
  ls -lh "$TGZ"
  tar -xzf "$TGZ" -C "$OSS"
fi
ls -d "$SPARK"
export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}
export SPARK_LOCAL_IP=127.0.0.1
"$SPARK/bin/spark-sql" --version 2>&1 | tail -2

echo "=========== 1) DDP 项目（kind=spark）"
rm -rf "$TT"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$SPARK" <<'PY'
import json, sys
tt, spark = sys.argv[1], sys.argv[2]
cfg = {
    "mode": "live",
    "platform": {
        "kind": "spark",
        "command": {
            "engine": "spark",
            "run": [f"{spark}/bin/spark-sql", "--master", "local[2]",
                    "--conf", "spark.ui.enabled=false",
                    "--conf", "spark.driver.memory=1g",
                    "--conf", f"spark.sql.warehouse.dir={tt}/warehouse",
                    "-f", "{file}"],
            "timeout": 900,
        },
    },
    "scheduler": {"kind": "cron"},
}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
# 不分区：先用最简形态验适配器与证据链（分区表写入语义另记边界）
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

echo "=========== 3) ddl render（spark 方言）"
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
INSERT INTO ods_orders_live VALUES
  (1, 10.50, DATE '2026-09-28'),
  (2, 20.25, DATE '2026-09-28'),
  (3, 30.00, DATE '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE | tail -8

echo "=========== 6) 读回（真实查询）"
"$SPARK/bin/spark-sql" --master "local[2]" --conf spark.ui.enabled=false \
  --conf "spark.sql.warehouse.dir=$TT/warehouse" \
  -e "SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM ods_orders_live" 2>/dev/null | grep -vE '^$|WARN|INFO' | tail -4

echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} engine={run.get('engine')} evidence_level={run.get('evidence_level')} sql_hash={str(run.get('sql_hash'))[:12]}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE

echo "=========== 8) 失败案例：普通 parquet 表上的 DELETE（方言规则说行级写只对湖表成立）"
cat > "$TT/delete.sql" <<'SQL'
-- table: ods_orders_live
-- source: ods_orders_raw
-- env: dev
DELETE FROM ods_orders_live WHERE order_id = 1;
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql --file "$TT/delete.sql" --env dev 2>&1 | grep -vE 'INFO|WARN|^$' | tail -5

echo "=========== 9) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" ods_orders_live || true

echo "=========== 10) 资源占用收尾（Spark 是本地进程，退出即释放）"
pgrep -af 'spark-sql|SparkSubmit' | head -3 || echo "no spark process left"
