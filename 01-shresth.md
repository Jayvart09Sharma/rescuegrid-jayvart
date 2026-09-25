# RescueGrid — Shresth's brief
Role: Integration &amp; backend lead
See `00-master-plan.md` for the full architecture and ground rules first.

## Your mission
Everyone else builds a piece. You're the one who makes sure the pieces actually fit together on
Friday — the event bus, the graph, the Nano itself, and the numbers that prove it worked.

## What you own

### 1. ZGX Nano setup (do this first, day one)
- Get everyone SSH'd in via ZTK, confirm the ZRT runtime is installed (`zrt status`).
- Set up the shared project structure on the Nano so Aditya/Kenil/Pranay/Jayvant's code has a
  consistent place to live (agree on a repo layout day one, don't let it drift).
- Own `HF_TOKEN` / `HF_REPO_ID` setup if anyone needs gated Hugging Face models.
- **Track total model footprint.** The Nano has 128GB unified memory shared across everyone's
  models running at once (vision + ASR + LLM, possibly concurrently). Before anyone finalizes a
  model choice, get their expected size/quantization and keep a running tally so we don't find
  out we're over budget the night before the demo.

### 2. The event bus / API contracts
Every teammate's adapter (live or replay) needs to hand off structured events the same way.
Define this contract on day one and circulate it — don't let four people invent four different
formats. Suggested shape (adjust with the team, but pin it down early):
```json
{
  "source": "drone_vision | radio_asr | gps | sensor | field_report",
  "timestamp": "ISO 8601",
  "confidence": 0.0,
  "entity": "Building-14",
  "claim": "collapsed",
  "raw_evidence_ref": "path or id to the frame/clip that produced this"
}
```
This `raw_evidence_ref` field is what makes Kenil's provenance click-through possible later —
don't let people drop it to save time now.

### 3. Neo4j — schema, deployment, and the temporal + provenance layer
- Stand up Neo4j on the Nano (or wherever the team agrees), own the schema.
- Every node/edge needs: when it became true, when it was last confirmed, and which source(s)
  it came from. This is what makes "what changed in the last 5 minutes?" actually answerable —
  work with Pranay on this since he's writing the code that populates it.
- Neo4j is the **only** source of truth — no other component should hold state that isn't also
  in the graph. If you catch a component caching its own copy of "current state," flag it.

### 4. The master scenario timeline
Own the skeleton of the single JSON/YAML file that drives every replay adapter (see
`00-master-plan.md` for the format). Everyone else adds their own events to it, but you own the
clock and make sure timestamps across sources actually tell one coherent story (e.g. the gas
sensor spike should make sense relative to when the drone spots the collapsed building).

### 5. Benchmarking
Own collecting these live during rehearsal, not scrambled together the night before:
- Bandwidth avoided (raw feed volume vs. structured events actually sent anywhere)
- P95 event-to-graph-update latency
- Query response latency
- % of events resolved fully on-device
- Cloud escalation rate (how often the optional cloud path was actually used)
- Response time under three conditions: normal / throttled / zero WAN

### 6. Demo rehearsal coordination
You're the one running point on making sure the 9-step demo script (see the master brief) works
end to end at least twice before Friday, with everyone's piece plugged in together, not just
tested in isolation.

## Model-choice check-ins
You're the person everyone else checks in *with* — but also flag anything where you're unsure
whether a choice affects total resource budget, and loop in the human team lead before locking it
in, especially anything that changes total Nano memory footprint.

## Definition of done
- [x] Everyone SSH'd in, ZRT confirmed working (`zrt status`: `llm` Nemotron-3-Nano-30B FP8 + `vision` Qwen3-VL-8B FP8; ASR outside ZRT on :8090)
- [x] Event contract written down and shared, everyone building to it (`scenario/rescuegrid_events.py`, six fields + `event_id`/`details`; validated by Kenil's `Event` model at the bus)
- [x] Neo4j schema live with timestamp + provenance fields (`neo4j/schema/`, shared instance :7688, `status_since` / `last_confirmed` / `source` / `raw_evidence_ref` / `conflict_*` on every entity, `Event` audit nodes)
- [x] Master scenario timeline skeleton exists, others can add to it (`scenario/scenario.json`, 13 events; Aditya's four clips wired in on 2026-09-25)
- [x] Benchmarks collected from at least one full rehearsal run (see `scenario/README.md`, "Measured 2026-09-25")
- [x] Full demo run end to end at least twice (2026-09-25: two real-time runs of all five adapters + live vision, hard-case suite in between; the frontend beat is the live twin behind `bus/gateway.py`)

## Status 2026-09-25 (backend integrated; what is left)
- Left for the team: Pranay's radio TTS → ASR (:8090) live adapter (radio claims are still scripted through `scenario/replay.py`);
  Jayvant's network simulator must actually shape the WAN (today it only sleeps on `/cloud/test`) before the
  normal / throttled / zero-WAN response-time row can be measured honestly; Kenil's four open Q&A/policy items
  (hard cases 15, 27, 28, 30). Frontend: open the live twin (`bus/gateway.sh`) and check each beat by eye.
