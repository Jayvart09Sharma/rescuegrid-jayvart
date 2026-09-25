# RescueGrid graph data model (Kenil's reasoning layer, v0.1)

Neo4j is the single source of truth. Every fact carries **when it became true**, **when it was
last confirmed**, and **provenance** (source, confidence, raw evidence reference). The 3D twin
renders this graph; Q&A queries it; nobody re-derives facts from the rendering.

## Node labels

Every entity node has two labels: `:Entity` (for cross-type lookup and name resolution) plus one
type label.

| Label | `kind` | Type-specific properties | Status vocabulary |
|---|---|---|---|
| `Building` | building | `building_type`, `floors`, `occupancy_est` | intact, damaged, collapsed, unknown |
| `Road` | road | `lanes` | open, restricted, blocked |
| `Team` | team | `unit_type` (rescue, ambulance, fire, police), `callsign`, `personnel`, `position_since` | available, en_route, on_scene, out_of_service |
| `Hazard` | hazard | `hazard_type` (gas, seismic, flood, fire), `level`, `radius_m` | active, cleared |
| `Sensor` | sensor | `sensor_type` (gas, seismic, water), `unit`, `threshold`, `reading` | normal, spike, offline |
| `Facility` | facility | `facility_type` (hospital, shelter), `capacity`, `beds_available` | open, full, closed |
| `Event` | – | see below | – |

### Fields on every entity node

| Property | Type | Meaning |
|---|---|---|
| `id` | string, unique | Stable id, e.g. `Building-14`, `Road-Main`, `Team-Ambulance2` |
| `name`, `aliases` | string, list | Display name and the spoken/written variants the fusion agent resolves (`"Main Street"` -> `Road-Main`) |
| `lat`, `lon`, `location` | float, point | Position; `location` is a Neo4j point for distance queries and the point index |
| `status` | string | The current claim about the entity |
| `status_since` | datetime | **When the current status became true** (timestamp of the event that set it) |
| `last_confirmed` | datetime | **Last time the current status was (re)confirmed** by any event |
| `source` | string | Provenance of the current status: `drone_vision`, `radio_asr`, `gps`, `sensor`, `field_report`, `seed_dataset` |
| `confidence` | float 0..1 | Confidence of the current status |
| `raw_evidence_ref` | string | The frame / clip / message id behind the current status (drives the provenance click-through) |
| `conflict`, `conflict_claim`, `conflict_source`, `conflict_confidence`, `conflict_evidence_ref`, `conflict_since` | mixed | Set when a second source contradicts the current status within the conflict window. Both sides stay visible; nothing is silently picked. |
| `seeded`, `created_at`, `updated_at` | bool, datetime | Bookkeeping |

`Team` nodes additionally carry `position_since` (when the current lat/lon was reported) and
`position_source` / `position_evidence_ref`, because a unit's position and its status are
different facts with different provenance.

### `Event` nodes (the audit trail)

One `Event` per ingested payload, linked `(:Event)-[:ABOUT]->(:Entity)`. Properties: `id`,
`source`, `timestamp`, `confidence`, `entity` (as received), `entity_id` (as resolved), `claim`,
`raw_evidence_ref`, `details` (JSON string of any extra payload), `ingested_at`, `applied`
(bool: did it change state?), `note` (why not, e.g. `stale`, `conflict`). "What changed in the last
5 minutes?" is a query over `Event.timestamp`.

## Relationships

| Relationship | Meaning | Properties |
|---|---|---|
| `(:Building\|:Facility)-[:ON_ROAD]->(:Road)` | static topology | `source` |
| `(:Road)-[:CONNECTS_TO]->(:Road)` | road graph (undirected semantics) | `length_m`, `source` |
| `(:Sensor)-[:MONITORS]->(:Entity)` | what a sensor watches | `source` |
| `(:Team)-[:STAGED_AT]->(:Facility)` | seed staging position | `since`, `source` |
| `(:Event)-[:ABOUT]->(:Entity)` | provenance history | – |
| `(:Team)-[:NEAR]->(:Entity)` | **fused** proximity (GPS distance and/or vision "near") | `since`, `last_confirmed`, `distance_m`, `sources` (list), `confidence` (noisy-OR of sources), `evidence_refs` (list), `event_ids` (list), `active`, `ended_at` |
| `(:Hazard)-[:AFFECTS]->(:Entity)` | hazard zone touches a building/road | `since`, `last_confirmed`, `source`, `confidence`, `evidence_refs` |
| `(:Hazard)-[:DETECTED_BY]->(:Sensor)` | which sensor raised it | `since`, `source` |
| `(:Team)-[:ASSIGNED_TO]->(:Entity)` | unit tasked to an entity (from field reports) | `since`, `last_confirmed`, `source`, `confidence`, `evidence_refs` |
| `(:Event)-[:CONFLICTS_WITH]->(:Event)` | two events disagree about one entity | `detected_at` |

## Temporal semantics

* Ingesting an event whose `claim` equals the current `status` only bumps `last_confirmed`
  (and keeps the higher confidence). It does not move `status_since`.
* A different claim newer than the latest evidence we hold (`last_confirmed`) becomes the new status and
  sets `status_since = event.timestamp`. Anything older than `last_confirmed` (out-of-order reports,
  late confirmations, late GPS fixes, late vision "near" frames) is recorded as an `Event` node with
  `applied=false, note='stale'` and never overwrites newer state. Positions and `NEAR` edges are gated
  the same way (`position_since`, `NEAR.last_confirmed`).
* Each event is applied in one Neo4j transaction. If a rule fails, the whole event rolls back and an
  audit-only `Event` with `action='error'` is written. Payloads that fail validation are reported by the
  agent (`action='invalid'`) without stopping the stream; ambiguous spoken names (`"Ambulance"` when two
  ambulances exist) are recorded with `action='ambiguous'` and applied to nobody.
* Auto-created placeholders (`status='unknown'`) and the static seed are priors, not evidence: the first
  real report simply overrides them, never conflicts with them.
* A `NEAR` / `AFFECTS` / `ASSIGNED_TO` edge that was ended and comes back starts a fresh epoch
  (`since` = the new event, sources/evidence/confidence restart, `epochs` counts).
* A contradicting claim from a *different source* inside the conflict window (default 5 min) with
  confidence within 0.25 of the current one is a **conflict**: the current status stays, the
  competing claim is stored in the `conflict_*` fields, both events are linked `CONFLICTS_WITH`, and
  the Q&A layer reports "conflicting reports" with both provenances. A later event from any source
  that matches one side resolves it.
* Scenario time vs wall-clock: replayed events carry scenario timestamps. Queries that say "now"
  use `max(Event.timestamp)` (the replay clock) unless `RESCUEGRID_CLOCK=wall`.

## Why `:Entity` on everything

One label to index names/aliases (fulltext index `entity_search`), one label for "everything
that changed since T", and one point index for "what is within 75 m of this GPS fix" regardless
of type. Type labels keep the per-type uniqueness constraints and make Cypher readable.
