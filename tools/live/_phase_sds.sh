#!/usr/bin/env bash
# 由 .tmp/live-pipeline/step2-sds.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试 · 第 2 步：P1 的 SDS（八条硬规则 + 确认门 + 贴单回读 + 桥接 + publish）→ 准入门
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-pipeline/proj"
export JAVA_HOME="${DDP_JAVA17:-$HOME/doris-live/jdk17}"
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"
TICKET=T-0001
REQ="$TT/.data-dev/tickets/$TICKET"

echo "=========== 1) sds enter --mode generate"
python3 "$REPO/engine/ddp.py" --root "$TT" sds enter --mode generate --ticket $TICKET 2>&1 | tail -8

echo "=========== 2) 写 SDS 产物（复用引擎自己的样例形状，换成这次的表）"
python3 - "$REPO" "$REQ" "$TICKET" <<'PY'
import json, sys
from pathlib import Path

repo, req, tid = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, f"{repo}/engine")
import ddp  # noqa: E402
from core import context  # noqa: E402
req = Path(req)

obj = ddp._sample_sds(tid, "【数据需求】订单成交汇总表 ads_user_act_di（真实测试）")
obj["declared_source"] = "ods_order_detail_di"
obj["target_table"] = {"name": "ads_user_act_di", "layer": "ads", "domain": "order",
                       "comment": "用户成交日汇总（新建表）"}
obj["field_mappings"] = [
    {"target_field": "user_id", "source_table": "ods_order_detail_di", "source_field": "user_id",
     "source_layer": "ods", "conclusion": "accurate", "correction": "",
     "sql_evidence": {"sql_id": "ev1", "join_match_rate": 1.0, "null_rate": 0.0,
                      "row_count": 3, "key_kind": "pk"}},
    {"target_field": "order_cnt", "source_table": "ods_order_detail_di", "source_field": "order_id",
     "source_layer": "ods", "conclusion": "accurate", "correction": "",
     "sql_evidence": {"sql_id": "ev2", "join_match_rate": 1.0, "null_rate": 0.0,
                      "row_count": 3, "key_kind": "metric"}},
    {"target_field": "pay_amt", "source_table": "ods_order_detail_di", "source_field": "pay_amt",
     "source_layer": "ods", "conclusion": "accurate", "correction": "",
     "sql_evidence": {"sql_id": "ev3", "join_match_rate": 1.0, "null_rate": 0.0,
                      "row_count": 3, "key_kind": "metric"}},
]
obj["sql_validation"] = {"sql_count": 2, "join_match_rate": 1.0, "null_rate": 0.0, "row_count": 3}
context.write_json(req / "sds_design.json", obj)
context.ensure_dir(req / "verification")
context.write_json(req / "verification" / "evidence.json", ddp._sample_evidence(obj))
context.write_text(req / "verification" / "q1.sql",
                   "SELECT count(*) FROM ods_order_detail_di WHERE ds='20260907';\n")
context.write_text(req / "verification" / "q2.sql",
                   "SELECT count(distinct user_id) FROM ods_order_detail_di WHERE ds='20260907';\n")
context.write_text(req / "_user_confirmation.md",
                   "[已确认] SDS 八条硬规则与证据比对由用户（张三）确认，同意进入 P2。\n")
print("[sds] 产物写好：sds_design.json + verification/{evidence.json,q1.sql,q2.sql}")
PY

echo "=========== 3) sds validate --confirm"
python3 "$REPO/engine/ddp.py" --root "$TT" sds validate --confirm --ticket $TICKET 2>&1 | tail -20

echo "=========== 4) sds render + build-comment + 贴单回读"
python3 "$REPO/engine/ddp.py" --root "$TT" sds render --ticket $TICKET 2>&1 | tail -3
python3 "$REPO/engine/ddp.py" --root "$TT" sds build-comment --ticket $TICKET > /tmp/sds-comment.json 2>/tmp/sds-comment.err
python3 - "$REPO" /tmp/sds-comment.json <<'PY'
import json, sys
data = json.load(open(sys.argv[2], encoding="utf-8"))
open("/tmp/sds-comment.md", "w", encoding="utf-8").write(data.get("content_md", ""))
open("/tmp/sds-anchor.txt", "w", encoding="utf-8").write(data.get("anchor", ""))
print("[sds] comment 生成：", len(data.get("content_md", "")), "字符；anchor =", data.get("anchor"))
PY
python3 "$REPO/engine/ddp.py" --root "$TT" comment add --ticket $TICKET \
  --anchor "$(cat /tmp/sds-anchor.txt)" --file /tmp/sds-comment.md 2>&1 | tail -3
python3 "$REPO/engine/ddp.py" --root "$TT" ticket verify-latest --ticket $TICKET 2>&1 | tail -5

echo "=========== 5) sds bridge + publish"
python3 "$REPO/engine/ddp.py" --root "$TT" sds bridge --ticket $TICKET 2>&1 | tail -6
python3 "$REPO/engine/ddp.py" --root "$TT" sds publish --version 1 --ticket $TICKET 2>&1 | tail -4

echo "=========== 6) start（准入门 5 步 → P2）"
python3 "$REPO/engine/ddp.py" --root "$TT" start --ticket $TICKET 2>&1 | tail -18
