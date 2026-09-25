#!/usr/bin/env bash
# Local (Nemotron-3-Nano-30B-A3B-FP8 on the ZGX Nano via ZRT) vs cloud (the same model family on Nebius Token Factory),
# identical pipeline, only the model endpoint changes. Local and cloud run SIMULTANEOUSLY on the same frozen private
# graph (Q&A is read-only, so no resets between repeats), first over the real network, then with the WAN emulated as
# throttled (rescuegrid/qa/netlink.py). Results -> bench/results/cloud_vs_local/, report -> docs/CLOUD_VS_LOCAL.md
#
#   CLOUD_MODEL=<nebius model id> bench/cloud_vs_local.sh [K] [questions file] [modes]
#   defaults: K=3, questions_heldout.json, modes "auto llm"
set -uo pipefail
cd "$(dirname "$0")/.."
export NEO4J_URI="bolt://127.0.0.1:7687"      # ALWAYS the private benchmark graph, never the shared 7688
K="${1:-3}"; QFILE="${2:-questions_heldout.json}"; MODES="${3:-auto llm}"
FEATS="${FEATS:-entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,det_render}"
CLOUD_URL="${CLOUD_URL:-https://api.tokenfactory.nebius.com/v1/}"
: "${CLOUD_MODEL:?set CLOUD_MODEL to the Nebius model id (see GET /v1/models)}"
grep -q '^NEBIUS_API_KEY=' .env || { echo "NEBIUS_API_KEY missing from .env"; exit 1; }
OUT="cloud_vs_local"; mkdir -p "bench/results/$OUT"; LOG="bench/results/$OUT/run.log"
echo "=== start $(date -u +%FT%TZ) K=$K questions=$QFILE modes=$MODES features=$FEATS cloud=$CLOUD_MODEL" | tee -a "$LOG"
.venv/bin/python scripts/ingest.py --reset --quiet | tail -1 | tee -a "$LOG"     # one reset, before anything runs

run_pair () {   # $1 = link profile for the cloud side, $2 = mode
  local prof="$1" mode="$2" auto_feats="$FEATS"
  [ "$mode" = "auto" ] && auto_feats="$FEATS,router_v2"
  echo "--- mode=$mode cloud_link=$prof: local and cloud in parallel" | tee -a "$LOG"
  QA_FEATURES="$auto_feats" QA_MAX_REPAIRS=1 LLM_NET_PROFILE=none \
    .venv/bin/python bench/run_bench.py --label "local-$prof-$mode" --mode "$mode" --questions "$QFILE" --repeats "$K" --no-reset --out "$OUT" \
    > "bench/results/$OUT/local-$prof-$mode.out" 2>&1 &
  local p1=$!
  QA_FEATURES="$auto_feats" QA_MAX_REPAIRS=1 LLM_NET_PROFILE="$prof" LLM_BASE_URL="$CLOUD_URL" LLM_MODEL="$CLOUD_MODEL" \
    .venv/bin/python bench/run_bench.py --label "cloud-$prof-$mode" --mode "$mode" --questions "$QFILE" --repeats "$K" --no-reset --out "$OUT" \
    > "bench/results/$OUT/cloud-$prof-$mode.out" 2>&1 &
  local p2=$!
  wait $p1; wait $p2
  grep -hE '^=== K=' "bench/results/$OUT/local-$prof-$mode.out" "bench/results/$OUT/cloud-$prof-$mode.out" | sed "s/^/  /" | tee -a "$LOG"
}
for prof in none throttled; do
  for mode in $MODES; do run_pair "$prof" "$mode"; done
done
.venv/bin/python bench/compare_cloud_local.py "bench/results/$OUT" | tee -a "$LOG"
echo "=== done $(date -u +%FT%TZ)" | tee -a "$LOG"
