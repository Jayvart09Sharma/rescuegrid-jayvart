# RescueGrid scenario timeline + event bus (Shresth)

One clock, one door into the graph. Every replay adapter reads `scenario.json`, waits for its events' `t`, and
POSTs them to the event bus. The bus runs Kenil's fusion agent in-process and writes the shared Neo4j. Nothing
else writes to the graph.

```
scenario.json ─┬─ Jaya/jayvart/sensors/sensor_replay.py        (sensor)        ─┐
               ├─ Jaya/jayvart/gps/gps_replay.py                (gps)           ─┤
               ├─ Jaya/jayvart/field_reports/field_report_replay.py (field_report) ┤  POST /events   bus :8096 ── FusionAgent ── Neo4j :7688
               ├─ scenario/replay.py --sources radio_asr  (scripted radio claims) ─┤   (six-field       (Kenil's code)      (shared)
               └─ scenario/replay.py --sources drone_vision                        │    contract)                              │
                     clip -> Aditya/vision/replay.py -> vision v2 :8091 ───────────┘                                          │
                     (YOLO-World + CLIP gate -> Qwen3-VL -> v2 posts the claim itself)                                        │
                                                                                                                              ▼
   Pranay's twin (mockfrontend/edgeAI/frontend, dist-live)  <── WS /stream, /qa, /netsim, /evidence ──  bus/gateway.py :8097 ─┘
                                                                 (graph ids -> twin ids, Neo4j diff -> GraphPatches)
```

## The timeline (`scenario.json`)

13 events over 3 minutes of scenario time, starting `2026-09-25T14:00:00Z`. `t` is seconds after start; the helper
turns it into the event's `timestamp`. Entity names are the seeded graph's names or aliases (`Rescue Team 4`, `Main
Street`, `Building-14`), so the fusion agent resolves them without creating strays. The story: quake at t=0, drone
sees Building 14 down at 15 s, radio says Main Street blocked at 32 s, Rescue 4 arrives (GPS + vision agree), gas
spikes next to the building at 90 s, Rescue 4 goes on scene, then radio says Bridge Street is open and 15 s later
drone shows it flooded: a conflict the graph keeps both sides of. Demo questions per beat are in `demo_questions_after`.

**Adding events:** append to `events`, keep `t` ascending, use the six contract fields, put extras in `details`.
Give every event a stable `event_id` so re-runs are deduplicated by the agent.

**Drone / road-camera events are real inference.** When `details.media` is a video clip (`videos/` is a symlink to
`Shresth/videos`), `scenario/replay.py` plays it through Aditya's vision v2 with his adapter
(`Aditya/vision/replay.py --ts-start <event timestamp> --speed <replay speed>`, 2 fps, frame timestamps on the
scenario clock). v2 confirms the hazard over 3 frames, asks Qwen3-VL once, and posts the six-field claim to the bus
itself (with `raw_bytes` for the bandwidth metric and `vision/frames/...` evidence). The scripted `claim`/`confidence`
in scenario.json is only the *expected* result: the runner prints v2's claims at the end and posts the scripted claim
as a fallback only if v2 produced nothing for that camera. `--scripted-vision` (or vision down) posts the scripted
claims, as before. A still image in `details.media` is sent as one forced frame (`details.live_tier1` attached) and the
scripted claim is posted. `evt-0005` (Rescue 4 `near` Building 14) stays scripted: pixels cannot say which team it is.

| t | camera | clip | v2 claim (rehearsed) |
|---|---|---|---|
| 15 s | drone-1 | `pixverse_c1 (3).mp4` (AI-generated, 5 s) | `Building-14 collapsed` 0.93 at t+3 s |
| 32 s | drone-1 | `pixverse_c1 (4).mp4` (AI-generated, 5 s) | `Main Street blocked` 0.98 at t+1 s, confirms the radio claim |
| 75 s | roadcam-3 | `High-angle-multi-lane-traffic.mp4` (Pexels, 31 s) | `Road-River open` 0.99 at the 3rd frame, tier 1 only |
| 165 s | drone-2 | `Flooded-Suburban-Area-With-Submerged-Roads.mp4` (Pexels, 18 s) | `Road-Bridge blocked` 0.96 at t+1 s -> conflict with radio `open` 0.75 |

Say it in the pitch: inputs are replayed clips (two of them AI-generated); detection, gating, Qwen assessment, fusion
and graph writes are live on the Nano.

**Using your own footage (e.g. a real 45 s drone video).** Nothing about the clips is hardcoded: any video file goes
through the same detector, gate and Qwen call, and the claim is whatever the detector confirmed. To swap one in:

1. Put the file anywhere (`videos/` is convenient) and point the event at it: `"details": {"media": "videos/my_drone.mp4",
   "camera": "drone-1"}`. Absolute paths work too. `"fps"` (default 2) and `"entities": {"building": "Building-14",
   "road": "Main Street"}` are optional; `entities` overrides Aditya's `camera_entities.json` for that clip, so a new
   video can be pointed at any seeded entity without touching his file. Names must exist in the seed
   (`neo4j/schema/seed_static.cypher`).
2. The event's `claim`/`confidence` are only what you *expect*; write what the footage should show. If the detector does
   not see it, the runner prints a warning and posts nothing (there is no scripted fallback).
3. Length does not matter: a 45 s clip is ~90 frames at 2 fps, ~65 ms each; Qwen is called once per new hazard per
   camera (re-assessed every 60 s while it persists). A clip that shows several hazards in sequence (collapse, then a
   blocked street) yields one claim per hazard, each mapped through the camera's building/road entities.
4. Dry-run it first without touching the shared graph: start a second vision instance on :8092 with no bus
   (`cd /home/hp2/Aditya/vision && /home/hp2/Shresth/rescuegrid/vision/.venv/bin/python -m uvicorn server:app --port 8092`)
   and play the clip with `replay.py my.mp4 --url http://127.0.0.1:8092 --source-id drone-1 --wait`; it prints the
   tier-1 label per frame and the claims it would post.
5. The twin's camera panel shows the real analysed frame with its boxes while the clip plays (live mode).
6. Easiest of all: open the twin (http://localhost:8097/ through the tunnel), and on the start screen upload the video,
   pick the camera and what it watches, START STREAM. That calls the gateway's `POST /streams`, which runs the same
   adapter command as above; the stream and its frame count show on the start screen (STREAMS button). Uploads land
   in `videos/uploads/` with a `.log` per stream.

## Run a rehearsal

```
Shresth/rescuegrid/bus/run.sh &                                  # event bus (needs Neo4j :7688 up)
Shresth/rescuegrid/bus/gateway.sh &                              # twin gateway :8097 (serves the live twin; optional for backend-only runs)
# wipe + reseed the shared graph WITHOUT Kenil's dummy events (ingest.py --reset would replay them):
cd /home/hp2/kenil/rescuegrid-reasoning && NEO4J_URI=bolt://127.0.0.1:7688 .venv/bin/python -c "from rescuegrid.graph import GraphStore; g=GraphStore(); g.reset()"
# restart the bus + gateway after a reset so their counters/history start clean

# one command, everything, real time:
python3 Shresth/rescuegrid/scenario/replay.py
# or the real shape, one process per adapter on the same clock (RESCUEGRID_SPEED=20 for a 9 s rehearsal):
RESCUEGRID_SPEED=20 python3 Jaya/jayvart/sensors/sensor_replay.py &
RESCUEGRID_SPEED=20 python3 Jaya/jayvart/gps/gps_replay.py &
RESCUEGRID_SPEED=20 python3 Jaya/jayvart/field_reports/field_report_replay.py &
python3 Shresth/rescuegrid/scenario/replay.py --sources drone_vision,radio_asr --speed 20 &
curl -s http://127.0.0.1:8096/metrics | python3 -m json.tool                 # Shresth's benchmark numbers, live
```

## Writing an adapter (live or replay)

```python
import sys; sys.path.insert(0, "/home/hp2/Shresth/rescuegrid/scenario")
from rescuegrid_events import scenario_events, ScenarioClock, post_event
clock = ScenarioClock()                               # RESCUEGRID_SPEED env sets the speed
for ev in scenario_events(sources=["sensor"]):        # contract-shaped, timestamp filled in
    clock.wait_until(ev["_t"]); post_event(ev, raw_bytes=64)
```
A live adapter skips the scenario and calls `post_event({...six fields...}, raw_bytes=<size of the frame/clip>)` whenever
the real feed produces something. `raw_bytes` is what makes the bandwidth-avoided number honest. stdlib only, no venv needed.

## Bus API (port 8096)

| | |
|---|---|
| `POST /events` | one event or a list. Reply: `action` (override / confirm / conflict / near / position_update / stale / duplicate / ambiguous / invalid / error), `entity_id`, `applied`, `notes`, `relationships`, `latency_ms` |
| `GET /events/recent?n=50&source=gps` | last events with their fusion outcome |
| `GET /metrics` | received/applied/errors, per-source counts, event-to-graph p50/p95, raw bytes vs event bytes, on-device %, scenario clock |
| `GET /health` | bus + Neo4j reachability |

## Twin gateway (port 8097, `bus/gateway.py`)

The only way the frontend sees the backend. `WS /stream` sends the graph as the twin's nodes/edges (ids translated,
provenance = the `Event` audit trail), then every bus event with the Neo4j diff it caused as `patches`, periodic
diffs, the scenario clock, network state and reroute suggestions. `POST /qa` proxies Kenil's `/qa` and returns the
twin's `FlyToSignal` (ids translated). `GET/POST /netsim/status` holds the WAN state (forwarded to Jayvant's Flask
simulator on :5000 when it runs). `POST /suggestions/{id}` records the IC's approve/dismiss through the bus as a
field report (rule 3: the suggestion came from the graph, the decision is human). `GET /evidence/<raw_evidence_ref>`
serves Aditya's frames and the sample stills. `GET /` serves `mockfrontend/edgeAI/frontend/dist-live`.
Streams: `POST /streams` (upload a video, play it into vision as a camera), `GET /streams`, `DELETE /streams/{id}`,
`POST /scenario/start {speed}` (the one-command rehearsal from the browser), `POST /reset` (graph back to the seed,
bus counters cleared via its new `POST /reset`, twins re-seeded), `GET /entities` (seeded names for the upload form). Suggestion
rule: whenever a road's status changes the gateway asks the graph "Can Ambulance 2 still reach the hospital?" (Kenil's
pre-baked intent); if a road on the route is blocked or uncertain, the route from that Cypher answer (`routeIds`) is
surfaced as the pending suggestion and the twin draws it from its own geometry. No coordinates or detours are scripted.
Cameras: `camera_entities.json` (read from the vision service) says which entity each camera watches; the gateway
relays every tier-1 result as a `camera` message and proxies the newest frame at `/evidence/latest/<camera>.jpg`, so
the twin's drones hover over what the cameras actually look at and its camera panel shows the real frame with boxes. Localhost only: from a laptop,
`ssh -L 8097:127.0.0.1:8097 hp2@<nano>` then http://localhost:8097/.

Every event and outcome is appended to `bus/bus.log.jsonl`. Env: `NEO4J_URI` (default the shared graph), `RESCUEGRID_REASONING`
(Kenil's folder), `RESCUEGRID_BUS_LOG`. `cloud_escalations` is a counter for the optional cloud path; nothing increments it yet, so on-device is 100%.

## Measured 2026-09-25, two full real-time rehearsals (13 events, five adapters, four clips through vision v2, all models loaded)

| Benchmark (from Shresth's brief) | Run 1 | Run 2 |
|---|---|---|
| Events applied | 13 of 13 | 13 of 13 |
| Event -> graph update (bus -> fusion agent -> Neo4j, one tx) | p50 163 ms, p95 474 ms | p50 192 ms, p95 579 ms |
| Vision, hazard confirmed -> claim in graph (tier 1 gate + one Qwen3-VL call + bus) | p50 3.8 s, p95 4.8 s | same (Qwen ~3.6 s here vs 1.8-2.0 s in Aditya's solo benchmark: the GPU is shared with Nemotron) |
| Vision tier 1 per frame (YOLO-World + CLIP) | p50 64 ms, p95 84 ms (143 frames, 3 Qwen calls) | p50 64 ms, p95 95 ms (128 frames, 4 Qwen calls) |
| Query response, pre-baked Q&A (7 intents) | 0.04-0.28 s | same |
| Query response, LLM text->Cypher path (free-form questions) | 6-9 s | same |
| Bandwidth avoided | 6.35 MB of frames/readings in, 6.6 KB of events out: ~970x | same |
| Events resolved on-device | 100% | 100% |
| Cloud escalation rate | 0% (no cloud path wired) | 0% |
| Twin: bus event -> WebSocket message with graph diff | < 0.5 s after the adapter's own timestamp for sensor/radio/GPS/report; 3-4.5 s for vision claims (Qwen) | same |
| Response time normal / throttled / zero WAN | not measured: Jayvant's simulator does not shape traffic yet (it sleeps on `/cloud/test`); nothing in the pipeline leaves localhost, so the on-device numbers above are the zero-WAN numbers | |

Hard-case suite (`tests/hard_cases.py`, run between the two rehearsals): 32 pass, 2 warn, 4 fail. The four failures are
Kenil's known open items (15: a 0.4 claim within 0.25 of a 0.9 one counts as a conflict; 27: multi-hop LLM question;
28: "last N minutes by source" incomplete; 30: nonsense question answered at 0.6 confidence).

## Radio and the reactive incident (2026-09-25 evening)

**Radio is real audio through the real pipeline.** `radio/make_radio.py` speaks ten EOC channel-3 lines with Piper (local
neural TTS, `radio/.venv`, voices in `radio/voices/`), shapes them like a handheld radio, and writes `radio/library/`.
At demo time a line (or an uploaded recording, `POST /radio`) goes: faster-whisper on the Nano (:8090) -> the local LLM
turns the transcript into ONE six-field claim with a JSON schema (entity names from the seed, claim vocabulary enforced)
-> the bus -> the graph. The twin plays the audio with a live spectrum (Web Audio analyser) in the RADIO CH3 panel and in
the provenance drawer of the event. Measured: ASR ~5.5 s for a 9 s clip (CPU, shared), claim extraction ~2.5 s.

**Reactive incident.** With REACTIVE INCIDENT on (start screen), the simulated feeds react to real detections instead of
running on a clock (`bus/gateway.py`, `react_to`): a building the drone sees collapsed -> dispatch calls it on the radio
(Piper voice -> ASR -> LLM -> graph, ~9 s), the nearest available rescue unit answers and rolls (simulated GPS fixes every
3 s at 9 m/s toward the building; the fusion agent derives NEAR from them), reports on scene by voice; 25 s after the
collapse the gas sensor that MONITORS that building spikes, the crew reports the gas odor. A blocked road -> dispatch
warns units (and the graph's reroute suggestion appears). A gas hazard with a unit inside -> a WITHDRAW suggestion.
Approving a reroute drives the unit along the graph route (road lat/lon) with an arrival report and radio call.
Every simulated event carries `details.simulated=true` and `raw_evidence_ref sim/...`; vision, ASR, fusion and the graph
are real. Each trigger fires once per entity per incident (RESET INCIDENT clears it).
