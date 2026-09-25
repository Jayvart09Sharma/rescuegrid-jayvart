"""Few-shot example bank for text-to-Cypher and a dependency-free similarity selector.
The bank doubles as the regression corpus: every entry is executable against the scenario graph."""
from __future__ import annotations

import math
import re
from collections import Counter

EXAMPLES_V2: list[dict] = [
    {"q": "Which roads are blocked?", "tags": "road status blocked closed impassable open", "cypher":
     "MATCH (r:Road) WHERE r.status IN ['blocked','restricted'] OR r.conflict = true\n"
     "RETURN r.id AS id, r.name AS name, r.status AS status, r.status_since AS status_since, r.source AS source, r.confidence AS confidence,\n"
     "       r.raw_evidence_ref AS raw_evidence_ref, r.conflict AS conflict, r.conflict_claim AS conflict_claim, r.conflict_source AS conflict_source\n"
     "ORDER BY r.status_since DESC LIMIT 25"},
    {"q": "Where is Rescue Team 4?", "tags": "where unit team position location near", "cypher":
     "MATCH (t:Team {id: 'Team-Rescue4'})\nOPTIONAL MATCH (t)-[n:NEAR]->(x) WHERE n.active = true\n"
     "RETURN t.id AS id, t.name AS name, t.status AS status, t.lat AS lat, t.lon AS lon, t.position_since AS position_since,\n"
     "       collect({near: x.id, distance_m: n.distance_m, sources: n.sources}) AS near LIMIT 5"},
    {"q": "Who is in the most danger?", "tags": "danger risk exposed units hazard collapsed", "cypher":
     "MATCH (t:Team)-[n:NEAR]->(x) WHERE n.active = true\n"
     "WITH t, collect({id: x.id, kind: x.kind, status: x.status, distance_m: n.distance_m, sources: n.sources}) AS near\n"
     "WITH t, near, reduce(s = 0, r IN near | s + CASE WHEN r.kind = 'hazard' AND r.status = 'active' THEN 50\n"
     "                                                  WHEN r.kind = 'building' AND r.status = 'collapsed' THEN 30 ELSE 0 END) AS danger\n"
     "WHERE danger > 0 RETURN t.id AS id, t.name AS name, t.status AS status, danger, near ORDER BY danger DESC LIMIT 10"},
    {"q": "What changed in the last 5 minutes?", "tags": "changed recent last minutes events happened new latest", "cypher":
     "MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $now - duration({minutes: 5})\n"
     "RETURN n.id AS id, n.name AS name, e.timestamp AS timestamp, e.source AS source, e.claim AS claim, e.confidence AS confidence, e.applied AS applied, e.raw_evidence_ref AS raw_evidence_ref\n"
     "ORDER BY e.timestamp DESC LIMIT 25"},
    {"q": "Can Ambulance 2 still reach the hospital?", "tags": "reach route path get to hospital roads connect drive", "cypher":
     "MATCH (t:Team {id: 'Team-Ambulance2'})-[n:NEAR]->(x)-[:ON_ROAD]->(start:Road) WHERE n.active = true\n"
     "MATCH (f:Facility {facility_type: 'hospital'})-[:ON_ROAD]->(dest:Road)\n"
     "MATCH p = (start)-[:CONNECTS_TO*0..6]-(dest)\n"
     "WITH t, f, p, [x IN nodes(p) WHERE x.status = 'blocked' AND coalesce(x.conflict, false) = false | x.id] AS hard_blocked,\n"
     "     [x IN nodes(p) WHERE coalesce(x.conflict, false) = true | x.id] AS uncertain\n"
     "WHERE size(hard_blocked) = 0\n"
     "RETURN t.id AS id, f.id AS facility, [x IN nodes(p) | x.id] AS route, length(p) AS hops, uncertain ORDER BY size(uncertain) ASC, hops ASC LIMIT 3"},
    {"q": "Who reported the Building 14 collapse and with what confidence?", "tags": "who reported report evidence confidence source provenance collapse", "cypher":
     "MATCH (e:Event)-[:ABOUT]->(n:Entity {id: 'Building-14'}) WHERE e.claim = 'collapsed'\n"
     "RETURN n.id AS id, n.name AS name, e.source AS source, e.confidence AS confidence, e.timestamp AS timestamp, e.raw_evidence_ref AS raw_evidence_ref\n"
     "ORDER BY e.timestamp DESC LIMIT 5"},
    {"q": "What did radio report in the last 3 minutes?", "tags": "radio drone sensor gps field report said see reported source last minutes", "cypher":
     "MATCH (e:Event)-[:ABOUT]->(n) WHERE e.source = 'radio_asr' AND e.timestamp >= $now - duration({minutes: 3})\n"
     "RETURN n.id AS id, n.name AS name, e.claim AS claim, e.timestamp AS timestamp, e.confidence AS confidence, e.raw_evidence_ref AS raw_evidence_ref\n"
     "ORDER BY e.timestamp DESC LIMIT 25"},
    {"q": "Which sensors have spiked and what do they monitor?", "tags": "sensor spike spiking reading threshold monitor gas seismic water", "cypher":
     "MATCH (s:Sensor) WHERE s.status = 'spike'\nOPTIONAL MATCH (s)-[:MONITORS]->(m)\n"
     "RETURN s.id AS id, s.name AS name, s.sensor_type AS sensor_type, s.reading AS reading, s.unit AS unit, s.threshold AS threshold, s.status_since AS status_since,\n"
     "       s.source AS source, s.confidence AS confidence, collect(m.id) AS monitors LIMIT 25"},
    {"q": "How many people are affected by active hazards?", "tags": "how many people occupants affected hazard buildings count sum total", "cypher":
     "MATCH (h:Hazard)-[a:AFFECTS]->(b:Building) WHERE h.status = 'active' AND a.active = true\n"
     "RETURN h.id AS id, h.name AS name, h.hazard_type AS hazard_type, h.level AS level, h.unit AS unit, h.source AS source, h.confidence AS confidence,\n"
     "       collect(b.id) AS buildings, sum(coalesce(b.occupancy_est, 0)) AS people ORDER BY people DESC LIMIT 10"},
    {"q": "Which units are near a gas hazard right now?", "tags": "units near inside hazard zone gas leak", "cypher":
     "MATCH (t:Team)-[n:NEAR]->(h:Hazard) WHERE n.active = true AND h.status = 'active' AND h.hazard_type = 'gas'\n"
     "RETURN t.id AS id, t.name AS name, t.status AS status, h.id AS hazard, h.name AS hazard_name, n.distance_m AS distance_m, h.source AS source, h.confidence AS confidence LIMIT 10"},
    {"q": "Which available units are closest to Building 22?", "tags": "closest nearest within metres distance units respond available radius", "cypher":
     "MATCH (b:Entity {id: 'Building-22'}), (t:Team) WHERE t.status = 'available' AND t.location IS NOT NULL\n"
     "RETURN t.id AS id, t.name AS name, t.unit_type AS unit_type, t.status AS status, round(point.distance(t.location, b.location)) AS distance_m,\n"
     "       t.position_since AS position_since, b.id AS target ORDER BY distance_m ASC LIMIT 5"},
    {"q": "Which units are within 200 metres of the hospital?", "tags": "within metres meters radius distance of hospital units", "cypher":
     "MATCH (f:Facility {facility_type: 'hospital'}), (t:Team) WHERE t.location IS NOT NULL AND point.distance(t.location, f.location) <= 200\n"
     "RETURN t.id AS id, t.name AS name, t.status AS status, round(point.distance(t.location, f.location)) AS distance_m, f.id AS facility LIMIT 10"},
    {"q": "Are there any conflicting reports?", "tags": "conflict conflicting disagree contradict dispute sources versus", "cypher":
     "MATCH (n:Entity) WHERE n.conflict = true\n"
     "RETURN n.id AS id, n.name AS name, n.status AS status, n.source AS source, n.confidence AS confidence, n.status_since AS status_since,\n"
     "       n.conflict_claim AS conflict_claim, n.conflict_source AS conflict_source, n.conflict_confidence AS conflict_confidence, n.conflict_since AS conflict_since LIMIT 25"},
    {"q": "What happened between 14:00 and 14:01?", "tags": "between and from to happened events time range window", "cypher":
     "MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= datetime('2026-09-25T14:00:00Z') AND e.timestamp < datetime('2026-09-25T14:01:00Z')\n"
     "RETURN n.id AS id, n.name AS name, e.claim AS claim, e.timestamp AS timestamp, e.source AS source, e.confidence AS confidence ORDER BY e.timestamp ASC LIMIT 25"},
    {"q": "Which units are assigned to Building 14?", "tags": "assigned assignment tasked units staged at", "cypher":
     "MATCH (t:Team)-[a:ASSIGNED_TO]->(x:Entity {id: 'Building-14'}) WHERE a.active = true\n"
     "RETURN t.id AS id, t.name AS name, t.status AS status, x.id AS target, a.since AS since, a.source AS source, a.confidence AS confidence LIMIT 10"},
    {"q": "What is the status of Building 14?", "tags": "status of is latest what happened to building road facility", "cypher":
     "MATCH (n:Entity {id: 'Building-14'})\n"
     "RETURN n.id AS id, n.name AS name, n.status AS status, n.status_since AS status_since, n.last_confirmed AS last_confirmed, n.source AS source, n.confidence AS confidence,\n"
     "       n.raw_evidence_ref AS raw_evidence_ref, n.conflict AS conflict, n.conflict_claim AS conflict_claim, n.conflict_source AS conflict_source, n.occupancy_est AS occupancy_est LIMIT 1"},
]

EXAMPLES_V0: list[dict] = [
    {"q": 'Which roads are blocked?', "tags": 'road status blocked closed impassable open', "cypher": "MATCH (r:Road) WHERE r.status IN ['blocked','restricted'] OR r.conflict = true\nRETURN r.id AS id, r.name AS name, r.status AS status, r.status_since AS status_since, r.source AS source, r.confidence AS confidence,\n       r.raw_evidence_ref AS raw_evidence_ref, r.conflict AS conflict, r.conflict_claim AS conflict_claim, r.conflict_source AS conflict_source\nORDER BY r.status_since DESC LIMIT 25"},
    {"q": 'Where is Rescue Team 4?', "tags": 'where unit team position location near', "cypher": "MATCH (t:Team) WHERE toLower(t.name) CONTAINS 'rescue team 4' OR any(a IN t.aliases WHERE toLower(a) = 'rescue team 4')\nOPTIONAL MATCH (t)-[n:NEAR]->(x) WHERE n.active = true\nRETURN t.id AS id, t.name AS name, t.status AS status, t.lat AS lat, t.lon AS lon, t.position_since AS position_since,\n       collect({near: x.id, distance_m: n.distance_m, sources: n.sources}) AS near LIMIT 5"},
    {"q": 'Who is in the most danger?', "tags": 'danger risk exposed units hazard collapsed', "cypher": "MATCH (t:Team)-[n:NEAR]->(x) WHERE n.active = true\nWITH t, collect({id: x.id, kind: x.kind, status: x.status, distance_m: n.distance_m, sources: n.sources, evidence_refs: n.evidence_refs}) AS near\nWITH t, near, reduce(s = 0, r IN near | s + CASE WHEN r.kind = 'hazard' AND r.status = 'active' THEN 50\n                                                  WHEN r.kind = 'building' AND r.status = 'collapsed' THEN 30\n                                                  WHEN r.kind = 'building' AND r.status = 'damaged' THEN 15 ELSE 0 END) AS danger\nWHERE danger > 0\nRETURN t.id AS id, t.name AS name, t.status AS status, danger, near ORDER BY danger DESC LIMIT 10"},
    {"q": 'What changed in the last 5 minutes?', "tags": 'changed recent last minutes events happened new latest', "cypher": 'MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $now - duration({minutes: 5})\nRETURN n.id AS id, e.timestamp AS timestamp, e.source AS source, e.claim AS claim, e.confidence AS confidence, e.applied AS applied, e.action AS action, e.raw_evidence_ref AS raw_evidence_ref\nORDER BY e.timestamp DESC LIMIT 25'},
    {"q": 'Can Ambulance 2 still reach the hospital?', "tags": 'reach route path get to hospital roads connect drive', "cypher": "MATCH (t:Team) WHERE any(a IN t.aliases WHERE toLower(a) = 'ambulance 2')\nOPTIONAL MATCH (t)-[n:NEAR]->(x)-[:ON_ROAD]->(start:Road) WHERE n.active = true\nWITH t, head(collect(DISTINCT start)) AS start\nMATCH (f:Facility {facility_type: 'hospital'})-[:ON_ROAD]->(dest:Road)\nMATCH p = (start)-[:CONNECTS_TO*0..6]-(dest)\nWITH t, f, p, [x IN nodes(p) WHERE x.status = 'blocked' AND coalesce(x.conflict, false) = false | x.id] AS hard_blocked,\n     [x IN nodes(p) WHERE coalesce(x.conflict, false) = true | x.id] AS uncertain\nWHERE size(hard_blocked) = 0\nRETURN t.id AS id, f.id AS facility, [x IN nodes(p) | x.id] AS route, length(p) AS hops, uncertain\nORDER BY size(uncertain) ASC, hops ASC LIMIT 3"},
    {"q": 'Who reported the Building 14 collapse and with what confidence?', "tags": 'who reported report evidence confidence source provenance collapse', "cypher": "MATCH (e:Event)-[:ABOUT]->(n:Entity {id: 'Building-14'}) WHERE e.claim = 'collapsed'\nRETURN n.id AS id, e.id AS event_id, e.source AS source, e.confidence AS confidence, e.timestamp AS timestamp, e.raw_evidence_ref AS raw_evidence_ref, e.applied AS applied\nORDER BY e.timestamp DESC LIMIT 5"},
    {"q": 'What did radio report in the last 3 minutes?', "tags": 'radio drone sensor gps field report said see reported source last minutes', "cypher": "MATCH (e:Event)-[:ABOUT]->(n) WHERE e.source = 'radio_asr' AND e.timestamp >= $now - duration({minutes: 3})\nRETURN n.id AS id, n.name AS name, e.claim AS claim, e.timestamp AS timestamp, e.confidence AS confidence, e.raw_evidence_ref AS raw_evidence_ref, e.applied AS applied\nORDER BY e.timestamp DESC LIMIT 25"},
    {"q": 'Which sensors have spiked and what do they monitor?', "tags": 'sensor spike spiking reading threshold monitor gas seismic water', "cypher": "MATCH (s:Sensor) WHERE s.status = 'spike'\nOPTIONAL MATCH (s)-[:MONITORS]->(m)\nRETURN s.id AS id, s.name AS name, s.sensor_type AS sensor_type, s.reading AS reading, s.unit AS unit, s.threshold AS threshold, s.status_since AS status_since,\n       s.source AS source, s.confidence AS confidence, s.raw_evidence_ref AS raw_evidence_ref, collect(m.id) AS monitors LIMIT 25"},
    {"q": 'How many people are affected by active hazards?', "tags": 'how many people occupants affected hazard buildings count sum total', "cypher": "MATCH (h:Hazard)-[a:AFFECTS]->(b:Building) WHERE h.status = 'active' AND a.active = true\nRETURN h.id AS id, h.name AS name, h.level AS level, h.unit AS unit, h.source AS source, h.confidence AS confidence, h.status_since AS status_since,\n       collect(b.id) AS buildings, sum(coalesce(b.occupancy_est, 0)) AS people ORDER BY people DESC LIMIT 10"},
    {"q": 'Which units are near a gas hazard right now?', "tags": 'units near inside hazard zone gas leak', "cypher": "MATCH (t:Team)-[n:NEAR]->(h:Hazard) WHERE n.active = true AND h.status = 'active' AND h.hazard_type = 'gas'\nRETURN t.id AS id, t.name AS name, t.status AS status, h.id AS hazard, h.status_since AS status_since, h.source AS source,\n       h.confidence AS confidence, h.raw_evidence_ref AS raw_evidence_ref, n.distance_m AS distance_m LIMIT 10"},
    {"q": 'Which available units are closest to Building 22?', "tags": 'closest nearest within metres distance units respond available radius', "cypher": "MATCH (b:Entity {id: 'Building-22'}), (t:Team) WHERE t.status = 'available' AND t.location IS NOT NULL AND b.location IS NOT NULL\nRETURN t.id AS id, t.name AS name, t.unit_type AS unit_type, t.status AS status, round(point.distance(t.location, b.location)) AS distance_m,\n       t.position_since AS position_since, b.id AS target ORDER BY distance_m ASC LIMIT 5"},
]

EXAMPLES = EXAMPLES_V0  # the baseline prompt uses exactly the original 11 examples


def bank(features) -> list[dict]:
    return EXAMPLES_V2 if ("fewshot_v2" in features or "all" in features) else EXAMPLES_V0


_TOK = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "of", "in", "on", "at", "to", "and", "or", "what", "which", "who", "how", "do", "does", "any", "there", "right", "now", "me", "give", "tell", "about", "still", "can", "with", "for"}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOK.findall(text.lower()) if t not in _STOP]


def select_examples(question: str, k: int = 3, examples: list[dict] | None = None) -> list[dict]:
    """Top-k examples by TF-IDF-weighted token overlap between the question and each example's question+tags."""
    examples = examples if examples is not None else EXAMPLES
    df: Counter = Counter()
    for ex in examples:
        for t in set(_tokens(ex["q"] + " " + ex["tags"])):
            df[t] += 1
    qt = set(_tokens(question))
    if not qt:
        return examples[:k]
    n = len(examples)
    scored = []
    for ex in examples:
        et = set(_tokens(ex["q"] + " " + ex["tags"]))
        score = sum(math.log((n + 1) / (1 + df[t])) for t in qt & et)
        scored.append((score, ex))
    scored.sort(key=lambda x: -x[0])
    chosen = [ex for s, ex in scored[:k] if s > 0]
    return chosen or examples[:k]


def render(examples: list[dict]) -> str:
    return "\n".join(f"Q: {ex['q']}\n```cypher\n{ex['cypher']}\n```" for ex in examples)
