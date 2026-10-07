#!/usr/bin/env bash
# 由 .tmp/live-common/p3_gate.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 通用：在某个真跑项目上补齐 P3 输入并跑一次真实 P3 出口门
# 用法：bash p3_gate.sh <项目根> <表名> [ticket] [dp_task SQL 文件（可选，覆盖默认 INSERT）]
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="$1"
TABLE="$2"
TICKET="${3:-T-LIVE}"
INSERT_FILE="${4:-}"
REQ="$TT/.data-dev/tickets/$TICKET"
STEM="${TABLE##*.}"          # 目录/文件按表名末段命名（P3 门比的是文件名与交付表清单）

# Spark 的默认 catalog 用「当前目录」下的 Derby metastore：多个真跑项目共用同一个 cwd
# 会互相污染（CREATE TABLE IF NOT EXISTS 变 no-op、INSERT 追到别人的表里）。
# 所以 Spark 类引擎由调用方通过 P3_CWD 指定一个隔离目录（Linux 文件系统上，drvfs 上 Derby 会起不来）。
cd "${P3_CWD:-$TT}"

mkdir -p "$REQ/dp_task"

if [ -n "$INSERT_FILE" ] && [ -f "$INSERT_FILE" ]; then
  cp "$INSERT_FILE" "$REQ/dp_task/$STEM.sql"
else
  cat > "$REQ/dp_task/$STEM.sql" <<SQL
-- table: $TABLE
-- source: ${TABLE}_raw
-- schedule: 0 2 * * *
-- env: dev
INSERT INTO $TABLE (order_id, amt, ds) VALUES
  (4, 40.00, DATE '2026-09-29'),
  (5, 50.00, DATE '2026-09-29');
SQL
fi
python3 "$REPO/engine/ddp.py" --root "$TT" platform run-sql \
    --file "$REQ/dp_task/$STEM.sql" --env dev --save-evidence --ticket "$TICKET" 2>&1 | tail -4

cat > "$REQ/architecture_doc.md" <<MD
# 架构设计（真实引擎联调样例）

## 交付表

\`\`\`json
{"tables": [{"name": "$STEM", "action": "create", "layer": "ods"}]}
\`\`\`
MD

python3 - "$REQ" "$STEM" <<'PY'
import json, sys
req, table = sys.argv[1], sys.argv[2]
open(f"{req}/sds_design.json", "w", encoding="utf-8").write(json.dumps({
    "ticket_id": "T-LIVE", "title": "真实引擎联调样例", "mode": "generate",
    "level_suggestion": "L3", "confirmed": True,
    "target_table": {"name": table, "layer": "ods", "domain": "order"},
    "field_mappings": [],
}, ensure_ascii=False, indent=2))
print("sds_design.json written")
PY

printf '# 用户确认\n[已确认] %s 真实引擎联调样例，用户已核对交付物\n' "$TICKET" > "$REQ/_user_confirmation.md"
python3 "$REPO/engine/ddp.py" --root "$TT" evidence selfcheck --ticket "$TICKET" >/dev/null
python3 "$REPO/engine/ddp.py" --root "$TT" evidence confirm --ticket "$TICKET" >/dev/null
python3 "$REPO/engine/ddp.py" --root "$TT" evidence verify --ticket "$TICKET"

python3 - "$REPO" "$TT" "$TICKET" <<'PY'
import sys
repo, tt, ticket = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, f"{repo}/engine")
import ddp  # noqa: E402
from pathlib import Path  # noqa: E402
from core import context, validators  # noqa: E402
from core.state import Pipeline  # noqa: E402

req = context.ticket_dir(Path(tt), ticket)
pipeline = Pipeline(req)
enriched = dict(pipeline.state)
ddp.enrich_state(enriched)
issues = validators.stage_gate(3, req, enriched, "viking://resources/")
blockers = [i for i in issues if i.severity in ("FAIL", "HALTED")]
for issue in issues:
    print(f"[{issue.severity}] {issue.code}: {issue.message}")
print(f"FAIL={len(blockers)}  WARN={len(issues) - len(blockers)}")
raise SystemExit(1 if blockers else 0)
PY
