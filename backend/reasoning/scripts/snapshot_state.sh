#!/usr/bin/env bash
# Make the current state recoverable: git tag + JSON dump of the PRIVATE graph + copy of latest bench results.
#   scripts/snapshot_state.sh <tag> "<message>"      -> creates bench/snapshots/<tag>/ and git tag <tag>
#   scripts/restore_state.sh <tag>                   -> checks out the tag and reloads the graph from the dump
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
TAG="${1:?tag name}"; MSG="${2:-snapshot $TAG}"
URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
case "$URI" in *7688*) echo "refusing to snapshot the SHARED graph ($URI); set NEO4J_URI to the private instance"; exit 1;; esac
OUT="bench/snapshots/$TAG"; mkdir -p "$OUT"
NEO4J_URI="$URI" .venv/bin/python - "$OUT/graph.json" <<'PY'
import json, sys
from rescuegrid.graph import GraphStore, to_native
g = GraphStore()
nodes = [dict(r["n"], _labels=r["l"]) for r in g.read("MATCH (n) RETURN properties(n) AS n, labels(n) AS l")]
rels = [{"a": r["a"], "b": r["b"], "type": r["t"], "props": r["p"]} for r in g.read("MATCH (a)-[r]->(b) RETURN a.id AS a, b.id AS b, type(r) AS t, properties(r) AS p")]
json.dump({"nodes": nodes, "rels": rels}, open(sys.argv[1], "w"), default=lambda v: to_native(v).isoformat() if hasattr(to_native(v), "isoformat") else str(v), indent=0)
print(f"graph dump: {len(nodes)} nodes, {len(rels)} relationships -> {sys.argv[1]}")
PY
ls bench/results/*.json >/dev/null 2>&1 && cp bench/results/*.json "$OUT/" || true
git add -A && (git commit -q -m "$MSG

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" || true)
git tag -f -a "$TAG" -m "$MSG" && echo "tagged $TAG at $(git rev-parse --short HEAD)"
