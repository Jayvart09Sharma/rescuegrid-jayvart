# RescueGrid — Master Plan
HP Edge AI SJSUHack · Team: Shresth, Aditya, Kenil, Pranay, Jayvant
Full project brief (share with judges too): https://claude.ai/artifact/VWdEJgZ6Pt3uAtiJMQWc9Z

## The one-sentence pitch
RescueGrid fuses drone video, radio, responder GPS, road cameras, and sensors into one live,
queryable Neo4j graph, rendered as a 3D command-post twin — running its primary inference on the
ZGX Nano so it keeps working even when the internet doesn't.

## Target user (locked — do not drift from this)
A **county EOC (Emergency Operations Center) watch officer**. Every screen, demo line, and piece
of copy uses their vocabulary — units, staging area, IC — not "emergency responders" generally.

## Architecture, in one picture
```
SIMULATED / REPLAYED FEEDS
        |
Drone | Radio | GPS | Sensors | Cameras
        |
   ZGX NANO INFERENCE
   (vision, ASR, local LLM, event/correlation agent)
        |
Structured events (timestamp + provenance)
        |
        v
      NEO4J  <-- single source of truth, temporal
      /   \
3D TWIN   COMMANDER Q&A
(render)  (queries Neo4j directly, never the twin)
        |
  OPTIONAL CLOUD (context only: satellite, weather, GIS)
```

Three rules that keep this coherent, don't violate them without a team discussion:
1. **Neo4j is the only source of truth.** The 3D twin renders the graph; it never gets inspected
   to rediscover facts. Q&A queries the graph + recent events directly.
2. **Every fact carries a timestamp and a source.** "What changed in the last 5 minutes?" only
   works if time and provenance are first-class fields, not bolted on later.
3. **Humans stay in control.** RescueGrid suggests and explains. It never auto-dispatches. A
   reroute is shown "suggested — commander approval required," full stop.

## The build order (this is what protects us on Friday)
1. **Must ship:** structured events (with timestamp + basic provenance) feeding Neo4j, with the
   internet-kill demo proven live. This alone satisfies Rules 3 and 5 completely.
2. **Should ship:** the 3D twin reacting live, plus click-an-event-for-provenance (source,
   timestamp, confidence, the actual drone frame/audio clip).
3. **Stretch:** full natural-language Q&A with camera-fly-to-answer (keep a scripted fallback of
   pre-baked queries that definitely work), and conflict handling when two sources disagree about
   the same fact (e.g. radio says a bridge is open, drone shows it blocked). Both are strong —
   neither should block layer 1 or 2.

If Friday goes sideways, we fall back to a version that still scores well on 3 of our 5 rules,
not a half-built version of everything.

## Why local, not cloud (the pitch line to memorize)
Not "the internet might be down." It's: **even with a working connection, continuously streaming
multi-feed disaster data to the cloud is the wrong architecture** — for bandwidth, for latency
that degrades exactly when demand spikes, and for data sensitivity. When connectivity genuinely
disappears, the same architecture becomes essential instead of merely efficient. The demo proves
this twice: once throttled (a degraded, shared satellite-style link — cloud lags, Nano doesn't),
once fully disconnected.

## Data sources — real vs. simulated (everyone builds to this contract)
Every feed gets ONE ingestion interface with two interchangeable adapters behind it: a `live`
adapter (real hardware/API) and a `replay` adapter (scripted/pre-recorded, same interface, same
schedule). The Nano's models never know which one is feeding them.

| Source | Demo simulation | Interface your code exposes |
| --- | --- | --- |
| Drone video | Stock/CC disaster footage, played frame-by-frame on a timer | RTSP stream or timestamped frame folder |
| Radio | Scripted lines via local TTS, played as audio | Mic input / audio file feed to ASR |
| Responder GPS | Path-following script, fake lat/long on a timer | MQTT topic / REST endpoint |
| Road cameras | Stock traffic-cam footage, played on a timer | Same vision endpoint as drones |
| Sensors (seismic/water/gas) | Script spiking values at scripted timestamps | MQTT topic per sensor type |
| Hospitals/shelters | Static seed dataset loaded at startup | Neo4j nodes, seeded once, not streamed |
| Field reports | Pre-written snippets, sent as text at scripted times | Same text ingestion endpoint |
| Maps | **Not simulated** — real, free public data | — |

**One scenario file drives everything.** A single JSON/YAML timeline (`t=0 seismic event`,
`t=+15s drone shows Building 14 collapsed`, `t=+32s radio reports Main Street blocked`,
`t=+90s gas sensor spikes`...). Every replay adapter reads off that same clock. Shresth owns the
skeleton; everyone adds their own events to it.

## Ground rules — read this part
1. **SSH only into your own assigned Nano device.** Don't touch another team's node — you'll get
   flagged and it breaks the hack rules outright.
2. **Coordinate before running heavy jobs.** Multiple people running GPU-heavy training/serving
   on the Nano at the same time tanks everyone's performance. Post in the group chat before you
   kick off anything big (fine-tuning, serving a large model) so we're not stepping on each other.
3. **Model choice is a team decision, not a solo one.** Which vision model, which ASR model,
   which local LLM, what size/quantization to run — **check with Shresth before finalizing.**
   The Nano has 128GB unified memory shared across everyone's models; HP recommends ~70B FP4 for
   serving and ~12B BF16 for fine-tuning as a ceiling, and we're running several models
   concurrently (vision + ASR + LLM), so this needs one person tracking the total footprint.
4. **Build to the interface, not around it.** Whatever you're building, expose it as the
   `live`/`replay` adapter pattern above so Shresth can wire it into the shared event bus and
   scenario timeline without custom glue code per person.
5. **Use the hack's own tooling.** ZTK to connect, ZRT to serve models (`zrt serve <model>`,
   `zrt status`, `zrt models`) — don't hand-roll a different serving setup; it's what the
   benchmarks and support docs assume.
6. **Be upfront in the README and pitch.** State plainly that inputs are replayed for demo
   purposes while inference, fusion, and graph reasoning are 100% live and local. Judges respect
   the honesty; don't let it read like you're hiding it.

## Deliverables (due Fri Sept 25, 8pm) — don't lose sight of these while building
- Public GitHub repo: code, README, Dockerfile/setup script, with reasoning for choices
- Metrics/benchmarks (see each brief's "what to benchmark" section)
- 5-min demo (live if on the SJSU subnet, else pre-recorded video embedded)
- 2-min max video, uploaded to YouTube, public URL
- Pitch readiness for semi-final judges: one visual of architecture, one of benchmarks, one of
  impact; 3 min of Q&A
- Social posts throughout, tagging the sponsors listed in the hack brief

## Your individual brief
- `01-shresth.md` — Integration, backend, Neo4j, Nano deployment, benchmarking
- `02-aditya.md` — Vision pipeline (drone + road-camera damage/hazard detection)
- `03-kenil.md` — Reasoning layer: fusion/correlation agent, local-LLM Q&A layer, conflict-handling design (stretch)
- `04-pranay.md` — Speech/ASR + radio adapter, and the entire frontend (one app: 3D twin, dashboard, provenance UI)
- `05-jayvant.md` — Backend only: GPS/sensor/hospital/field-report adapters, plus the network-condition simulator behind the throttle/disconnect demo beat
