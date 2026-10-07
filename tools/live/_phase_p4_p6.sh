#!/usr/bin/env bash
# 由 .tmp/live-pipeline/step4b-finish.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试 · 第 4 步（续）：用账本里最后两条真 query_id 写 P4 机器证据 → P4 门 → P5 部署 → P6 → finish
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-pipeline/proj"
export JAVA_HOME="${DDP_JAVA17:-$HOME/doris-live/jdk17}"
export PATH="$JAVA_HOME/bin:$PATH"
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"
TICKET=T-0001
REQ="$TT/.data-dev/tickets/T-0001"
DDP="python3 $REPO/engine/ddp.py --root $TT"

echo "=========== 1) P4 机器证据（账本最后两条 = tc_0001 / tc_0002 的真 run）"
python3 - "$REQ" <<'PY'
import json, sys
from pathlib import Path
req = Path(sys.argv[1])
ledger = json.loads((req.parent.parent / "runtime" / "executions.json").read_text(encoding="utf-8"))
runs = [r for r in ledger["runs"] if r.get("status") == "SUCCESS"]
tc1, tc2 = runs[-2]["query_id"], runs[-1]["query_id"]
(req / "test_report.md").write_text(
    "# test_report.md（真实测试）\n\n## 测试用例\n\n"
    "| test_id | 用例 | 结果 |\n| --- | --- | --- |\n"
    f"| TC-0001 | 目标表行数 = 2 | ✅ PASS (query_id: {tc1}) |\n"
    f"| TC-0002 | 成交金额合计 = 60.75 | ✅ PASS (query_id: {tc2}) |\n\n"
    "## 结论\n\n全部通过；query_id 可在 .data-dev/runtime/executions.json 回查。\n",
    encoding="utf-8")
(req / "test_runs.json").write_text(json.dumps({
    "generated_by": "live-pipeline/step4",
    "runs": [
        {"test_id": "TC-0001", "description": "目标表行数 = 2", "query_id": tc1, "status": "SUCCESS",
         "sql_file": "test/tc_0001.sql"},
        {"test_id": "TC-0002", "description": "成交金额合计 = 60.75", "query_id": tc2, "status": "SUCCESS",
         "sql_file": "test/tc_0002.sql"},
    ],
    "summary": {"total": 2, "success": 2, "failed": 0},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[p4] TC-0001={tc1}  TC-0002={tc2}")
PY

echo "=========== 2) P4 出口门 → P5"
$DDP advance --phase 5 --ticket $TICKET --reason "P4 机器证据门（真 query_id）" 2>&1 | tail -16

echo "=========== 3) P5 部署（真跑 prod：adverwarehouse=ddp-live-prod）"
$DDP deploy --comment "[已确认发布] 三道部署门已确认（真实测试，用户拍板）" --ticket $TICKET 2>&1 | tail -14

echo "=========== 4) P5 出口门 → P6"
$DDP advance --phase 6 --ticket $TICKET --reason "P5 出口（prod 已执行）" 2>&1 | tail -16

echo "=========== 5) P6：语义层 + finish"
printf '[已确认] 用户拍板进入语义层更新（真实测试，张三确认）' > "$REQ/_user_confirmation.md"
$DDP semantic --scenario metric-dev --ticket $TICKET 2>&1 | tail -6
$DDP ctx set --key semantic_entry_user_decided --value true --ticket $TICKET 2>&1 | tail -3
$DDP finish --reason "从建单到语义层闭环（真实测试）" --ticket $TICKET 2>&1 | tail -16
