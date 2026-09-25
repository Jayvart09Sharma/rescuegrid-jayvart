# RescueGrid · Command Twin (frontend)

The single frontend for RescueGrid: 3D digital twin, feed panel, event timeline, commander Q&A,
provenance click-through, cloud/network status, and a Sim Director for rehearsing the demo.
Owner: Pranay (see `04-pranay.md`).

```bash
npm install
npm run dev          # http://localhost:5173 (or: npx vite --port 5190 if 5173 is taken)
npm run build        # type-check + production bundle
```

> **Honesty line for judges:** in replay mode, feed *inputs* are replayed from the scenario
> timeline and drone frames are generated placeholders. The twin only renders graph state.

## Mesh twin (default since 2026-09-25)

The twin is now a **reconstructed 3D city** (`src/scene/MeshCity.tsx`): lit, shadowed meshes under an afternoon
sky. Everything starts as a cyan blueprint grid. After the page loads, reveal waves spread from the drones' launch
points (Staging Area A for Drones 1 and 2, Drone 3's pad; read from `DRONE_MOTIONS`) over about 17 s and rebuild it
in full colour: buildings rise floor by floor behind a scan band, the ground turns
into streets with lane markings, sidewalks, lots, parks and fields, and the river, bridges, trees and parked cars
appear. Drone coverage reveals areas ahead of the wave.

The graph's city (±50, every entity the backend knows) sits inside a **scenery ring** of suburbs out to ±110:
houses, small shops, parks, streets and cars. That ring is decoration only: not graph entities, not clickable, never
coloured by status.

It uses the same inputs as the particle twin (seed geometry, drone coverage map, graph status texture), so seeking,
status colours, collapse and fly-to highlights behave the same:
- A flagged but unscanned building shows as a red or yellow blueprint.
- Status shows as glowing edges in the fixed palette.
- A Q&A highlight shows as cyan edges.
- Building-14's collapse squashes and tilts it, drops rubble chunks and raises a dust cloud.

No new dependencies (three.js, drei `Sky`) and nothing loaded from the network, so it works with zero WAN.

- `?twin=particles`: the original point-cloud twin, unchanged.
- `?reveal=all`: show the whole city rebuilt immediately, with no load wave.
- The side-rail panels follow the same transition: after load each fills from see-through glass to a solid colour,
  sweeping left to right one panel after another (`.hud.twin-mesh` rules at the end of `hud/hud.css`).
  Particle mode keeps the old glass panels.
- The previous `Scene.tsx` and `Overlays.tsx` are backed up in `backup-2026-09-25-particle-twin/`.
- Tunables in `MeshCity.tsx`: `SUN` (light direction), `OUTER` (ring size), `WAVE_DELAY` / `WAVE_SPEED` / `WAVE_BAND` (load reveal), `BUILD_SEC` (how long a building takes to rise). Bloom, fog and sky colours are in `Scene.tsx` (mesh branch).
- The first mesh version is also backed up as `backup-2026-09-25-particle-twin/MeshCity.v1.tsx`.

## What it looks like, and why

The twin is a **drone-scanned particle reconstruction**, not a pre-built city model.

- **Drone scanning:** every building, road and patch of ground is GPU particles. Until a drone's
  camera footprint passes over an area, its particles are blurred, scattered and dark. As a
  drone sweeps, they snap into focus with a lock-in flash.
- **Projection:** each drone's view cone streams its camera frame down into the scene as colored
  points. This uses placeholder frames until Aditya's footage lands at `public/evidence/<raw_evidence_ref>`.
- **State colors:** these are fixed by the brief and layered on top of the particles.
  - green = normal
  - yellow = warning, or conflicting reports
  - red = danger/blocked
  - blue = responder/safe route
- **Radio and sensor facts show without drone coverage.** Main St can go red from a radio call
  before any drone has flown over it. That's the fusion story, made visible.

Demo beats (all can be triggered from **Sim Director**):

| Beat | What happens |
| --- | --- |
| QUAKE | Shockwave rings sweep the city, the screen shakes, an alert fires |
| COLLAPSE | Building-14's particles fall, bulge out and settle into a red rubble heap |
| ROAD BLOCK | Main St segment turns red with barriers; radio rings, then a drone lock-on confirms it |
| REROUTE | Blue dashed detour animates. **Suggested, commander approval required.** Ambulance 2 holds until IC clicks Approve |
| GAS HAZARD | Particle gas plume grows from the sensor; Rescue Team 4 gets an AT RISK ring |
| FLY-TO Q&A | "Who's in the most danger?" gives a typed answer plus Cypher, then the camera flies to the unit and beacons light up |
| CONFLICT | Oak Ave bridge flashes yellow: radio says open, drone sees debris, not auto-resolved |
| Network | NORMAL / THROTTLED (kbps slider) / ZERO WAN: cloud tiles lag or go dark, Nano path stays up |

**Autopilot** plays the whole scenario and asks the scripted questions on cue. It never approves a
suggestion; that stays a human click (Rule 3).

Click any timeline row, building or unit to open **provenance**:
- source, timestamp, confidence, `raw_evidence_ref`
- the evidence itself: drone frame with detection box, radio waveform (plays the transcript via
  on-device TTS), or sensor trace
- which graph nodes that event wrote

## Architecture rules this code keeps

1. **Neo4j is the only source of truth.** `src/store.ts` holds a read-only mirror of the graph
   (`nodes`/`edges` with `since`, `lastConfirmed`, `sources[]`). It changes only through
   `GraphPatch`es from a source. The 3D scene reads the store and never feeds facts back.
   Q&A answers query the graph mirror, never the scene.
2. **Every fact carries time and provenance.** Each patch applied from an event attaches a
   `Provenance` (event id, source, timestamp, confidence, evidence ref) to the nodes it touched.
3. **Humans stay in control.** Suggestions render as "suggested" until approved.

## Sources: replay vs. live (same adapter pattern as the backend)

`src/data/engine.ts` picks one at startup:

**Start screen (live mode).** Nothing runs until START INCIDENT: the screen is for staging. Drone videos are
uploaded and radio transmissions are queued (gateway `POST /radio/hold` + `POST /radio/queue`, played in order with a
first delay and a gap, server side, each through ASR -> LLM -> graph when its turn comes), then the popup closes.
On connecting to the gateway the twin opens this screen (also behind the STREAMS button in the top bar) with one row per drone: tick the drones that fly and give each its **video** (played into the
Nano's vision service as that camera, frame by frame, looping, with the building/road it watches taken from
`camera_entities.json` or typed in). Only ticked drones exist in the twin: a drone appears when its stream starts and
is removed when it stops. The drone moves the way its footage moves (the vision service returns a phase-correlation
camera-motion estimate per frame; the twin integrates it from the entity the camera watches and re-anchors when the
clip loops). The DRONE CAMERA panel loops the uploaded video with the detector's boxes over it. No road cameras in the
twin (they stay backend-only). Also: **run the rehearsal scenario**, switch to **simulation**, or **reset the incident**.
Running streams are listed with their frame count and can be stopped. Gateway routes: `POST /streams` (multipart file, camera, building, road, fps,
speed, loop), `GET /streams`, `DELETE /streams/{id}`, `POST /scenario/start`, `POST /reset`, `GET /entities`.

Mode is chosen at **runtime**: the top bar has a LIVE / SIMULATION toggle. At startup the app probes the gateway
(`/health`) and goes live if it answers, simulation otherwise. One build serves both.

- **Simulation** (`replay`): `src/data/replaySource.ts` plays `src/data/scenario.ts` off one clock and
  supports seeking (Sim Director). It also stands in for the GPS feed. `src/data/cloudSim.ts` emulates cloud
  latency from the network state. This is the only scripted path, and it is labelled as such on screen.
- **Live**: set env vars at build time and `src/data/liveSource.ts` takes over. Everything goes through
  Shresth's gateway (`Shresth/rescuegrid/bus/gateway.py`, port 8097), which also serves this build, so the
  values are **relative** and one build works from the Nano or from a laptop over an SSH tunnel:

```bash
# build the live twin next to the replay one (dist/ stays the offline fallback)
export VITE_RG_LIVE_URL=/stream VITE_RG_QA_URL=/qa VITE_RG_NETSIM_URL=/netsim VITE_RG_SCENARIO_START=2026-09-25T14:00:00Z
node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build --outDir dist-live
# (node is at ~/.local/node/bin on the Nano; `npm run build` works too once that is on PATH)

# serve: cd Shresth/rescuegrid/bus && ./gateway.sh      -> http://127.0.0.1:8097/
# from a laptop: ssh -L 8097:127.0.0.1:8097 hp2@<nano> ; open http://localhost:8097/
```

  What the gateway does for this app (see its docstring): translates graph ids to this app's ids
  (`Road-Main` → `Main-St-5`, `Team-Rescue4` → `Rescue-Team-4`, `Facility-CountyGeneral` → `Hospital-Valley`,
  `Road-Bridge` → `Oak-Bridge`, `Hazard-gas-Sensor-Gas3` → `Hazard-Gas-1`...), maps lat/lon onto this grid
  with an affine fit through Building 14 / Lincoln HS / County General, sends every bus event with the Neo4j
  diff it caused as `patches`, proxies Kenil's `/qa` into a `FlyToSignal`, holds the network state
  (forwarding to Jayvant's simulator when it is up), records IC decisions on suggestions through the bus,
  and serves evidence frames at `/evidence/<raw_evidence_ref>`. In live mode nothing is scripted: reroute
  suggestions carry `routeIds` from the graph's own Cypher route and the twin draws the polyline from its geometry;
  the drones hover over whatever `camera_entities.json` says each camera watches (orbiting while frames arrive);
  the DRONE CAMERA panel shows the real frame the vision service just analysed, with its YOLO-World boxes and CLIP
  scene label. The Sim Director is hidden in live mode: the scenario clock comes from the backend replay.

## Contracts (agree these with the owners, see `src/types.ts`)

**Event (Shresth).** This is the brief's shape, verbatim:
```json
{ "source": "drone_vision|radio_asr|gps|sensor|field_report", "timestamp": "ISO 8601",
  "confidence": 0.94, "entity": "Building-14", "claim": "collapsed",
  "raw_evidence_ref": "frames/drone-1/f000450.jpg" }
```

**Live socket messages (gateway → twin):**
```
{type:'seed', nodes, edges}                      graph entities, merged over the seed city; sources = audit trail
{type:'event', event, patches?, silent?}         a bus event + the Neo4j diff it caused (silent = history on connect)
{type:'patch', patches} · {type:'clock', t, speed?} · {type:'suggestion', suggestion, silent?}
{type:'network', status} · {type:'flyto', signal}
```

**Fly-to signal (Kenil):**
```json
{ "target": "Rescue-Team-4", "highlight": ["Rescue-Team-4","Hazard-Gas-1"],
  "answer": "plain-language answer", "cypher": "MATCH ... (optional, shown to the officer)" }
```

**Network status (Jayvant):**
```json
{ "state": "normal|throttled|disconnected", "kbps": 256, "latencyMs": 3200 }
```

**Suggestion decision** (frontend → backend): `POST /suggestions/:id {decision:'approved'|'dismissed'}` on the
gateway; an approval is recorded in the graph as a field report from the IC (Ambulance 2 → en_route).

## Layout

```
src/
  types.ts            contracts
  store.ts            graph mirror + UI state (zustand)
  data/               seed city, scenario timeline, replay/live sources, Q&A fallback, frame generator
  scene/              ParticleCity (point-cloud shader), Drones (cones + frame projection),
                      Markers (units/facilities/sensors), Overlays (hazards, roads, routes, pings),
                      coverage (drone scan map), Scene (camera rig, bloom, shake)
  hud/                TopBar, LeftRail, RightRail, Bottom (timeline + Sim Director), Overlays
```

Dev handle for scripted checks: `window.__rg.engine.seek(90)`, `window.__rg.engine.ask("…")`.
