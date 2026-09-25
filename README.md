# ResQ

**ResQ (formerly RescueGrid): one live graph of a disaster, built on the edge.** Drone video, radio, responder GPS, road cameras and sensors are fused
into a single temporal, provenance-tagged Neo4j graph and rendered as a 3D command-post twin for a county EOC watch
officer. All inference runs on an HP ZGX Nano (GB10, 128 GB unified memory), so it keeps working when the internet
does not. Built at the HP Edge AI SJSUHack by Shresth, Aditya, Kenil, Pranay and Jayvant. Code identifiers, env vars and the package keep the original `rescuegrid` name.

> Honesty line: the **inputs** are replayed (drone clips, two of them AI-generated; scripted or Piper-spoken radio;
> simulated GPS, sensors and field reports). **Detection, transcription, claim extraction, fusion, graph writes and
> Q&A are live and local** on the Nano. The console says which mode it is in.

## What is in the box

```
frontend/            Pranay's Command Twin (React + three.js). frontend/dist-live is prebuilt: no node needed to run.
backend/
  bus/               Shresth: event bus (POST /events -> fusion agent -> Neo4j) and the twin gateway (WS stream, uploads,
                     radio, reactive incident, Q&A proxy, serves the twin)                          :8096, :8097
  vision/            Aditya: vision v2, YOLO-World + CLIP gate on every frame, Qwen3-VL (via ZRT) once per hazard,
                     camera-motion estimate, claims to the bus; replay adapter for clips/folders       :8091
  asr/               faster-whisper large-v3-turbo, OpenAI-style /v1/audio/transcriptions             :8090
  radio/             Piper TTS radio library (10 EOC channel-3 lines) + say.py for spoken lines
  reasoning/         Kenil: fusion agent (status policy, conflicts, NEAR/AFFECTS/ASSIGNED_TO rules) and the Q&A layer
                     (7 pre-baked intents + guarded LLM text->Cypher), serve_qa.py                    :8095
  adapters/          Jayvant: sensor / GPS / field-report replay adapters, network-condition simulator
  scenario/          the ONE timeline (scenario.json), the shared adapter helper, the master replay runner
  neo4j/             the shared graph: control script, schema, static seed, deep test (Neo4j + JDK downloaded by setup)
  tests/             hard_cases.py: 38 adversarial cases against the live stack
videos/              the two AI-generated demo clips (Pexels clips: see videos/README.md)
docs/                the master plan and the individual briefs
setup.sh / start.sh / stop.sh / status.sh
```

## Architecture

```
uploaded video / clips  ─►  vision v2 (:8091) ──┐        radio audio ─► ASR (:8090) ─► LLM claim (ZRT :8080) ─┐
GPS · sensors · reports ─►  replay adapters   ──┼─► event bus (:8096) ─► fusion agent ─► Neo4j (:7688) ◄── Q&A (:8095)
                                                │                                            │
                     twin gateway (:8097) ◄─────┘   graph diff -> WebSocket patches           │
                     serves the Command Twin, uploads, radio queue, reactive incident, IC decisions
```

Three rules: **Neo4j is the only source of truth** (the twin renders the graph, Q&A queries the graph); **every fact
carries a timestamp and a source** (`status_since`, `last_confirmed`, `source`, `confidence`, `raw_evidence_ref`,
conflicts kept on the node); **humans stay in control** (reroutes and withdrawals are suggestions the IC approves;
the decision is recorded in the graph).

## Quick start

Requirements: Linux with Python 3.12, curl, ~6 GB disk for models, an NVIDIA GPU for tier-1 vision (or `--no-gpu`),
and, for the LLM and Qwen3-VL, an OpenAI-compatible server on `127.0.0.1:8080` (on the ZGX Nano: ZRT, below).

```bash
git clone https://github.com/Jayvart09Sharma/rescuegrid-jayvart.git rescuegrid && cd rescuegrid
./setup.sh              # venvs, ASR model, Piper voices + radio library, YOLO-World + CLIP, Neo4j + JDK, schema + seed
./start.sh              # Neo4j, ASR, vision, Q&A, bus, gateway; prints ./status.sh
# open http://127.0.0.1:8097/  (from a laptop: ssh -L 8097:127.0.0.1:8097 user@host, then http://localhost:8097/)
```

On the start screen: tick drones and give each a video, tick radio transmissions (or add recordings), set the gap,
press **START INCIDENT**. Or **RUN REHEARSAL SCENARIO** for the 13-event Pine County timeline, or **SIMULATION** for
the in-browser replay that needs no backend at all. **RESET INCIDENT** puts the graph back to the static seed.

### Models on the ZGX Nano (ZRT)

```bash
zrt serve hf:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8 --name llm    --gpu-memory-fraction 0.40 --max-model-len 32768
zrt serve hf:Qwen/Qwen3-VL-8B-Instruct-FP8             --name vision --gpu-memory-fraction 0.20 --max-model-len 8192
zrt status              # both Ready on http://127.0.0.1:8080 (first cold start ~15 min: kernel compile)
```

Set `LLM_BASE_URL` in `backend/reasoning/.env` and `RESCUEGRID_LLM` / `ZRT_URL` for the gateway and vision service if
the server is elsewhere. Without an LLM the pre-baked Q&A, fusion, vision tier 1 and the twin still work; Qwen
assessments and radio claim extraction do not.

### Rehearsal from the shell

```bash
cd backend/scenario && python3 replay.py                   # all feeds, real time; clips through vision v2 live
RESCUEGRID_SPEED=5 python3 replay.py --sources sensor,gps  # some feeds, 5x
curl -s http://127.0.0.1:8096/metrics | python3 -m json.tool   # event->graph p50/p95, bandwidth avoided, on-device %
backend/bus/.venv/bin/python backend/tests/hard_cases.py       # after a fresh replay
```

## Measured (2026-09-25, on the Nano, all models loaded)

| | |
|---|---|
| Event -> graph update (bus -> fusion agent -> Neo4j, one transaction) | p50 ~180 ms, p95 ~580 ms (13 events, cold); p95 189 ms warm |
| Hazard on screen -> fact in graph (3-frame gate + one Qwen3-VL call + bus) | p50 3.8 s, p95 4.8 s |
| Vision tier 1 per frame (YOLO-World + CLIP ViT-L/14) | p50 64 ms; Qwen called on ~4% of frames |
| Radio: recording -> transcript -> claim in graph | ASR ~5.5 s + LLM ~2.5 s |
| Q&A: pre-baked intents / LLM text->Cypher | 0.04-0.3 s / 6-9 s |
| Bandwidth avoided | 6.35 MB of frames and readings in, 6.6 KB of events out (~970x) |
| Hard-case suite | 32 pass, 2 warn, 4 fail (Kenil's known Q&A/policy items) |

## Where to read more

`backend/scenario/README.md` (timeline, event contract, gateway, radio, reactive incident, benchmarks),
`backend/vision/README.md` (pipeline, gate, footage, performance), `backend/reasoning/README.md` (fusion, conflicts,
Q&A contract), `frontend/README.md` (the twin, live vs simulation, contracts), `docs/` (the plan and the briefs).

## Branches

`main` is the consolidated app. Each teammate's piece is also on its own branch as it was on the Nano:
`shresth-backend`, `aditya-vision`, `kenil-reasoning` (with history), `pranay-frontend`, `jayvant-adapters`;
`legacy-main` is what `main` held before the consolidation.
