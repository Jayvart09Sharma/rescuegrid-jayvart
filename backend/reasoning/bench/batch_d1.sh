#!/usr/bin/env bash
# Measurement batch after the D1 fixes (dev set). label | mode | QA_FEATURES | QA_MAX_REPAIRS
set -uo pipefail
cd "$(dirname "$0")/.."
export NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
case "$NEO4J_URI" in *7688*) echo "refusing to benchmark the shared graph"; exit 1;; esac
RUNS=(
  "d1-nofeat|llm||2"
  "d1-compact-dyn|llm|fewshot_v2,dyn_fewshot,compact_schema|2"
  "d1-all-norender|llm|entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema|2"
  "d1-all|llm|entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render|2"
  "d1-all-rep1|llm|entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render|1"
  "d1-all-auto|auto|entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render,router_v2|2"
)
for spec in "${RUNS[@]}"; do
  IFS='|' read -r label mode feats reps <<< "$spec"
  .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
  echo "=== $label (mode=$mode, QA_FEATURES=$feats, QA_MAX_REPAIRS=$reps) ==="
  QA_FEATURES="$feats" QA_MAX_REPAIRS="$reps" .venv/bin/python bench/run_bench.py --label "$label" --mode "$mode" 2>&1 | grep -vE '^Transaction|^\[' | grep -A2 '^run '
done
echo "=== batch done ==="
