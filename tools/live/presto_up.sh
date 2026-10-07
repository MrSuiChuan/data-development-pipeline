#!/usr/bin/env bash
# 由 .tmp/live-close/presto-up.sh 平移入库（只改了路径变量；逻辑与当时真跑一致）。
# Presto 0.299 单机 coordinator（memory connector），Java 17
set -uo pipefail
export JAVA_HOME="${DDP_JAVA17:-$HOME/doris-live/jdk17}"
export PATH="$JAVA_HOME/bin:$PATH"
BASE="${PRESTO_HOME:-$HOME/presto-live}"
TAR=/tmp/presto-server-0.299.tar.gz
SRV="$BASE/presto-server-0.299"

mkdir -p "$BASE"
if [ ! -d "$SRV" ]; then
  echo "== 解包 Presto（746 MB，慢工）"
  tar xzf "$TAR" -C "$BASE"
fi
mkdir -p "$SRV/etc/catalog" "$SRV/data"

cat > "$SRV/etc/node.properties" <<EOF
node.environment=ddp
node.id=ddp-presto-1
node.data-dir=$SRV/data
EOF
cat > "$SRV/etc/jvm.config" <<'EOF'
-server
-Xmx2G
-XX:+UseG1GC
-XX:G1HeapRegionSize=32M
-XX:+ExplicitGCInvokesConcurrent
-XX:+HeapDumpOnOutOfMemoryError
-XX:+UseNUMA
-Djdk.attach.allowAttachSelf=true
EOF
cat > "$SRV/etc/config.properties" <<'EOF'
coordinator=true
node-scheduler.include-coordinator=true
http-server.http.port=18080
query.max-memory=1GB
query.max-memory-per-node=512MB
discovery-server.enabled=true
discovery.uri=http://127.0.0.1:18080
EOF
cat > "$SRV/etc/catalog/memory.properties" <<'EOF'
connector.name=memory
EOF
printf 'com.facebook.presto=INFO\n' > "$SRV/etc/log.properties"

echo "== 启动（最多等 180 秒）"
"$SRV/bin/launcher" start
for i in $(seq 1 60); do
  if curl -s -m 3 http://127.0.0.1:18080/v1/info >/dev/null 2>&1; then echo "  Presto ready after ~$((i*3))s"; break; fi
  sleep 3
done
curl -s -m 5 http://127.0.0.1:18080/v1/info 2>/dev/null | head -c 300; echo
tail -3 "$SRV/data/var/log/server.log" 2>/dev/null | cut -c1-160
