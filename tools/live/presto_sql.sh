#!/usr/bin/env bash
# 由 .tmp/live-close/presto-sql.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# 用 Presto 的 HTTP statement API 执行一个 SQL 文件（PrestoDB 没在 Maven Central 发布可执行 CLI，
# 所以这里直接对着真协议的 /v1/statement 说话；出错返回非零，和 CLI 一样）。
# 用法： presto-sql.sh <server> <catalog> <schema> <file.sql>
set -uo pipefail
SERVER="$1"; CATALOG="$2"; SCHEMA="$3"; FILE="$4"

# Presto 的 /v1/statement 不吃尾部加分号（官方 CLI 会替你剥掉，裸 HTTP 客户端要自己剥）：
# 实测 `SELECT 1;` → mismatched input ';'，`SELECT 1` → 正常。
sql=$(python3 - "$FILE" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read().strip()
print(re.sub(r";\s*$", "", text))
PY
)
url="$SERVER/v1/statement"
headers=(-H "X-Presto-User: ddp" -H "X-Presto-Catalog: $CATALOG" -H "X-Presto-Schema: $SCHEMA")

resp=$(curl -sS -m 60 "${headers[@]}" --data-binary "$sql" "$url")
for _ in $(seq 1 60); do
  if printf '%s' "$resp" | grep -q '"error"'; then
    printf '%s\n' "$resp" | python3 -c 'import json,sys; d=json.load(sys.stdin); e=d["error"]; print("ERROR:", e.get("message"), "|", (e.get("failureInfo") or {}).get("type"))'
    exit 1
  fi
  data=$(printf '%s' "$resp" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("data") or []))')
  if [ "$data" != "[]" ]; then
    printf '%s' "$resp" | python3 -c '
import json, sys
d = json.load(sys.stdin)
cols = [c["name"] for c in d.get("columns", [])]
rows = d.get("data") or []
if cols: print(" | ".join(cols))
for r in rows: print(" | ".join(str(v) for v in r))
'
  fi
  nxt=$(printf '%s' "$resp" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("nextUri") or ""))')
  [ -z "$nxt" ] && break
  resp=$(curl -sS -m 60 "${headers[@]}" "$nxt")
done
exit 0
