#!/usr/bin/env bash
# Self-contained Neo4j 5.26 Community for this project (no Docker, no sudo, no system Java).
# Installs JDK 21 + Neo4j into ./.local, data stays under ./.local/neo4j/data.
#   scripts/neo4j_local.sh install | start | stop | status | logs | cypher "<query>" | reset
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL="$ROOT/.local"
NEO4J_VERSION="${NEO4J_VERSION:-5.26.0}"
NEO4J_HOME="$LOCAL/neo4j"
JDK_HOME="$LOCAL/jdk"
PASSWORD="${NEO4J_PASSWORD:-rescuegrid}"
ARCH="$(uname -m)"; case "$ARCH" in aarch64|arm64) JARCH=aarch64;; x86_64) JARCH=x64;; *) echo "unsupported arch $ARCH"; exit 1;; esac

dedupe_conf() {  # stock neo4j.conf already declares some keys we override; keep only ours active
  local c="$NEO4J_HOME/conf/neo4j.conf"
  for k in server.default_listen_address server.bolt.listen_address server.http.listen_address server.https.enabled server.memory.heap.initial_size server.memory.heap.max_size server.memory.pagecache.size dbms.usage_report.enabled; do
    awk -v k="$k" 'BEGIN{seen=0} { if (index($0,k"=")==1) { if (seen) next; seen=1 } print }' "$c" > "$c.tmp" && mv "$c.tmp" "$c"
  done
}
install() {
  mkdir -p "$LOCAL"
  if [ ! -x "$JDK_HOME/bin/java" ]; then
    echo ">> downloading Temurin JDK 21 ($JARCH)"
    curl -fL --progress-bar "https://api.adoptium.net/v3/binary/latest/21/ga/linux/$JARCH/jdk/hotspot/normal/eclipse?project=jdk" -o "$LOCAL/jdk.tar.gz"
    mkdir -p "$JDK_HOME" && tar -xzf "$LOCAL/jdk.tar.gz" -C "$JDK_HOME" --strip-components=1 && rm "$LOCAL/jdk.tar.gz"
  fi
  if [ ! -x "$NEO4J_HOME/bin/neo4j" ]; then
    echo ">> downloading Neo4j Community $NEO4J_VERSION"
    curl -fL --progress-bar "https://dist.neo4j.org/neo4j-community-$NEO4J_VERSION-unix.tar.gz" -o "$LOCAL/neo4j.tar.gz"
    mkdir -p "$NEO4J_HOME" && tar -xzf "$LOCAL/neo4j.tar.gz" -C "$NEO4J_HOME" --strip-components=1 && rm "$LOCAL/neo4j.tar.gz"
    cat >> "$NEO4J_HOME/conf/neo4j.conf" <<CONF

# --- RescueGrid local overrides ---
server.default_listen_address=127.0.0.1
server.bolt.listen_address=:7687
server.http.listen_address=:7474
server.https.enabled=false
server.memory.heap.initial_size=512m
server.memory.heap.max_size=1g
server.memory.pagecache.size=256m
dbms.usage_report.enabled=false
CONF
    dedupe_conf; JAVA_HOME="$JDK_HOME" "$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password "$PASSWORD"
  fi
  echo ">> installed: $("$JDK_HOME/bin/java" -version 2>&1 | head -1) ; Neo4j $NEO4J_VERSION at $NEO4J_HOME"
}
start()  { JAVA_HOME="$JDK_HOME" "$NEO4J_HOME/bin/neo4j" start; wait_ready; }
stop()   { JAVA_HOME="$JDK_HOME" "$NEO4J_HOME/bin/neo4j" stop; }
status() { JAVA_HOME="$JDK_HOME" "$NEO4J_HOME/bin/neo4j" status || true; }
logs()   { tail -n 50 "$NEO4J_HOME/logs/neo4j.log"; }
wait_ready() {
  for i in $(seq 1 60); do
    if curl -s -m 2 http://127.0.0.1:7474 >/dev/null 2>&1; then echo ">> Neo4j ready: http://127.0.0.1:7474 (bolt://127.0.0.1:7687, neo4j/$PASSWORD)"; return 0; fi
    sleep 2
  done
  echo "!! Neo4j did not become ready in 120 s; see: $0 logs"; return 1
}
cypher() { JAVA_HOME="$JDK_HOME" "$NEO4J_HOME/bin/cypher-shell" -a bolt://127.0.0.1:7687 -u neo4j -p "$PASSWORD" "$@"; }
reset()  { stop || true; rm -rf "$NEO4J_HOME/data/databases" "$NEO4J_HOME/data/transactions"; JAVA_HOME="$JDK_HOME" "$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password "$PASSWORD"; start; }

case "${1:-}" in
  install) install;; start) start;; stop) stop;; status) status;; logs) logs;; reset) reset;;
  cypher) shift; cypher "$@";;
  *) echo "usage: $0 {install|start|stop|status|logs|reset|cypher <query>}"; exit 1;;
esac
