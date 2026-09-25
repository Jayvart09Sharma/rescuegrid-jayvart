#!/usr/bin/env bash
# Ablation ladder: each line = label | mode | QA_FEATURES. Graph is reset before every run. Results -> bench/results/<label>-<sha>.json
set -uo pipefail
cd "$(dirname "$0")/.."
export NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
case "$NEO4J_URI" in *7688*) echo "refusing to benchmark the shared graph"; exit 1;; esac
RUNS=(
  "all-llm|llm|entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema"
  "all-auto|auto|entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,router_v2"
  "l1-entity|llm|entity_link"
  "l2-entity-schema|llm|entity_link,schema_check"
  "l3-plus-bankv2|llm|entity_link,schema_check,fewshot_v2"
  "l4-plus-dyn|llm|entity_link,schema_check,fewshot_v2,dyn_fewshot"
  "solo-schema|llm|schema_check"
  "solo-dyn|llm|dyn_fewshot"
  "solo-compact|llm|compact_schema"
  "solo-bankv2|llm|fewshot_v2"
)
for spec in "${RUNS[@]}"; do
  IFS='|' read -r label mode feats <<< "$spec"
  .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
  echo "=== $label (mode=$mode, QA_FEATURES=$feats) ==="
  QA_FEATURES="$feats" .venv/bin/python bench/run_bench.py --label "$label" --mode "$mode" 2>&1 | grep -vE '^Transaction|^\[' | grep -A2 '^run '
done
echo "=== ladder done ==="
