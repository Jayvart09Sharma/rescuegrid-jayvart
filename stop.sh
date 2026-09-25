#!/usr/bin/env bash
# Stop the RescueGrid services started by start.sh (Neo4j too unless --keep-db). ZRT models are left alone.
R="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for p in 8097 8096 8095 8091 8090; do
  pid=$(ss -ltnp 2>/dev/null | grep ":$p " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  [ -n "$pid" ] && { kill "$pid" && echo "   stopped :$p (pid $pid)"; }
done
[ "${1:-}" = "--keep-db" ] || "$R/backend/neo4j/neo4jctl.sh" stop
