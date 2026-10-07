#!/usr/bin/env bash
# 由 .tmp/live-pipeline/step1-flink.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试 · 第 1 步（不依赖 Docker）：live 项目 + JSON tracker CLI + 建单
# 平台用本地 Flink 1.20.3 + Paimon 1.4.1（已装好、已验证），避免 Docker 现在不稳的问题。
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-pipeline/proj"
FLINK_DIR="${FLINK_HOME:-$HOME/flink-live/flink-1.20.3}"
WARE_DEV=/tmp/ddp-live-dev
WARE_PROD=/tmp/ddp-live-prod
export JAVA_HOME="${DDP_JAVA17:-$HOME/doris-live/jdk17}"
export PATH="$JAVA_HOME/bin:$PATH"

mkdir -p "$WARE_DEV" "$WARE_PROD"
cat > /tmp/ddp-dev-init.sql <<SQL
SET 'execution.target' = 'local';
SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'TABLEAU';
CREATE CATALOG paimon WITH ('type' = 'paimon', 'warehouse' = 'file://$WARE_DEV');
USE CATALOG paimon;
CREATE DATABASE IF NOT EXISTS ddp_dev;
USE ddp_dev;
SQL
cat > /tmp/ddp-prod-init.sql <<SQL
SET 'execution.target' = 'local';
SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'TABLEAU';
CREATE CATALOG paimon WITH ('type' = 'paimon', 'warehouse' = 'file://$WARE_PROD');
USE CATALOG paimon;
CREATE DATABASE IF NOT EXISTS ddp_prod;
USE ddp_prod;
SQL

rm -rf "$TT"; mkdir -p "$TT"
python3 "$REPO/engine/ddp.py" --root "$TT" setup --mode live >/dev/null
python3 - "$TT" "$REPO" "$FLINK_DIR" <<'PY'
import json, sys
tt, repo, flink = sys.argv[1], sys.argv[2], sys.argv[3]

def runner(init):
    return ("bash", "-lc",
            f"{flink}/bin/sql-client.sh -i {init} -f {{file}} > /tmp/ddp-sql.log 2>&1; rc=$?; "
            "if grep -q '\\[ERROR\\]' /tmp/ddp-sql.log; then rc=1; fi; tail -60 /tmp/ddp-sql.log; exit $rc")

cfg = {
    "mode": "live",
    "cli": {"tracker": ["python3", f"{repo}/.tmp/live-pipeline/tracker.py"]},
    "platform": {
        "kind": "flink", "format": "paimon", "base_env": "dev",
        "database": "ddp_dev",
        "command": {"engine": "flink", "run": list(runner("/tmp/ddp-dev-init.sql")), "timeout": 900},
        "profiles": {"prod": {"database": "ddp_prod",
                              "command": {"engine": "flink", "run": list(runner("/tmp/ddp-prod-init.sql")),
                                          "timeout": 900}}},
    },
    "knowledge": {"uri_prefix": "viking://resources/", "kind": "custom-cli"},
    "limits": {"evidence_max_age_hours": 24},
    "scheduler": {"kind": "cron"},
}
open(f"{tt}/.data-dev/config.json", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
print("[setup] live 配置：tracker=JSON CLI 替身，platform=flink(+paimon)，dev/prod 两个 warehouse")
PY

echo "=========== 建单"
cat > /tmp/ddp-desc.md <<'MD'
## 背景
运营要看「每个用户当天的成交汇总」。现有订单明细表 ods_order_detail_di 每天有新增与变更，
报表侧需要一张按 ds 分区的用户粒度成交汇总表，供次日看板直接查询，避免每次都扫明细。

## 目标
产出 ads_user_act_di：粒度 = 用户 × 日期（ds 分区）；字段 user_id、order_cnt、pay_amt、ds。
目标表放在 ads 层，供看板直连；产出后由数据研发同学（负责人：张三）验收。

## 范围
本期只做这 1 张表及其调度：来源表 ods_order_detail_di，只统计 pay_status = 'PAID' 的订单，
金额用 decimal(18,2) 求和，按 ds 分区整表重写。不含实时链路、不含其它指标。

## 验收口径
1) DDL 与 ETL 都能在 dev 环境真跑通过（真实引擎执行，不是 mock）；
2) 读回行数与金额总量与手算一致（用例：TC-0001 行数、TC-0002 金额）；
3) P3 出口门 FAIL=0；4) 发布走 P5 的 [已确认发布] 门。

## 风险
- 明细表当天数据迟到会导致次日看板缺数：先按 T+1 口径交付，迟到补偿走回刷；
- pay_status 口径若上游调整，需要同步改这里的过滤条件。

计划日期：2026-10-07
MD
python3 "$REPO/engine/ddp.py" --root "$TT" ticket create \
  --title "【数据需求】订单成交汇总表 ads_user_act_di（真实测试）" \
  --description-file /tmp/ddp-desc.md 2>&1 | tail -16

echo "=========== 工单状态"
python3 "$REPO/engine/ddp.py" --root "$TT" ticket show 2>&1 | tail -10
