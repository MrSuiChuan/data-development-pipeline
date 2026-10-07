#!/usr/bin/env bash
# tools/live 公共约定：仓库根、工作目录、依赖检查。
# 用法： source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
set -uo pipefail

REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
WORK="${DDP_LIVE_WORK:-$REPO/.tmp/live}"
mkdir -p "$WORK"
DDP="python3 $REPO/engine/ddp.py"
P3_GATE="$REPO/tools/live/p3_gate.sh"

need() {  # need <命令> [提示]
  command -v "$1" >/dev/null 2>&1 || { echo "缺依赖：$1 ${2:-}" >&2; exit 2; }
}

project_dir() {  # project_dir <engine> → 打印工作目录并建好 ticket 骨架
  local dir="$WORK/$1/proj"
  rm -rf "$dir"
  mkdir -p "$dir/.data-dev/tickets/T-LIVE/ddl" "$dir/.data-dev/tickets/T-LIVE/etl" "$dir/.data-dev/tickets/T-LIVE/dp_task"
  echo "$dir"
}

write_config() {  # write_config <项目目录> <platform.json 片段>
  python3 - "$1" "$2" <<'PY'
import json, sys
tt, platform = sys.argv[1], json.loads(sys.argv[2])
cfg = {"mode": "live", "platform": platform, "scheduler": {"kind": "cron"}}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
PY
}

write_spec() {  # write_spec <项目目录> [primary_key json] [properties json]
  python3 - "$1" "${2:-[]}" "${3:-{}}" <<'PY'
import json, sys
tt, pk, props = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
spec = {"table": "ods_orders_live",
        "columns": [{"name": "order_id", "type": "bigint", "comment": "订单号"},
                    {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
                    {"name": "ds", "type": "date"}],
        "partition_by": [], "primary_key": pk, "properties": props}
open(f"{tt}/spec.json", "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False, indent=2))
PY
}

seed_etl() {  # seed_etl <项目目录> <可选 schedule 注解>
  local dir="$1" schedule="${2:-}"
  {
    echo "-- table: ods_orders_live"
    echo "-- source: ods_orders_raw"
    [ -n "$schedule" ] && echo "-- schedule: $schedule"
    echo "-- env: dev"
    cat <<'SQL'
INSERT INTO ods_orders_live (order_id, amt, ds) VALUES
  (1, 10.50, '2026-09-28'),
  (2, 20.25, '2026-09-28'),
  (3, 30.00, '2026-09-28');
SQL
  } > "$dir/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql"
  cp "$dir/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" "$dir/.data-dev/tickets/T-LIVE/dp_task/ods_orders_live.sql"
}

run_chain() {  # run_chain <项目目录> → doctor → ddl render → 建表 → 写入 → 账本 → verify → P3 门
  local dir="$1"
  $DDP --root "$dir" setup --mode live >/dev/null
  echo "=========== doctor"
  $DDP --root "$dir" platform doctor 2>&1 | tail -8
  echo "=========== ddl render"
  $DDP --root "$dir" ddl render --spec "$dir/spec.json" \
      --out "$dir/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" 2>&1 | tail -3
  cat "$dir/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql"
  echo "=========== 建表"
  $DDP --root "$dir" platform run-sql --file "$dir/.data-dev/tickets/T-LIVE/ddl/ods_orders_live.sql" \
      --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
  echo "=========== 写入"
  $DDP --root "$dir" platform run-sql --file "$dir/.data-dev/tickets/T-LIVE/etl/ods_orders_live.sql" \
      --env dev --save-evidence --ticket T-LIVE 2>&1 | tail -4
  echo "=========== 账本 + verify"
  python3 - "$dir" <<'PY'
import json, sys
data = json.load(open(f"{sys.argv[1]}/.data-dev/runtime/executions.json", encoding="utf-8"))
for r in data.get("runs", []):
    print(f"  query_id={r.get('query_id')} engine={r.get('engine')} level={r.get('evidence_level')}")
PY
  $DDP --root "$dir" evidence verify --ticket T-LIVE 2>&1 | tail -4
  echo "=========== 真实 P3 出口门（期望 FAIL=0）"
  bash "$P3_GATE" "$dir" ods_orders_live T-LIVE || true
}
