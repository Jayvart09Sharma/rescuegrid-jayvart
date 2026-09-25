# Event contract accepted by the fusion agent (v0.1)

This is the draft shared payload from the master plan, unchanged, plus two OPTIONAL fields the
fusion agent understands. Producers that only send the six draft fields are fully supported.

```json
{
  "source": "drone_vision | radio_asr | gps | sensor | field_report",
  "timestamp": "2026-09-25T14:00:15Z",
  "confidence": 0.91,
  "entity": "Building-14",
  "claim": "collapsed",
  "raw_evidence_ref": "drone/cam1/frame_000450.jpg",

  "event_id": "optional - generated (sha1 of source+timestamp+entity+claim+raw_evidence_ref) when absent; a repeated id is skipped as a duplicate",
  "details": { "optional free-form payload, see claims below" }
}
```

`entity` may be an id (`Building-14`) or a spoken name (`"Main Street"`, `"Rescue Team 4"`); the agent
resolves it via the graph's `id`, `name` and `aliases`, then a fulltext phrase match. Unknown entities are
created with a label inferred from `details.entity_type` or the name, and flagged `auto_created=true`.
A name that matches several entities equally well (`"Ambulance"` with two ambulances) is **not** guessed:
the event is recorded with `action='ambiguous'` and applied to nobody. Payloads that fail validation
are reported (`action='invalid'`) and skipped; the stream keeps going. Events older than the newest
evidence already held for that entity (status, position or NEAR edge) are recorded with `applied=false`
and `note='stale'`.

## Claims the fusion agent acts on

| source | claim | details used | graph effect |
|---|---|---|---|
| drone_vision, field_report | `collapsed`, `damaged`, `intact` | – | Building status + provenance |
| radio_asr, drone_vision, field_report, gps | `blocked`, `restricted`, `open` | `transcript` (kept as evidence text) | Road status + provenance |
| sensor | `spike`, `normal` | `reading`, `unit` | Sensor status/reading; `spike` creates or refreshes a `Hazard` that `AFFECTS` what the sensor `MONITORS` |
| gps | `position_update` | `lat`, `lon` (required), `speed_mps` | Team position + `NEAR` relationships to entities within `NEAR_RADIUS_M` (75 m) |
| drone_vision, field_report | `near` | `target` (entity name/id) | Merges into the same `NEAR` relationship as GPS: sources and evidence accumulate, confidence is noisy-OR |
| field_report, radio_asr | `on_scene`, `en_route`, `available`, `out_of_service` | `target` (optional) | Team status; with `target` also `ASSIGNED_TO` |
| any | anything else | – | Recorded as an `Event` and written as the entity's `status` verbatim (generic path), so new claim types never get dropped |

Timestamps are ISO 8601 with timezone (`Z` or offset). Confidence is 0..1.
