#!/usr/bin/env bash
# RescueGrid one-time setup: Python environments, model weights, Neo4j + JDK, Piper voices, radio library, the twin build.
# Tested on the HP ZGX Nano (Ubuntu, aarch64, Python 3.12, CUDA 13). No sudo needed: everything lands inside this folder
# or in ~/.cache. Re-run safely; each step skips what already exists.
#   ./setup.sh            everything
#   ./setup.sh --no-gpu   skip the vision environment (torch/CUDA), e.g. on a laptop
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; B="$R/backend"
PY=${PYTHON:-python3}
ARCH=$(uname -m)
step() { printf '\n\033[1;36m>> %s\033[0m\n' "$*"; }
venv() { # venv <dir> <requirements...>
  local d="$1"; shift
  [ -x "$d/.venv/bin/python" ] || $PY -m venv "$d/.venv"
  "$d/.venv/bin/pip" install -q --upgrade pip
  "$d/.venv/bin/pip" install -q "$@"
}

step "reasoning (Kenil): fusion agent + Q&A"
venv "$B/reasoning" -r "$B/reasoning/requirements.txt"
[ -f "$B/reasoning/.env" ] || cp "$B/reasoning/.env.example" "$B/reasoning/.env"

step "event bus + twin gateway (Shresth)"
venv "$B/bus" -r "$B/bus/requirements.txt"

step "ASR (faster-whisper large-v3-turbo, CPU int8)"
venv "$B/asr" -r "$B/asr/requirements.txt"
"$B/asr/.venv/bin/python" - <<'EOF'
from faster_whisper import WhisperModel
WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")   # downloads ~1.6 GB into ~/.cache once
print("   ASR model cached")
EOF

step "radio (Piper voices + library)"
venv "$B/radio" -r "$B/radio/requirements.txt"
mkdir -p "$B/radio/voices"
for v in en_US-ryan-medium en_US-lessac-medium en_US-joe-medium; do
  d=${v%%-*}; n=${v#*-}; n=${n%-*}; q=${v##*-}
  for ext in onnx onnx.json; do
    [ -f "$B/radio/voices/$v.$ext" ] || curl -sL -o "$B/radio/voices/$v.$ext" "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/$d/$n/$q/$v.$ext"
  done
done
[ -f "$B/radio/library/library.json" ] || (cd "$B/radio" && .venv/bin/python make_radio.py)

if [ "${1:-}" != "--no-gpu" ]; then
  step "vision (Aditya): YOLO-World + CLIP tier 1; Qwen3-VL is served by ZRT"
  [ -x "$B/vision/.venv/bin/python" ] || $PY -m venv "$B/vision/.venv"
  "$B/vision/.venv/bin/pip" install -q --upgrade pip
  "$B/vision/.venv/bin/pip" install -q torch torchvision --index-url "${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"
  "$B/vision/.venv/bin/pip" install -q -r "$B/vision/requirements.txt"
  mkdir -p "$B/vision/weights" "$B/vision/frames"
  [ -f "$B/vision/weights/yolov8m-worldv2.pt" ] || curl -sL -o "$B/vision/weights/yolov8m-worldv2.pt" "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8m-worldv2.pt"
  "$B/vision/.venv/bin/python" -c "import clip; clip.load('ViT-L/14', device='cpu'); print('   CLIP ViT-L/14 cached')"
  ln -sfn ../vision/samples "$B/vision-v1/samples" 2>/dev/null || true
fi

step "Neo4j 5.26 community + Temurin JDK 21 (no docker, no sudo) on bolt 7688 / http 7475"
if [ ! -x "$B/neo4j/jdk/bin/java" ]; then
  JARCH=$([ "$ARCH" = "aarch64" ] && echo aarch64 || echo x64)
  curl -sL -o /tmp/jdk.tgz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/$JARCH/jdk/hotspot/normal/eclipse"
  mkdir -p "$B/neo4j/jdk" && tar -xzf /tmp/jdk.tgz -C "$B/neo4j/jdk" --strip-components=1 && rm /tmp/jdk.tgz
fi
if [ ! -x "$B/neo4j/neo4j/bin/neo4j" ]; then
  curl -sL -o /tmp/neo4j.tgz "https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz"
  mkdir -p "$B/neo4j/neo4j" && tar -xzf /tmp/neo4j.tgz -C "$B/neo4j/neo4j" --strip-components=1 && rm /tmp/neo4j.tgz
  cat >> "$B/neo4j/neo4j/conf/neo4j.conf" <<'EOF'

# RescueGrid shared graph (setup.sh): localhost only, non-default ports so a teammate's own Neo4j can keep 7687
server.default_listen_address=127.0.0.1
server.bolt.listen_address=:7688
server.http.listen_address=:7475
server.https.enabled=false
server.memory.heap.initial_size=1g
server.memory.heap.max_size=2g
EOF
  JAVA_HOME="$B/neo4j/jdk" "$B/neo4j/neo4j/bin/neo4j-admin" dbms set-initial-password rescuegrid >/dev/null
fi
"$B/neo4j/neo4jctl.sh" start
"$B/neo4j/neo4jctl.sh" schema && "$B/neo4j/neo4jctl.sh" seed

step "twin (Pranay): prebuilt bundle is in frontend/dist-live; rebuild only if node is available"
if command -v node >/dev/null 2>&1 && [ -d "$R/frontend/node_modules" ]; then
  (cd "$R/frontend" && VITE_RG_LIVE_URL=/stream VITE_RG_QA_URL=/qa VITE_RG_NETSIM_URL=/netsim VITE_RG_SCENARIO_START=2026-09-25T14:00:00Z \
     node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build --outDir dist-live)
else
  echo "   (skipped: run 'npm install' in frontend/ and re-run setup.sh to rebuild; dist-live is committed)"
fi

step "done. Models served by ZRT are NOT started here (see README 'Models on the ZGX Nano'). Next: ./start.sh"
