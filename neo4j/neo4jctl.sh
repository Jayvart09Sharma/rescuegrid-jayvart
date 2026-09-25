#!/usr/bin/env bash
# RescueGrid SHARED Neo4j on the ZGX Nano - the single source of truth for the whole team.
#   ./neo4jctl.sh start|stop|restart|status|logs [n]|schema|seed|backup|cypher "<query>"
# Runs as systemd --user service 'rescuegrid-neo4j' (starts at boot).
# Bolt: bolt://127.0.0.1:7688   Browser: http://127.0.0.1:7475 (ssh -L 7475:127.0.0.1:7475 from a laptop)   user/pass: neo4j / rescuegrid
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export JAVA_HOME="$ROOT/jdk"; export NEO4J_HOME="$ROOT/neo4j"
BOLT="${NEO4J_URI:-bolt://127.0.0.1:7688}"; USER_="${NEO4J_USER:-neo4j}"; PASS="${NEO4J_PASSWORD:-rescuegrid}"

cypher() { "$NEO4J_HOME/bin/cypher-shell" -a "$BOLT" -u "$USER_" -p "$PASS" --format plain "$@"; }
wait_ready() {
  for i in $(seq 1 90); do
    if cypher "RETURN 1" >/dev/null 2>&1; then echo ">> Neo4j ready  $BOLT  http://127.0.0.1:7475  ($USER_/$PASS)"; return 0; fi
    sleep 2
  done
  echo "!! Neo4j not ready after 180 s - see: $0 logs"; return 1
}
# Managed by the systemd --user unit rescuegrid-neo4j (enabled; linger on => starts at boot, restarts on failure).
UNIT=rescuegrid-neo4j
start()   { systemctl --user start "$UNIT"; wait_ready; }
stop()    { systemctl --user stop "$UNIT"; echo ">> stopped"; }
restart() { systemctl --user restart "$UNIT"; wait_ready; }
status()  { systemctl --user --no-pager status "$UNIT" 2>&1 | sed -n '1,4p'; cypher "MATCH (n) RETURN count(n) AS nodes" 2>/dev/null | tail -1 | sed 's/^/   nodes: /' || true; }
logs()    { tail -n "${2:-50}" "$NEO4J_HOME/logs/neo4j.log"; }
schema()  { cypher -f "$ROOT/schema/schema.cypher" && echo ">> schema applied (constraints + indexes, idempotent)"; }
seed()    { cypher -f "$ROOT/schema/seed_static.cypher" && echo ">> static world seeded (idempotent: never overwrites live status)"; }
backup()  { local out="$ROOT/backups/neo4j-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$out"; stop; "$NEO4J_HOME/bin/neo4j-admin" database dump neo4j --to-path="$out" && echo ">> dump at $out"; start; }

case "${1:-}" in
  start) start;; stop) stop;; restart) restart;; status) status;; logs) logs "$@";;
  schema) schema;; seed) seed;; backup) backup;;
  cypher) shift; cypher "$@";;
  *) echo "usage: $0 {start|stop|restart|status|logs [n]|schema|seed|backup|cypher <query>}"; exit 1;;
esac
