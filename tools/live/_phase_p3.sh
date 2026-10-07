#!/usr/bin/env bash
# 由 .tmp/live-pipeline/step3c-p3.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 真实测试 · 第 3 步（续）：P3 真跑。
# 关键修正：上游 fixture 表**不归本工单交付**，用直接 sql-client 建（插件会拦"写了但 ddl/ 里没有"——这是对的）。
set -u
REPO="${DDP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TT="${DDP_LIVE_WORK:-$REPO/.tmp/live}/live-pipeline/proj"
export JAVA_HOME="${DDP_JAVA17:-$HOME/doris-live/jdk17}"
export PATH="$JAVA_HOME/bin:$PATH"
export PYTHONPATH="${DDP_PYLIB:-$HOME/doris-live/pylibs}${PYTHONPATH:+:$PYTHONPATH}"
TICKET=T-0001
REQ="$TT/.data-dev/tickets/T-0001"
FLINK_DIR="${FLINK_HOME:-$HOME/flink-live/flink-1.20.3}"
DDP="python3 $REPO/engine/ddp.py --root $TT"

echo "=========== 1) 上游 fixture（直接 sql-client，不经插件：它不是本工单的交付物）"
cat > /tmp/fixture.sql <<'SQL'
CREATE TABLE IF NOT EXISTS ods_order_detail_di (
  order_id BIGINT, user_id STRING, pay_amt DECIMAL(18,2), pay_status STRING, ds DATE
);
INSERT INTO ods_order_detail_di VALUES
  (1, 'u1', 10.50, 'PAID',     DATE '2026-10-06'),
  (2, 'u1', 20.25, 'PAID',     DATE '2026-10-06'),
  (3, 'u2', 30.00, 'PAID',     DATE '2026-10-06'),
  (4, 'u2', 99.00, 'REFUNDED', DATE '2026-10-06');
SELECT count(*) AS src_rows FROM ods_order_detail_di;
SQL
( cd "$FLINK_DIR" && ./bin/sql-client.sh -i /tmp/ddp-dev-init.sql -f /tmp/fixture.sql 2>&1 | grep -E 'src_rows|^\|' | tail -3 )

echo "=========== 2) 交付物：ddl / etl / dp_task 三层同名同内容（dp_task 带 schedule 注解）"
mkdir -p "$REQ/ddl" "$REQ/etl" "$REQ/dp_task"
cat > "$REQ/etl/ads_user_act_di.sql" <<'SQL'
-- table: ads_user_act_di
-- source: ods_order_detail_di
-- schedule: 0 2 * * *
-- env: dev
INSERT INTO ads_user_act_di
SELECT user_id, count(*) AS order_cnt, sum(pay_amt) AS pay_amt, ds
FROM ods_order_detail_di
WHERE pay_status = 'PAID'
GROUP BY user_id, ds;
SQL
cp "$REQ/etl/ads_user_act_di.sql" "$REQ/dp_task/ads_user_act_di.sql"
ls -1 "$REQ/ddl" "$REQ/etl" "$REQ/dp_task"

echo "=========== 3) 真跑建目标表"
$DDP platform run-sql --file "$REQ/ddl/ads_user_act_di.sql" --env dev --save-evidence --ticket $TICKET 2>&1 | tail -3
echo "=========== 4) 真跑 ETL（聚合）"
$DDP platform run-sql --file "$REQ/etl/ads_user_act_di.sql" --env dev --save-evidence --ticket $TICKET 2>&1 | tail -3

echo "=========== 5) 读回（直接 sql-client，避免把 SELECT 写进执行账本干扰绑定校验）"
cat > /tmp/readback2.sql <<'SQL'
SELECT count(*) AS rows_inserted, sum(pay_amt) AS amt_sum FROM ads_user_act_di;
SELECT user_id, order_cnt, pay_amt FROM ads_user_act_di ORDER BY user_id;
SQL
( cd "$FLINK_DIR" && ./bin/sql-client.sh -i /tmp/ddp-dev-init.sql -f /tmp/readback2.sql 2>&1 | grep -E '^\|' | tail -8 )

echo "=========== 6) 证据 selfcheck + confirm + verify"
$DDP evidence selfcheck --ticket $TICKET 2>&1 | tail -3
$DDP evidence confirm --ticket $TICKET 2>&1 | tail -3
$DDP evidence verify --ticket $TICKET 2>&1 | tail -4

echo "=========== 7) P3 出口门 → P4"
$DDP advance --phase 4 --ticket $TICKET --reason "P3 出口（真实引擎执行证据）" 2>&1 | tail -20
