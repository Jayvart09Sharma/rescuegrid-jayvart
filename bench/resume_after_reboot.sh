#!/usr/bin/env bash
# Resume after the 2026-09-25 04:42 UTC reboot: the held-out baseline cell (ho-baseline-llm, K=3) already finished;
# run only the two missing held-out cells, then the cloud-vs-local comparison. Private graph HARD-PINNED.
set -uo pipefail
cd "$(dirname "$0")/.."
export NEO4J_URI="bolt://127.0.0.1:7687"
.venv/bin/python -c "
from rescuegrid.graph import GraphStore; g=GraphStore(); assert '7687' in g.cfg.neo4j_uri; print('pinned to', g.cfg.neo4j_uri, '| events', g.read_dicts('MATCH (e:Event) RETURN count(e) AS c')[0]['c'])" || { echo "private graph not reachable - aborting"; exit 1; }
BEST="entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render"
for spec in "ho-best-llm|llm|$BEST" "ho-best-auto|auto|$BEST,router_v2"; do
  IFS='|' read -r label mode feats <<< "$spec"
  NEO4J_URI="bolt://127.0.0.1:7687" .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
  echo "=== $label (mode=$mode, QA_FEATURES=$feats, QA_MAX_REPAIRS=1, K=3) $(date -u +%T) ==="
  NEO4J_URI="bolt://127.0.0.1:7687" QA_FEATURES="$feats" QA_MAX_REPAIRS=1 .venv/bin/python bench/run_bench.py --label "$label" --mode "$mode" \
      --questions questions_heldout.json --repeats 3 2>&1 | grep -vE '^Transaction|^\[' | grep -E '^=== K=|^[a-z0-9-]+@|^saved .*k3'
done
git add bench/results && git commit -q -m "Held-out best-config results (K=3), resumed after reboot

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>" && echo "committed held-out results"
echo "=== cloud vs local $(date -u +%T) ==="
NEO4J_URI="bolt://127.0.0.1:7687" CLOUD_MODEL="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B" ./bench/cloud_vs_local.sh 3 questions_heldout.json "auto llm" 2>&1 | tail -30
git add bench/results docs/CLOUD_VS_LOCAL.md 2>/dev/null && git commit -q -m "Cloud vs local results (Nebius, same model; normal + emulated throttled link; K=3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>" && echo "committed cloud results"
echo "=== all done $(date -u +%T) ==="
