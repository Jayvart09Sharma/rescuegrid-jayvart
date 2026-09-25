#!/usr/bin/env bash
# Held-out evaluation with K repeats: baseline (no flags) vs best config, pure model path, plus production auto mode.
#   bench/heldout.sh [K]      (default K=3; ~9 min per pass per config)
set -uo pipefail
cd "$(dirname "$0")/.."
export NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
case "$NEO4J_URI" in *7688*) echo "refusing to benchmark the shared graph"; exit 1;; esac
K="${1:-3}"
BEST="${BEST:-entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render}"
RUNS=(
  "ho-baseline-llm|llm||2"
  "ho-best-llm|llm|$BEST|2"
  "ho-best-auto|auto|$BEST,router_v2|2"
)
for spec in "${RUNS[@]}"; do
  IFS='|' read -r label mode feats reps <<< "$spec"
  .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
  echo "=== $label (mode=$mode, QA_FEATURES=$feats, QA_MAX_REPAIRS=$reps, K=$K) ==="
  QA_FEATURES="$feats" QA_MAX_REPAIRS="$reps" .venv/bin/python bench/run_bench.py --label "$label" --mode "$mode" --questions questions_heldout.json --repeats "$K" 2>&1 | grep -vE '^Transaction|^\[' | grep -E '^=== K=|^[a-z0-9-]+@|^run |^saved' | head -12
done
echo "=== heldout done ==="
