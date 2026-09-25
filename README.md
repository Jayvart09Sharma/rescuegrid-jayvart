# RescueGrid - reasoning layer (Kenil)

Fusion of raw perception events into a temporal, provenance-tagged Neo4j graph, plus natural-language
Q&A over that graph with a camera-fly-to signal for the 3D twin. Runs standalone on dummy data against a
private Neo4j; every external dependency (Neo4j, event feeds, LLM) is swappable through env vars or a
5-line adapter, so it can be pointed at the shared instances later without touching the write logic.

Honest scope note: the inputs here are hand-written replayed events; inference, fusion and graph reasoning are live and local.

```
events/*.json  --(EventSource)-->  FusionAgent  --(GraphStore)-->  Neo4j  <--(QAEngine)-- "Who is in the most danger?"
   replay adapter now,              status policy + rules            |            pre-baked Cypher (fallback)
   MQTT/stdin/bus later             NEAR / AFFECTS / ASSIGNED_TO      |            or LLM text->Cypher (guarded, read-only)
                                    conflicts surfaced, never picked   +--> QAResponse {answer, highlight{type,id,ids,lat,lon}, ...}
```

## Quick start

```bash
cd rescuegrid-reasoning
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # defaults: local Neo4j on 7687, local LLM on 127.0.0.1:8080

# Neo4j - pick ONE:
docker compose up -d                       # needs docker group / sudo; data in ./.neo4j
./scripts/neo4j_local.sh install && ./scripts/neo4j_local.sh start   # no docker/sudo: JDK 21 + Neo4j 5.26 inside ./.local

.venv/bin/python scripts/ingest.py --reset            # schema + static world + replay events/dummy_events.json
.venv/bin/python scripts/verify_graph.py              # human-readable dump: statuses, timestamps, provenance, relationships
.venv/bin/python scripts/ask.py "Can Ambulance 2 still reach the hospital?"
.venv/bin/python scripts/ask.py --demo                # every pre-baked question, fallback + LLM modes
.venv/bin/python scripts/serve_qa.py --port 8095      # POST /qa for the frontend (docs/QA_CONTRACT.md)
.venv/bin/python -m pytest                            # 95 tests; DB/LLM tests auto-skip when those are not reachable
```

Neo4j Browser: http://localhost:7474 (neo4j / rescuegrid). Useful views:

```cypher
MATCH (n:Entity) RETURN n.id, n.kind, n.status, n.status_since, n.last_confirmed, n.source, n.confidence, n.raw_evidence_ref, n.conflict ORDER BY n.kind, n.id;
MATCH (t:Team)-[r:NEAR|ASSIGNED_TO]->(x) RETURN t, r, x;                                   // fused relationships
MATCH (h:Hazard)-[r:AFFECTS|DETECTED_BY]->(x) RETURN h, r, x;
MATCH (e:Event)-[:ABOUT]->(n) RETURN e.timestamp, e.source, e.claim, n.id, e.applied, e.action, e.note ORDER BY e.timestamp;
MATCH (n:Entity) WHERE n.conflict RETURN n.name, n.status, n.source, n.confidence, n.conflict_claim, n.conflict_source, n.conflict_confidence;
MATCH (a)-[r]->(b) RETURN a, r, b;                                                          // whole graph (small)
```

## Layout

| Path | What |
|---|---|
| `docker-compose.yml`, `scripts/neo4j_local.sh` | the private Neo4j (step 1) |
| `schema/schema.cypher`, `schema/seed_static.cypher`, `docs/DATA_MODEL.md` | constraints, indexes, the static world, and the data model (step 2) |
| `events/dummy_events.json`, `docs/EVENT_CONTRACT.md` | 10 hand-written events in the draft payload shape + the optional `details` extension (step 3) |
| `rescuegrid/contracts.py` | `Event` (inbound) and `QAResponse` / `Highlight` (outbound) pydantic contracts |
| `rescuegrid/sources/` | pluggable `EventSource`s: JSON file / in-memory (replay), JSON-lines on stdin, MQTT (lazy paho import) |
| `rescuegrid/graph.py` | the only place Cypher writes live |
| `rescuegrid/fusion/` | `policy.py` (pure status/conflict decisions), `resolve.py` (spoken name -> entity), `rules.py` (correlations), `agent.py` (step 4) |
| `rescuegrid/qa/` | `fallback.py` (7 pre-baked intents), `text2cypher.py` (guarded LLM path), `engine.py`, LLM backends (steps 6-7) |
| `scripts/` | `ingest.py`, `verify_graph.py`, `ask.py`, `serve_qa.py`, `apply_schema.py` |
| `tests/` | policy unit tests, fusion integration, Cypher guard, Q&A fallback, Q&A LLM |

## How a raw event becomes graph state

1. `Event` validation (six draft fields required; `event_id` generated when absent; `details` optional).
2. Duplicate `event_id` -> skipped (safe for at-least-once buses).
3. Entity resolution: id -> name/alias (normalised) -> fulltext phrase -> create with an inferred label and `auto_created=true`.
4. Status policy (`fusion/policy.py`, pure): same claim -> **confirm** (`last_confirmed` moves, `status_since` does not; a live source takes provenance over from the seed); older than `status_since` -> **stale**, recorded but not applied; a different source contradicting within 5 min at comparable confidence -> **conflict**; otherwise **override** (`status_since` = event time, provenance = this event).
5. Correlation rules: GPS -> `NEAR` everything within 75 m (and closes `NEAR` the unit moved away from); vision `near` -> the *same* `NEAR` edge (sources accumulate, confidence = noisy-OR); sensor spike -> `Hazard` `AFFECTS` what the sensor `MONITORS`, `DETECTED_BY`, units inside the radius `NEAR` the hazard; field report -> `ASSIGNED_TO`.
6. An `Event` audit node linked `ABOUT` the entity, with `applied`/`action`/`note`.

Scenario result (`scripts/verify_graph.py`): Building 14 collapsed (drone 0.91, 14:00:15); Main Street blocked (radio 0.78);
Rescue Team 4 `NEAR` Building 14 with sources `[gps, drone_vision]` and confidence 0.991; gas hazard `AFFECTS` Building 14 and
Rescue Team 4 `NEAR` it; Bridge Street blocked (drone 0.85) **with a conflict** retained from radio "open" (0.70), both events linked `CONFLICTS_WITH`.

## Conflict handling (built, minimal)

Two sources disagreeing about one entity inside the window are never silently merged: the node's `status` follows the
higher-confidence report, the other report is kept in `conflict_*` fields with its own provenance, the two `Event` nodes are
linked `CONFLICTS_WITH`, and every Q&A answer that touches the entity says "conflicting reports" with both sides. A third
source confirming either side resolves it (`resolve_conflict` / `confirm_competing` in `policy.py`). Deferred: per-source
reliability weights, and a UI action for the watch officer to resolve a conflict by hand.

## Q&A

* `mode=fallback`: 7 pre-baked intents (regex -> Cypher -> templated answer) that never touch a model. Demo safety net.
* `mode=llm`: schema-aware prompt with few-shots -> one Cypher query -> read-only guard (allowlisted leading clause; no writes,
  admin/SHOW/USE, procedures other than fulltext, multiple statements, literals projected as facts like `'open' AS status`, or
  write-sounding aliases; forces `LIMIT`) -> runs in a read transaction -> up to 2 automatic repairs on errors, plus one on a
  0-row result -> answer composed by the model from the rows as JSON -> **fact-checked**: every source/time/confidence it quotes
  must come from one row, else one regeneration and a lowered confidence + warning -> highlight ids must appear in the rows.
  Empty or all-zero results are reported as "no matching records, not proof of absence", never as a confident negative. If the
  model fails outright, the pre-baked intent answers, or the rows are listed without the model.
* Read-only and humans-in-control are enforced before any query: "set/delete/ignore previous rules..." and "send/dispatch/
  reroute..." requests get a fixed refusal ("No changes were made" / "RescueGrid never dispatches").
* `mode=auto` (default): pre-baked first; LLM for anything else, and also when the pre-baked intent matched but could not
  resolve the entity (confidence < 0.6).
* Output contract: `docs/QA_CONTRACT.md`. Humans stay in control: routes/tasking come back as `suggestion` with
  `requires_commander_approval=true`.

LLM backends: any OpenAI-compatible endpoint (default: the ZGX box's ZRT/vLLM Nemotron-3-Nano-30B, reasoning off for speed) or
the Claude API (`LLM_PROVIDER=anthropic`, needs `ANTHROPIC_API_KEY`; written to the SDK reference, not yet exercised here).
Model choice for the shared Nano is a team decision - this layer does not care which model answers.

## Swapping to the shared infrastructure later

| Piece | Now | Later |
|---|---|---|
| Neo4j | `NEO4J_URI=bolt://127.0.0.1:7687` | change the URI/credentials in `.env`; if Shresth's schema uses other labels/ids, adapt `schema/*.cypher` + `resolve.py` id conventions |
| Event feeds | `JsonFileSource(events/dummy_events.json)` | `MqttSource(topics=[...])`, `JsonlStdinSource()`, or a new class with `__iter__` yielding `Event`s |
| Payload shape | draft six fields + optional `details` | if the final bus payload renames fields, map them in one place: `sources/base.py::coerce_event` |
| LLM | local vLLM via `LLM_BASE_URL` | any OpenAI-compatible URL/model, or `LLM_PROVIDER=anthropic` |
| Frontend | `POST /qa` on 8095 | same contract; add fields, never rename |
