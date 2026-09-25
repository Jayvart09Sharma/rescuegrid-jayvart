#!/usr/bin/env bash
# Resume #2 (team asked to keep the stack idle for a clean comparison): ho-best-llm repeat 3, ho-best-auto K=3, then cloud vs local.
set -uo pipefail
cd "$(dirname "$0")/.."
export NEO4J_URI="bolt://127.0.0.1:7687"
.venv/bin/python -c "
from rescuegrid.graph import GraphStore; g=GraphStore(); assert '7687' in g.cfg.neo4j_uri; print('pinned to', g.cfg.neo4j_uri)" || { echo "private graph not reachable - aborting"; exit 1; }
traffic () { echo "external traffic check $(date -u +%T): qa.out POSTs=$(grep -c 'POST /qa' qa.out 2>/dev/null || echo 0), gateway /qa=$(grep -c 'POST /qa' /home/hp2/Shresth/rescuegrid/bus/gateway.out 2>/dev/null || echo 0), bus events_received=$(curl -s -m 3 http://127.0.0.1:8096/health | python3 -c 'import json,sys; print(json.load(sys.stdin).get("events_received"))' 2>/dev/null)"; }
traffic
BEST="entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render"
NEO4J_URI="bolt://127.0.0.1:7687" .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
echo "=== ho-best-llm repeat 3 $(date -u +%T) ==="
NEO4J_URI="bolt://127.0.0.1:7687" QA_FEATURES="$BEST" QA_MAX_REPAIRS=1 .venv/bin/python bench/run_bench.py --label ho-best-llm-rep3 --mode llm \
    --questions questions_heldout.json 2>&1 | grep -vE '^Transaction|^\[' | grep -E '^[a-z0-9-]+@|^saved'
NEO4J_URI="bolt://127.0.0.1:7687" .venv/bin/python scripts/ingest.py --reset --quiet | tail -1
echo "=== ho-best-auto K=3 $(date -u +%T) ==="
NEO4J_URI="bolt://127.0.0.1:7687" QA_FEATURES="$BEST,router_v2" QA_MAX_REPAIRS=1 .venv/bin/python bench/run_bench.py --label ho-best-auto --mode auto \
    --questions questions_heldout.json --repeats 3 2>&1 | grep -vE '^Transaction|^\[' | grep -E '^=== K=|^[a-z0-9-]+@|^saved .*k3'
traffic
git add bench/results && git commit -q -m "Held-out: best-config model-path repeat 3 and auto-mode K=3 (team stack idle)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>" && echo "committed held-out results"
echo "=== cloud vs local $(date -u +%T) ==="
NEO4J_URI="bolt://127.0.0.1:7687" CLOUD_MODEL="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B" ./bench/cloud_vs_local.sh 3 questions_heldout.json "auto llm" 2>&1 | tail -30
traffic
git add bench/results docs/CLOUD_VS_LOCAL.md 2>/dev/null && git commit -q -m "Cloud vs local results (Nebius, same model; normal + emulated throttled link; K=3; team stack idle)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>" && echo "committed cloud results"
echo "=== all done $(date -u +%T) ==="
