# Q&A output contract (v0.1) - handoff to the 3D twin

`POST /qa` (scripts/serve_qa.py) or `QAEngine.answer()` returns this JSON. The minimal shape from
Kenil's brief, `{"answer": ..., "highlight": {"type": ..., "id": ...}}`, is a strict subset, so a
frontend that only reads those two fields works unchanged.

```json
{
  "answer": "No confirmed route. Ambulance 2 is on Oak Avenue (near Lincoln High School Shelter). Main Street blocked (radio_asr 0.78, 14:00:32Z). The only remaining route Oak Avenue -> Bridge Street -> River Road has conflicting reports - Bridge Street: drone_vision reports blocked (0.85, 14:02:45Z) but radio_asr reports open (0.70, 14:02:30Z). Recommend verifying before committing the unit.",
  "highlight": {
    "type": "road",              // building | road | team | hazard | sensor | facility | event | none
    "id": "Road-Bridge",         // the entity to fly the camera to (null when type = none)
    "ids": ["Road-Oak", "Road-River", "Team-Ambulance2", "Facility-CountyGeneral"],   // also highlight, e.g. the route
    "action": "fly_to",          // fly_to | highlight | none
    "lat": 37.336, "lon": -121.8935
  },
  "confidence": 0.8,
  "mode": "fallback",            // fallback (pre-baked Cypher) | llm (text->Cypher) | llm+fallback | error
  "intent": "reachability",      // set for pre-baked answers
  "cypher": "MATCH ...",         // the query that produced the evidence (for the provenance panel / debugging)
  "evidence": [ {...rows...} ],  // raw rows the answer was computed from
  "provenance": [ {"source": "radio_asr", "timestamp": "2026-09-25T14:00:32Z", "confidence": 0.78, "raw_evidence_ref": "radio/ch3/clip_000032.wav"} ],
  "suggestion": "If Bridge Street is verified passable: Oak Avenue -> Bridge Street -> River Road (2 segments, ~1320 m) - suggested, commander approval required.",
  "requires_commander_approval": true,   // the twin must render this as a suggestion, never as an order
  "conflicts": [ {"id": "Road-Bridge", "name": "Bridge Street", "status": "blocked", "source": "drone_vision", "confidence": 0.85,
                  "competing_claim": "open", "competing_source": "radio_asr", "competing_confidence": 0.7} ],
  "warnings": ["route depends on a road with conflicting reports"],
  "question": "Can Ambulance 2 still reach the hospital?",
  "as_of": "2026-09-25T14:02:45Z"        // the scenario clock the answer was computed at
}
```

## Frontend beats this drives

| Beat (Pranay's brief) | Field |
|---|---|
| camera fly-to when a question is answered | `highlight.id` + `highlight.lat/lon`, `highlight.action = fly_to` |
| highlight a route / several entities | `highlight.ids` (mixed types are fine - ids are unique across the graph) |
| "suggested reroute - commander approval required" banner | `requires_commander_approval` + `suggestion` |
| conflicting-reports badge | `conflicts[]` (non-empty) and the `warnings[]` text |
| provenance click-through | `provenance[]` (`raw_evidence_ref` is the frame / clip / message id) and `evidence[]` |

`id` values are the graph ids (`Building-14`, `Road-Main`, `Team-Rescue4`, `Hazard-gas-Sensor-Gas3`,
`Facility-CountyGeneral`), so the twin and the graph agree without a lookup table.

## Pre-baked questions (guaranteed, LLM-free)

`GET /questions` returns them; each also works with any phrasing that matches its intent regex:

1. Who is in the most danger?
2. Can Ambulance 2 still reach the hospital?   (any `Can <unit> reach <facility>?`)
3. Which roads are blocked?
4. What changed in the last 5 minutes?         (any N minutes / hours)
5. Are there any conflicting reports?
6. Where is Rescue Team 4?                     (any `Where is <unit>?`)
7. What happened to Building 14?               (any `What happened to / status of <entity>?`)

Everything else goes to the LLM text->Cypher path (`mode=auto`). `mode=fallback` never calls a model.
