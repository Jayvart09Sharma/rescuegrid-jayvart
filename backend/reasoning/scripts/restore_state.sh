#!/usr/bin/env bash
# Return to a snapshot: code at the git tag + private graph reloaded from that snapshot's dump.
#   scripts/restore_state.sh baseline-v0
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
TAG="${1:?tag}"; URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
case "$URI" in *7688*) echo "refusing to restore into the SHARED graph ($URI)"; exit 1;; esac
git stash push -q -m "restore_state autosave $(date -u +%FT%TZ)" || true
git checkout -q "$TAG" && echo "code at $TAG ($(git rev-parse --short HEAD)); uncommitted work stashed (git stash list)"
if [ -f "bench/snapshots/$TAG/graph.json" ]; then
  NEO4J_URI="$URI" .venv/bin/python - "bench/snapshots/$TAG/graph.json" <<'PY'
import json, sys
from datetime import datetime
from rescuegrid.graph import GraphStore
d = json.load(open(sys.argv[1])); g = GraphStore(); g.wipe(); g.apply_schema()
def fix(p):  # ISO strings -> datetime so Neo4j stores DateTime again
    return {k: (datetime.fromisoformat(v) if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[10] == "T" else v) for k, v in p.items()}
for n in d["nodes"]:
    labels = ":".join(n.pop("_labels")); props = fix({k: v for k, v in n.items() if k != "location"})
    g.write(f"CREATE (n:{labels}) SET n = $p, n.location = CASE WHEN $p.lat IS NULL THEN null ELSE point({{latitude: $p.lat, longitude: $p.lon}}) END", p=props)
for r in d["rels"]:
    g.write(f"MATCH (a {{id:$a}}), (b {{id:$b}}) CREATE (a)-[x:{r['type']}]->(b) SET x = $p", a=r["a"], b=r["b"], p=fix(r["props"]))
print("graph restored:", g.counts())
PY
else
  NEO4J_URI="$URI" .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
fi
