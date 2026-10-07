#!/usr/bin/env bash
# 由 .tmp/live-lakehouse/run.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# LIVE-005：湖表格式逐个真跑（Spark 4.1.3 宿主，一次只跑一个格式）
# 用法：bash run.sh <iceberg|delta|hudi|paimon>
set -u
FMT="${1:?用法: run.sh <iceberg|delta|hudi|paimon>}"
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SPARK="$HOME/oss/spark-4.1.3-bin-hadoop3"
JARS="$HOME/oss/jars"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-lakehouse/proj-$FMT"
M=https://maven.aliyun.com/repository/public
# Spark 的 Derby metastore 落在 cwd：每个格式一个独立的 Linux 侧工作目录，
# 既避免格式之间互相污染，也避免在 drvfs（/mnt/c）上起 Derby（锁文件不可靠）。
LAB="$HOME/oss/lab/$FMT"

export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}
export SPARK_LOCAL_IP=127.0.0.1

case "$FMT" in
  iceberg) JAR_NAME=iceberg-spark-runtime-4.1_2.13-1.11.0.jar ;;
  delta)   JAR_NAME=delta-spark_4.1_2.13-4.4.0.jar ;;
  hudi)    JAR_NAME=hudi-spark4.1-bundle_2.13-1.2.0.jar ;;
  paimon)  JAR_NAME=paimon-spark-4.1_2.13-2.0.0.jar ;;
  *) echo "未知格式: $FMT"; exit 2 ;;
esac

echo "=========== 0) 资源纪律：跑 Spark 前停掉所有组件容器"
docker stop ddp-hive ddp-ch ddp-trino ddp-pg ddp-my >/dev/null 2>&1 || true
docker ps --format '{{.Names}} {{.Status}}'
free -m | sed -n '1,2p'

echo "=========== 0b) 准备 $FMT 构件"
mkdir -p "$JARS"
if [ ! -s "$JARS/$JAR_NAME" ]; then
  case "$FMT" in
    iceberg) URL="$M/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/1.11.0/$JAR_NAME" ;;
    delta)   URL="$M/io/delta/delta-spark_4.1_2.13/4.4.0/$JAR_NAME" ;;
    hudi)    URL="$M/org/apache/hudi/hudi-spark4.1-bundle_2.13/1.2.0/$JAR_NAME" ;;
    paimon)  URL="$M/org/apache/paimon/paimon-spark-4.1_2.13/2.0.0/$JAR_NAME" ;;
  esac
  curl -fL --retry 3 -o "$JARS/$JAR_NAME" "$URL"
fi
ls -lh "$JARS/$JAR_NAME"

echo "=========== 1) DDP 项目（kind=spark + format=$FMT）"
rm -rf "$TT" "$LAB"
mkdir -p "$TT/.data-dev/tickets/T-LIVE/ddl" "$TT/.data-dev/tickets/T-LIVE/etl" "$TT/.data-dev/tickets/T-LIVE/dp_task" "$LAB"
export P3_CWD="$LAB"          # p3_gate 里的 Spark 也在隔离目录里跑
cd "$LAB"                     # Derby metastore / warehouse 都落在 Linux 侧，且按格式隔离
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$SPARK" "$JARS/$JAR_NAME" "$FMT" "$LAB" <<'PY'
import json, sys
tt, spark, jar, fmt, lab = sys.argv[1:6]
base = [f"{spark}/bin/spark-sql", "--master", "local[2]",
        "--conf", "spark.ui.enabled=false",
        "--conf", f"spark.sql.warehouse.dir={lab}/warehouse",
        "--conf", "spark.driver.memory=1g"]
if fmt == "delta":
    # Delta 4.x 把 delta-storage 等拆成独立构件：单个 --jars 会 NoClassDefFoundError
    # （io/delta/storage/commit/actions/AbstractProtocol）。用 --packages 让 Spark 解析传递依赖。
    base += ["--packages", "io.delta:delta-spark_4.1_2.13:4.4.0",
             "--repositories", "https://maven.aliyun.com/repository/public",
             "--conf", f"spark.jars.ivy={lab}/ivy"]
elif fmt == "paimon":
    # Paimon 的 spark 构件同样有传递依赖（paimon-common / paimon-spark-common 等）：
    # 只挂一个 jar 会在 Spark 启动阶段就失败（实测 SparkSubmit 直接报错）。
    base += ["--packages", "org.apache.paimon:paimon-spark-4.1_2.13:2.0.0",
             "--repositories", "https://maven.aliyun.com/repository/public",
             "--conf", f"spark.jars.ivy={lab}/ivy"]
else:
    base += ["--jars", jar]
if fmt == "iceberg":
    table = "lake.default.ods_orders_live"
    base += ["--conf", "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
             "--conf", "spark.sql.catalog.lake=org.apache.iceberg.spark.SparkCatalog",
             "--conf", "spark.sql.catalog.lake.type=hadoop",
             "--conf", f"spark.sql.catalog.lake.warehouse={lab}/warehouse/iceberg"]
elif fmt == "delta":
    table = "default.ods_orders_live"
    base += ["--conf", "spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension",
             "--conf", "spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog"]
elif fmt == "hudi":
    table = "default.ods_orders_live"
    # Hudi 1.2 的 Spark 集成要 Kryo：少了它在 INSERT 阶段直接 SparkSubmit 级失败（实测）
    base += ["--conf", "spark.serializer=org.apache.spark.serializer.KryoSerializer",
             "--conf", "spark.sql.extensions=org.apache.spark.sql.hudi.HoodieSparkSessionExtension",
             "--conf", "spark.sql.catalog.spark_catalog=org.apache.spark.sql.hudi.catalog.HoodieCatalog"]
else:
    table = "paimon.db.ods_orders_live"
    base += ["--conf", "spark.sql.catalog.paimon=org.apache.paimon.spark.SparkCatalog",
             # Paimon 自己会检查这个扩展；少了它 catalog 初始化直接抛 RuntimeException（实测）
             "--conf", "spark.sql.extensions=org.apache.paimon.spark.extensions.PaimonSparkSessionExtensions",
             "--conf", f"spark.sql.catalog.paimon.warehouse={lab}/warehouse/paimon"]
base += ["-f", "{file}"]
cfg = {"mode": "live",
       "platform": {"kind": "spark", "format": fmt, "command": {"engine": "spark", "run": base, "timeout": 1800}},
       "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
props = {"primaryKey": "order_id", "preCombineField": "amt", "type": "COW"} if fmt == "hudi" else (
    {"primary-key": "order_id", "bucket": "1"} if fmt == "paimon" else {})
spec = {"table": table,
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": ["order_id"], "properties": props, "format": fmt}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
print(f"[setup] {fmt} config + spec written, table={table}")
PY

echo "=========== 2) platform doctor"
python3 "$REPO/engine/ddp.py" --root "$TT" platform doctor | head -20

echo "=========== 3) ddl render"
python3 "$REPO/engine/ddp.py" --root "$TT" ddl render --spec "$TT/spec.json" \
    --out "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >/dev/null
cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"

TABLE_NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['table'])" "$TT/spec.json")
case "$FMT" in
  iceberg) DDL_PREFIX="CREATE DATABASE IF NOT EXISTS lake.default;" ;;
  paimon)  DDL_PREFIX="CREATE DATABASE IF NOT EXISTS paimon.db;" ;;
  *)       DDL_PREFIX="" ;;
esac
if [ -n "$DDL_PREFIX" ]; then
  printf '%s\n' "$DDL_PREFIX" > "$TT/.data-dev/tickets/T-LIVE/ddl/_prefix.sql"
  cat "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" >> "$TT/.data-dev/tickets/T-LIVE/ddl/_prefix.sql"
  mv "$TT/.data-dev/tickets/T-LIVE/ddl/_prefix.sql" "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"
fi

echo "=========== 4) 真跑建表"
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -6

echo "=========== 5) 真跑写入"
cat > "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" <<SQL
-- table: $TABLE_NAME
-- source: ods_orders_raw
-- env: dev
INSERT INTO $TABLE_NAME (order_id, amt, ds) VALUES
  (1, 10.50, DATE '2026-09-28'),
  (2, 20.25, DATE '2026-09-28'),
  (3, 30.00, DATE '2026-09-28');
SQL
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$TT/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -6

echo "=========== 6) 读回（真实查询）"
python3 - "$TT" "$SPARK" "$JARS/$JAR_NAME" "$FMT" "$TABLE_NAME" <<'PY' > "$TT/readback.sql"
import sys
tt, spark, jar, fmt, table = sys.argv[1:6]
print(f"SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM {table};")
PY
python3 - "$TT" "$SPARK" "$JARS/$JAR_NAME" "$FMT" "$TABLE_NAME" <<'PY'
import json, subprocess, sys
tt, spark, jar, fmt, table = sys.argv[1:6]
cfg = json.load(open(f"{tt}/.data-dev/config.json", encoding="utf-8"))
argv = list(cfg["platform"]["command"]["run"])
argv[argv.index("-f") + 1] = f"{tt}/readback.sql"
proc = subprocess.run(argv, capture_output=True, text=True)
lines = [l for l in (proc.stdout or "").splitlines() if l.strip() and not l.startswith(("25/", "26/", "WARN", "INFO", "SLF4J"))]
print("\n".join(lines[-4:]))
print("rc =", proc.returncode)
PY

echo "=========== 7) 账本 + verify"
python3 - "$TT" <<'PY'
import json, sys
tt = sys.argv[1]
data = json.load(open(f"{tt}/.data-dev/runtime/executions.json", encoding="utf-8"))
for run in data.get("runs", []):
    print(f"  query_id={run.get('query_id')} engine={run.get('engine')} level={run.get('evidence_level')} sql_hash={str(run.get('sql_hash'))[:12]} status={run.get('status')}")
PY
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket T-LIVE 2>&1 | tail -5

echo "=========== 8) 真实 P3 出口门"
bash ""$REPO/tools/live/p3_gate.sh"" "$TT" "$TABLE_NAME" || true

echo "=========== 9) 该格式结束（Spark 进程随命令退出释放）"
