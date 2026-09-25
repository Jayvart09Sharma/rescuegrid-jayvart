# RescueGrid vision service v2 (Aditya)

Drone and road-camera frames come in, graph claims come out: `Building-14 collapsed`, `Main Street blocked`,
`Road-River open`, `Road-Bridge blocked`, each with a timestamp, confidence and the frames that produced it. All
inference is local on the ZGX Nano. Inputs are pre-recorded clips replayed as if live; inference, gating, fusion and
graph writes on them are live.

This replaces `Shresth/rescuegrid/vision/server.py` (v1). v1 is kept there as a backup, is stopped, and should not be used.

## Status against `aditya.md` (2026-09-24)

| Definition of done | Status |
|---|---|
| Model choice discussed and confirmed with Shresth (footprint-aware) | Done. No new models: the YOLO-World, CLIP and Qwen3-VL-8B that v1 already used. v2 replaced v1, so the GPU footprint is unchanged. |
| Detects the scenario's key events (Building 14 collapse, Main Street blockage) from sourced footage | Done: `Building-14 collapsed` 0.93, `Main Street blocked` 0.98. |
| Drone replay adapter working, matches the live-adapter interface | Done: `replay.py` plays a clip in real time, one frame per request, into the same endpoint a live camera would use. |
| Road-camera replay adapter reusing the same vision endpoint | Done: same script and endpoint with `--source-type roadcam`; `Road-River open` from the Pexels traffic clip. |
| Every detection emits a correctly-shaped event with a frame reference | Done: six-field contract events, validated against Kenil's `Event` model, written to the shared Neo4j, evidence frames served over HTTP. |

Open, outside `aditya.md`: scenario integration (on hold until everything else is finished), the
"Rescue Team 4 near Building 14" decision, and `hard_cases.py` case 36. See [Open items](#open-items).

## Pipeline

```
frame ─► Tier 1, every frame (~30-60 ms): YOLO-World objects + CLIP scene class
           │
           ▼ gate, per camera
           │   - only HAZARD signals count: a hazard scene (CLIP >= 0.5) or a hazard object (rubble, fire, flood water...)
           │     cars, people, trucks are context for Qwen, never a trigger
           │   - must hold 3 frames in a row          (one-frame flicker never reaches Qwen)
           │   - Qwen once per hazard per camera       (re-assessed every 60 s while it persists, dropped after 5 frames without it)
           ▼
         Tier 2, Qwen3-VL-8B via ZRT (~2 s): up to 4 frames from the camera's last 5 s in ONE request + the confirmed hazard as a hint
           ▼
         Contract event (source, timestamp, confidence, entity, claim, raw_evidence_ref) ─► event bus :8096 ─► Kenil's fusion agent ─► Neo4j
```

A road camera watching normal traffic reports its road `open` from tier 1 alone, with no Qwen call.

**The claim comes from tier 1, not from Qwen's sentence.** Claim = detector hazard class + Qwen's `road_passable`
(passable road -> `restricted` instead of `blocked`). Confidence = min(detector, Qwen); halved if Qwen saw no hazard;
if Qwen is down the claim still goes out on the detector alone at 0.8x confidence. Qwen's sentence is kept in
`details.vlm` for the watch officer. Why: Qwen alone reads the Main Street clip as a "vehicle collision" or "bridge
collapse", while CLIP labels it `blocked_road` on every frame. A stricter prompt did not fix Qwen's wording ("white
truck wrecked"), so the prompt was left unchanged.

## Demo footage

One clip per scenario camera event. Tested 2026-09-24 in real time at 2 fps; each claim validated against Kenil's
`Event` model and resolved to a seeded node.

| Scenario t | Camera | Clip (`/home/hp2/Shresth/videos/`) | Tier 1 | Claim | Qwen calls |
|---|---|---|---|---|---|
| 15 s | drone-1 | `pixverse_c1 (3).mp4`, 5.0 s | collapsed_building 0.62-0.93, confirmed at 3.0 s | `Building-14 collapsed` 0.93 | 1 |
| 32 s | drone-1 | `pixverse_c1 (4).mp4`, 5.0 s | blocked_road 0.88-1.00, confirmed at 1.0 s | `Main Street blocked` 0.98 (second source after radio) | 1 |
| 75 s | roadcam-3 | `High-angle-multi-lane-traffic.mp4`, 30.6 s | normal 0.99-1.00 on every frame | `Road-River open` 0.99 at the 3rd frame | 0 |
| 165 s | drone-2 | `Flooded-Suburban-Area-With-Submerged-Roads.mp4`, 18.0 s | flooded_road 0.90-0.99 on every frame, confirmed at 1.0 s | `Road-Bridge blocked` 0.96 (conflicts with radio "open") | 1 |

120 frames across the four clips (11 + 11 + 62 + 36), 3 Qwen calls. Qwen's sentences:

- Building 14: "Building collapse with emergency crews on scene and debris blocking roadway."
- Main Street: "Bridge collapse blocks road with emergency response visible." (wrong wording; the claim is still right)
- Bridge Street: "Widespread flooding inundates residential areas and roads with water covering structures and vegetation."

Play them into the graph at scenario time (service on :8091 with the bus on; `--wait` prints the claims):

```
cd backend/vision; PY=.venv/bin/python; V=../../videos
$PY replay.py "$V/pixverse_c1 (3).mp4" --source-id drone-1 --ts-start 2026-09-25T14:00:15Z --wait
$PY replay.py "$V/pixverse_c1 (4).mp4" --source-id drone-1 --ts-start 2026-09-25T14:00:32Z --wait
$PY replay.py "$V/High-angle-multi-lane-traffic.mp4" --source-id roadcam-3 --source-type roadcam --ts-start 2026-09-25T14:01:15Z --wait
$PY replay.py "$V/Flooded-Suburban-Area-With-Submerged-Roads.mp4" --source-id drone-2 --ts-start 2026-09-25T14:02:45Z --wait
```

A claim's `timestamp` is the frame where the hazard was confirmed, so it lands 0.5-3 s after `--ts-start`.

### Sources and licenses

- `pixverse_c1 (3).mp4`, `pixverse_c1 (4).mp4`: AI-generated with PixVerse by the team. Not real disaster footage;
  say so in the pitch and the repo.
- `Flooded-Suburban-Area-With-Submerged-Roads.mp4`: "Aerial View of Urban Flooded Area and Roads" by RD, Pexels,
  https://www.pexels.com/video/aerial-view-of-urban-flooded-area-and-roads-34473529/ (1440x2560 download of the
  1512x2688 original). Pexels License: free for commercial use, no attribution required. It shows a flooded
  residential area from the air, not literally a bridge; it stands in for Bridge Street.
- `High-angle-multi-lane-traffic.mp4`: "High-angle multilane traffic", Pexels, Pexels License, no attribution
  required. Does not read as distinctly U.S.
- Both Pexels clips are portrait (1440x2560). The pipeline does not care; the frontend may. Suggested demo segments:
  traffic 00:02-00:15, flood 00:02-00:14 (the full clips were tested; `replay.py` has no start/end option, so trim the
  files if you want only those segments).

## Integrating with v2 (what changed for each of you)

**Where it runs.** `http://127.0.0.1:8091`, the same port and `/v1/vision/frame` request as v1, so existing callers
work unchanged. v2 runs from `/home/hp2/Aditya/vision` with bus posting on; log in `vision.log`.
Restart: `pkill -f "uvicorn server:app --host 127.0.0.1 --port 8091"`, then the "live" command under [Run](#run).
Back to v1 (emergency only): stop v2, then `cd Shresth/rescuegrid/vision && .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8091`.

**Every run writes to the shared graph.** With bus posting on, any clip replayed into :8091 posts claims to the bus
and the shared Neo4j. For experiments, use a dry-run instance on another port (see [Run](#run)).

**Shresth (bus, scenario, benchmarks).** v2 posts contract events itself to `RESCUEGRID_BUS` (:8096), with
`details.raw_bytes` for the bandwidth metric. Your `scenario/replay.py` still works: it gets the tier-1 result as
before, but v2 posts no claim for its single forced stills (see [Callers that send isolated stills](#callers-that-send-isolated-stills)).
Vision benchmarks live at `GET /v1/vision/stats`. `tests/hard_cases.py` case 36 needs updating for v2.

**Kenil (fusion agent).** Every event is the six-field contract, `source: "drone_vision"`, validated against your
`Event` model. Claims v2 can emit, and what they mean:

| claim | entity type | when |
|---|---|---|
| `collapsed` | building | `collapsed_building` confirmed by the gate |
| `damaged` | building | `damaged_building` or `structure_fire` |
| `blocked` | road | `blocked_road`, debris, `flooded_road`, landslide, fallen tree, vehicle crash, downed power line, and Qwen says not passable (or unsure) |
| `restricted` | road | same hazards, but Qwen says the road is still passable |
| `open` | road | a `report_open` road camera sees confident normal traffic for 3 frames (tier 1 only) |

`entity` is always a seeded id or alias from `camera_entities.json`; `details.entity_type` is `building`/`road` (for
your `infer_label` if a name ever misses). `details.hazard` lists the detector classes; `details.vlm` holds Qwen's
assessment or `null` if Qwen was down (then `details.vlm_error` says why and confidence is discounted). v2 never emits
`near`, `intact`, `flooded` or team claims, and no clearing claim either (a hazard leaving the frame is not evidence the
road reopened). A flood is sent as `blocked` with `details.hazard: ["flooded_road"]`, matching the scenario's own
Bridge Street event.

**Pranay (frontend, provenance click-through).**
- Image behind a vision event: `raw_evidence_ref` is `vision/frames/<file>.jpg`; fetch it from
  `GET http://127.0.0.1:8091/v1/vision/frames/<file>.jpg`. On disk it is `/home/hp2/Aditya/vision/frames/<file>.jpg`,
  **not** under `Shresth/rescuegrid/` like the scripted `vision/samples/...` refs, so do not resolve both the same way.
- The up-to-4 frames Qwen looked at: `details.evidence_frames` (same format, oldest first).
- What to show as the explanation: `details.vlm.description` (one sentence), `details.vlm.hazards[]` (type, severity 1-5,
  confidence), `road_passable`, `people_visible`. Treat the description as supporting text; the claim is the fact.
- The service binds to 127.0.0.1. A browser on another machine needs a proxy through your backend, or run with
  `--host 0.0.0.0`.
- Live detector labels per frame (for an overlay while the clip plays): `GET /v1/vision/events?tier=1&source_id=drone-1&since=<iso>`.
- The two Pexels clips are portrait (1440x2560); the PixVerse clips are landscape (1280x720).

**Jayvant (network simulator).** At runtime vision only talks to localhost: Qwen via ZRT on 127.0.0.1:8080, the bus
on :8096; weights load from local disk (`weights/`, `~/.cache/clip`). Throttling or cutting the WAN should not change
vision latency. Not yet verified with the WAN actually cut; start the service *before* cutting it, since ultralytics
may try online checks at startup.

## Performance (measured 2026-09-24 on the Nano, Nemotron 30B + Qwen3-VL-8B loaded)

**How to send a clip to Qwen** (`bench_qwen.py`, 5 s drone clips, 6 runs per mode, inputs perturbed so vLLM's cache
never hits; raw results in `bench_qwen_results_2026-09-24.jsonl`):

| Qwen input per 5 s clip | time | first token | input tokens |
|---|---|---|---|
| 1 frame | 1.8 s | 0.13 s | 368 |
| **4 frames, one request (used)** | **2.0 s** | 0.24 s | 1,044 |
| 8 frames, one request | 2.3 s | 0.48 s | 1,932 |
| native video 360p 2 fps | 2.2 s | 0.42 s | 1,292 |
| native video 720p 2 fps | 3.3 s | 1.48 s | 4,592 |
| one request per second of video | 9.5 s | - | 1,840 |

Qwen's time is ~40 output tokens at ~23 tok/s, so extra frames are nearly free and extra *calls* are not. 720p
video costs 3.5x the tokens and read the Main Street clip as a "bridge collapse". A per-request video fps setting
is ignored by vLLM (it samples at 2 fps).

**Savings compared with Qwen alone.** Qwen GPU time for one minute of one camera at 2 fps (120 frames), using
tier 1 ~45 ms/frame, Qwen 1.8 s per frame or 2.0 s per 4-frame window:

| Approach | Hazard in view all minute | Normal footage |
|---|---|---|
| Qwen on every frame | ~216 s (3.6x slower than real time) | ~216 s |
| Qwen once per 5 s window | ~24 s | ~24 s |
| **v2 (tier 1 + gate)** | **~9 s** (5.4 s tier 1 + 2 Qwen calls) | **~5 s**, no Qwen calls |

That is ~96% less than Qwen on every frame and 60-80% less than per-window Qwen. With the scenario's three
cameras, per-window Qwen needs ~6 s of Qwen per 5 s of footage and falls behind; v2 calls Qwen only when something
new appears.

**v1 vs v2 on the same two PixVerse clips** (22 frames, real time):

| | v1 | v2 |
|---|---|---|
| Qwen calls | 11+ (14 triggers, mostly "new:truck"/"new:bus" flicker) | **2** |
| Claims in contract shape | 0 (v1 has no entity/claim) | **2**, both correct |

**Demo latency, hazard in view to fact in the graph:**

| Step | Time |
|---|---|
| Tier-1 result per frame (HTTP round trip) | ~35-120 ms |
| Hazard confirmed (3 frames in a row at 2 fps) | 1.0 s (Main Street, flood) to 3.0 s (Building 14) |
| Qwen assessment | ~1.9-2.4 s |
| Bus + fusion agent into Neo4j | 83-387 ms |
| **Total** | **~3-5.5 s** |

Pitch line: "Qwen ran on 3 of 120 frames; the rest took ~30-50 ms each." Not measured with Kenil's Q&A model busy
on the GPU at the same time; time one full rehearsal.

## Run

```
cd backend/vision
PY=.venv/bin/python        # torch, ultralytics, CLIP (setup.sh)

# live (how it runs now, on v1's old port so existing callers reach it): claims -> event bus -> fusion agent -> shared Neo4j
RESCUEGRID_BUS=http://127.0.0.1:8096 nohup $PY -m uvicorn server:app --host 127.0.0.1 --port 8091 >> vision.log 2>&1 &
# dry run on a spare port: claims kept in memory only, visible at /v1/vision/claims (~2 GB extra GPU while both run)
$PY -m uvicorn server:app --host 127.0.0.1 --port 8092

# replay adapter (drone or road camera, video file or folder of frames); --ts-start puts claims on the scenario clock
$PY replay.py "/home/hp2/Shresth/videos/pixverse_c1 (3).mp4" --source-id drone-1 --ts-start 2026-09-25T14:00:15Z --wait
$PY replay.py footage.mp4 --url http://127.0.0.1:8092 --source-id roadcam-3 --source-type roadcam --wait   # dry run

$PY bench_qwen.py out.jsonl                                      # Qwen input-mode benchmark (RUNS, MODES, HINT env)
```

Run `replay.py` from `/home/hp2/Aditya/vision`: v1 has its own `replay.py` in `Shresth/rescuegrid/vision/` with fewer
options. `replay.py` options: `--url` (default `$RESCUEGRID_VISION` or :8091), `--source-id`, `--source-type`,
`--fps` (default 2), `--speed` (0 = as fast as possible), `--entities` (JSON), `--ts-start`, `--loop`, `--wait`, `--quiet`.

Needs the ZRT `vision` model up (`zrt status`). GPU memory for tier 1: ~2.5 GB. Qwen3-VL-8B-FP8 is served by ZRT at
`--gpu-memory-utilization 0.2`, 25.8 GB, 8192-token context.

## Which entity a claim is about: `camera_entities.json`

Pixels cannot say "this is Building 14", and Kenil's fusion agent only resolves names (id / name / aliases); it does
not place entities by location, and an unknown name becomes a new node. So each camera is mapped to what it is
watching, per entity type:

```json
"drone-1":   {"building": "Building-14", "road": "Main Street"},
"drone-2":   {"road": "Road-Bridge"},
"roadcam-3": {"road": "Road-River", "report_open": true}
```

A frame's `entities` form field (replay `--entities '{"road": "Main Street"}'`) overrides the map for that request.
Hazards with no mapped entity (or no entity type, like wildfire smoke) stay local and are counted as
`claims_unmapped`. `POST /v1/vision/reset` reloads the file. Names must exist in the seeded graph
(`Shresth/rescuegrid/neo4j/schema/seed_static.cypher`); all four above resolve (checked read-only with Kenil's resolver:
`Main Street` -> `Road-Main`).

## API

| Method | Path | What |
|---|---|---|
| POST | `/v1/vision/frame` | multipart `file` + `source_id`, `source_type` (drone/roadcam), optional `ts` (ISO, frame time), `frame_ref`, `force_vlm`, `entities` (JSON). Returns the tier-1 event immediately, including `gate.decision` and v1's `keyframe_reason` |
| GET | `/v1/vision/claims?source_id=` | contract events produced, with the bus reply (`dry_run` when no bus) |
| GET | `/v1/vision/events?tier=1\|2&source_id=&since=` | raw vision events |
| GET | `/v1/vision/frames/<file>` | evidence frame behind a `raw_evidence_ref` (`vision/frames/<file>`) |
| GET | `/v1/vision/stats` | tier-1/tier-2 latency, Qwen calls per frame, gate triggers, claims, trigger-to-claim latency, active hazards |
| POST | `/v1/vision/reset` | forget a camera's hazard memory (form `source_id`, or all) between rehearsals |
| GET | `/health` | class lists, bus target, camera map |

Gate decisions on each tier-1 event: `no_hazard`, `persisting` (hazard seen, not yet held 3 frames),
`trigger:new:<kind>`, `trigger:reassess:<kind>`, `trigger:forced`, `already_assessed`, `report_open`.
Only one Qwen request runs at a time; a trigger that arrives while one is waiting for the same camera is merged into
it (never dropped, never queued twice). Events and claims are kept in memory (last 5,000 / 2,000) and reset on restart;
the graph is the record.

## Contract event

```json
{"source": "drone_vision", "timestamp": "2026-09-25T14:00:18Z", "confidence": 0.926,
 "entity": "Building-14", "claim": "collapsed", "raw_evidence_ref": "vision/frames/drone-1_1790287247302_3.jpg",
 "event_id": "vis-42069cc87d30379e",
 "details": {"camera": "drone-1", "camera_type": "drone", "entity_type": "building",
             "evidence_frames": ["vision/frames/drone-1_..._0.jpg", "...", "..._3.jpg"], "raw_bytes": 721157,
             "hazard": ["collapsed_building"], "detector_conf": 0.926, "trigger": "trigger:new:collapsed_building",
             "vlm": {"description": "...", "hazards": [{"type": "structural collapse", "severity": 5, "confidence": 1.0}],
                     "road_passable": false, "people_visible": true},
             "vlm_error": null, "vision_event_id": "..."}}
```

`timestamp` is the frame where the hazard was confirmed (scenario time when the adapter sends `ts`), not when Qwen
answered. `event_id` is stable per camera+entity+claim+time, so re-posting is deduplicated by the fusion agent.
`raw_bytes` is the frames the claim summarises, for Shresth's bandwidth-avoided metric. Road cameras also use
`source: drone_vision` (the scenario's convention; the contract has no roadcam source), with `details.camera_type`.

## Tuning (env)

`GATE_SCENE_CONF` 0.5, `GATE_OBJ_CONF` 0.35, `GATE_PERSIST` 3, `GATE_CLEAR` 5, `REASSESS_SEC` 60, `BUFFER_SEC` 5,
`BUFFER_MAX` 32, `VLM_FRAMES` 4, `OPEN_CONF` 0.6, `VLM_MAX_SIDE` 640, `VLM_MAX_TOKENS` 120, `SCENE_MODEL` ViT-L/14,
`YOLO_CONF` 0.20, `CAMERA_MAP`, `FRAME_DIR`, `EVIDENCE_PREFIX`, `ZRT_URL`, `VLM_MODEL`, `RESCUEGRID_BUS`.

Known trade-off: the gate requires 3 *consecutive* frames, so when CLIP flip-flops between two related hazards
(Building 14 clip: blocked_road / collapsed_building for the first 2 s) confirmation waits until one settles
(3.0 s here). A "3 of the last 4 frames" rule would confirm ~0.5 s sooner and would also emit `Main Street blocked`
from that clip; left strict until the team decides it wants that.

## Callers that send isolated stills

`Shresth/rescuegrid/scenario/replay.py` sends one still per scenario drone event with `force_vlm=true` and posts its
own scripted claim. v2 answers it like v1 (tier-1 result + a Qwen assessment) but posts **no** claim: only hazards the
gate confirmed (3 frames in a row, frames at most `BUFFER_SEC` apart) become bus events, so stills sent seconds apart
(or across rehearsals) never do. Verified: a forced still produced a tier-2 event and 0 bus events.

`Shresth/rescuegrid/tests/hard_cases.py` case 36 (same frame 5x -> keyframe on the 1st frame) encodes v1 behaviour. On
v2 the trigger comes on the 3rd frame (persistence), still exactly one Qwen call; the case needs updating, not the gate.

## What is in the shared Neo4j from vision so far

Only two test claims, from the PixVerse clips on 2026-09-24 (the Pexels clips were tested in dry-run mode):

| Event | Claim | Fusion result |
|---|---|---|
| `vis-42069cc87d30379e` | `Building-14 collapsed` @ 14:00:18 | `confirm`, applied (drone_vision recorded as confirming source, `last_confirmed` 14:00:18) |
| `vis-9424192de50fad8f` | `Main Street blocked` @ 14:00:34 | `stale`, not applied: the graph already held newer Main Street evidence (14:04:25) from an earlier test run. Recorded for audit, `ABOUT` Road-Main |

Both Event nodes point at v2's evidence frames. After a graph reset and a full scenario run these would apply normally.

## Open items

1. **Scenario integration: DONE 2026-09-25 (Shresth).** `Shresth/rescuegrid/scenario/replay.py` now plays the four
   clips through this service with `replay.py --ts-start <event ts> --speed <replay speed>` (scenario.json
   `details.media` = the clip, `evt-0013` added at t=32 s for `Main Street blocked`); v2 posts the claims, the scripted
   ones are only the expected result (`--scripted-vision` restores the old behaviour). Rehearsed twice in real time:
   all four claims landed (Building-14 collapsed 0.93 override, Main Street blocked 0.98 confirm, Road-River open 0.99,
   Road-Bridge blocked 0.96 conflict vs radio). The plan that was here, for the record:
   - point the scenario's drone/camera events at the four clips above (`details.media`), and add a drone_vision
     `Main Street blocked` event at t=32 s if vision should be the second source there;
   - for those events, play the clip through v2 (`replay.py --source-id <camera> --ts-start <event timestamp>`)
     instead of posting the scripted claim;
   - keep radio, sensor, GPS and field-report events as they are.
2. **"Rescue Team 4 near Building 14" (t=45 s, evt-0005).** Vision can see responders but cannot tell which team
   they are, so v2 never emits `near`. Proposed: keep this event scripted, backed by the GPS-derived NEAR edge. Needs
   the team's OK.
3. **`hard_cases.py` case 36**: updated for v2 on 2026-09-25 (gate decisions persisting, persisting, trigger, already_assessed x2; one Qwen call). Passes.
4. **Not yet measured:** vision latency with Kenil's Q&A model busy on the GPU, and with the WAN actually cut.
5. **One Qwen request at a time.** If three cameras trigger together their assessments queue (~6 s worst case), and
   each hazard claim is posted only after its Qwen assessment, so the last one lands ~6 s after confirmation.
   Road-camera `open` reports do not wait (no Qwen call). Tier-1 results per frame are never delayed.
6. **`frames/` grows** with every trigger (~0.1-0.4 MB per frame, 21 files / 6.7 MB after testing). Clear it between
   rehearsals if needed, but frames referenced by graph events are what provenance click-through opens.

## Files

| File | What |
|---|---|
| `server.py` | the service (tier 1, gate, Qwen, claims, API) |
| `camera_entities.json` | camera -> entity map |
| `replay.py` | replay adapter for drones and road cameras (video file or frame folder) |
| `bench_qwen.py` | Qwen input-mode benchmark; `bench_qwen_results_2026-09-24.jsonl` is its raw output |
| `weights/yolov8m-worldv2.pt` | YOLO-World weights (copied from v1) |
| `frames/` | evidence frames behind `raw_evidence_ref` |
| `vision.log` | service log |

## Test log (2026-09-24)

- Qwen input-mode benchmark on both PixVerse clips, with and without the tier-1 hint (table above).
- v1 vs v2 on the PixVerse clips: 11+ vs 2 Qwen calls; v2 claims correct.
- All four demo clips through v2 in real time (dry run): four correct claims, 3 Qwen calls in 120 frames.
- Claims validated against Kenil's `Event` model; entity names resolved read-only with his `EntityResolver`.
- Live write through the bus to the shared Neo4j (see above); evidence frames served over HTTP (200).
- Scenario runner's call shape (single still, `force_vlm=true`): tier-2 event, 0 bus events.
- Qwen prompt variant ("name a hazard only if clearly visible"): no real improvement, not adopted.
