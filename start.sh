#!/usr/bin/env bash
# Start every RescueGrid service on this machine (after ./setup.sh). Logs in backend/*/**.out. Stop with ./stop.sh.
#   ./start.sh          Neo4j, ASR :8090, vision :8091, Q&A :8095, bus :8096, twin gateway :8097
# The two ZRT models must already be serving (zrt status): `llm` (Nemotron-3-Nano-30B-A3B-FP8) and `vision` (Qwen3-VL-8B FP8).
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; B="$R/backend"
up() { ss -ltn 2>/dev/null | grep -q ":$1 "; }
wait_port() { for i in $(seq 1 "${2:-60}"); do up "$1" && return 0; sleep 2; done; echo "!! :$1 did not come up"; return 1; }
bg() { # bg <port> <dir> <logname> <cmd...>
  local port="$1" dir="$2" log="$3"; shift 3
  if up "$port"; then echo "   :$port already up"; return; fi
  (cd "$dir" && nohup "$@" >> "$log" 2>&1 &)
  wait_port "$port" 90 && echo "   :$port up  ($log)"
}

echo ">> Neo4j (shared graph, bolt 7688)";      "$B/neo4j/neo4jctl.sh" start
echo ">> ASR faster-whisper :8090";             bg 8090 "$B/asr"    asr.out    .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8090
echo ">> vision v2 (YOLO-World + CLIP + Qwen via ZRT) :8091"
RESCUEGRID_BUS=http://127.0.0.1:8096 bg 8091 "$B/vision" vision.out .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8091
echo ">> Q&A (Kenil) :8095";                    bg 8095 "$B/reasoning" serve_qa.log .venv/bin/python scripts/serve_qa.py --port 8095
echo ">> event bus + fusion agent :8096";       bg 8096 "$B/bus"    bus.out    .venv/bin/uvicorn event_bus:app --host 127.0.0.1 --port 8096
echo ">> twin gateway + console :8097";         bg 8097 "$B/bus"    gateway.out .venv/bin/uvicorn gateway:app --host 127.0.0.1 --port 8097
echo
"$R/status.sh"
echo
echo "Console: http://127.0.0.1:8097/   (from a laptop: ssh -L 8097:127.0.0.1:8097 <user>@<this host>, then http://localhost:8097/)"
echo "Rehearsal from the shell: cd backend/scenario && python3 replay.py    (or press RUN REHEARSAL SCENARIO on the start screen)"
