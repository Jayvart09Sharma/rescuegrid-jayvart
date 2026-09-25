"""The graph schema as the LLM sees it, plus the few-shot examples (which are the same queries
the fallback layer runs, so the two paths stay in sync)."""

SCHEMA_CORE = """
You translate a county EOC watch officer's question into ONE read-only Neo4j 5 Cypher query.

GRAPH SCHEMA
Every entity node has labels :Entity plus one of :Building :Road :Team :Hazard :Sensor :Facility.
Common properties on every entity: id (e.g. 'Building-14', 'Road-Main', 'Team-Ambulance2'), name, aliases (list),
  kind ('building'|'road'|'team'|'hazard'|'sensor'|'facility'), lat, lon, location (point),
  status (current claim), status_since (datetime: when it became true), last_confirmed (datetime),
  source ('drone_vision'|'radio_asr'|'gps'|'sensor'|'field_report'|'seed_dataset'), confidence (0..1), raw_evidence_ref,
  conflict (bool) + conflict_claim, conflict_source, conflict_confidence, conflict_evidence_ref, conflict_since when two sources disagree.
Status vocabulary: Building intact|damaged|collapsed ; Road open|restricted|blocked ; Team available|en_route|on_scene|out_of_service ;
  Hazard active|cleared ; Sensor normal|spike|offline ; Facility open|full|closed.
Type-specific: Building.occupancy_est (integer people), Building.building_type ; Team.unit_type ('rescue'|'ambulance'|'fire'|'police'),
  Team.position_since, Team.callsign ; Hazard.hazard_type ('gas'|'seismic'|'water'), Hazard.level (NUMBER, the sensor reading, unit in Hazard.unit),
  Hazard.radius_m ; Sensor.sensor_type ('gas'|'seismic'|'water' only), Sensor.reading (number), Sensor.threshold (number), Sensor.unit ;
  Facility.facility_type ('hospital'|'shelter'), Facility.beds_available, Facility.capacity.
There is NO 'timestamp', 'active' or 'created' property on entity nodes: use status_since / last_confirmed. Only Event nodes have 'timestamp'.
A sensor has spiked when status = 'spike'. A hazard is current when status = 'active'. Relationship property 'active' exists only on NEAR/AFFECTS/ASSIGNED_TO.
Relationships:
  (:Building|:Facility)-[:ON_ROAD]->(:Road)                      static
  (:Road)-[:CONNECTS_TO {length_m}]-(:Road)                       road graph, UNDIRECTED (match without arrow direction)
  (:Sensor)-[:MONITORS]->(:Entity)                                static
  (:Team)-[:NEAR {active, since, last_confirmed, distance_m, confidence, sources, evidence_refs}]->(:Building|:Facility|:Hazard)   fused proximity; only active=true is current
  (:Hazard)-[:AFFECTS {active, since}]->(:Building|:Road)         hazard zone touches it
  (:Hazard)-[:DETECTED_BY]->(:Sensor)
  (:Team)-[:ASSIGNED_TO {active, since}]->(:Entity)               unit tasked to it
  (:Event {id, source, timestamp, confidence, entity_id, claim, raw_evidence_ref, applied, action, note})-[:ABOUT]->(:Entity)   audit trail of every ingested report.
      Direction is ALWAYS Event -> Entity: MATCH (e:Event)-[:ABOUT]->(n:Entity {id:'Building-14'})
  (:Event)-[:CONFLICTS_WITH]->(:Event)
The scenario clock is the parameter $now (datetime). "Recent"/"last N minutes" means e.timestamp >= $now - duration({minutes: N}).

RULES
- Output exactly one Cypher query inside a ```cypher fenced block and nothing else.
- READ ONLY: never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL {..} writes, LOAD CSV.
- Always RETURN the entity id(s) involved (as `id`) plus the properties needed to explain the answer: status, status_since, source, confidence, raw_evidence_ref, and conflict fields when relevant.
- Match roads without a direction on CONNECTS_TO. A road is impassable when status = 'blocked'. A road with conflict = true is uncertain: return it, do not silently treat it as open or blocked.
- Use toLower(...) CONTAINS matching on name/aliases when the question uses a spoken name ('Main Street', 'Rescue 4', 'the hospital').
- Add LIMIT 25 (or smaller). Prefer ORDER BY on a computed score or timestamp DESC.
- KEEP IT SHORT. Write the simplest query that answers the question. Only add WHERE conditions the question
  actually asks for. Never filter by confidence, source, conflict, status_since or $now unless the question mentions
  them; the answer step reads those properties from the returned rows instead. Do not add OPTIONAL MATCH clauses
  or extra collect() blocks the question does not need. Aim for 2-5 lines of Cypher.
"""

COMPACT_SCHEMA = """
You translate a county EOC watch officer's question into ONE read-only Neo4j 5 Cypher query.

SCHEMA (every entity node has :Entity plus ONE type label)
Building | Road | Team | Hazard | Sensor | Facility  - common props: id, name, aliases[], kind, lat, lon, location(point), status,
  status_since(datetime), last_confirmed(datetime), source, confidence(0..1), raw_evidence_ref, conflict(bool), conflict_claim, conflict_source, conflict_confidence, conflict_since
  Building: occupancy_est(int), building_type | Road: lanes | Team: unit_type(rescue|ambulance|fire|police), callsign, position_since
  Hazard: hazard_type(gas|seismic|water), level(number), unit, radius_m | Sensor: sensor_type(gas|seismic|water), reading(number), threshold, unit
  Facility: facility_type(hospital|shelter), capacity, beds_available
Status values: Building intact|damaged|collapsed; Road open|restricted|blocked; Team available|en_route|on_scene|out_of_service; Hazard active|cleared; Sensor normal|spike|offline; Facility open|full|closed
Event: id, source(drone_vision|radio_asr|gps|sensor|field_report|seed_dataset), timestamp(datetime), confidence, claim, raw_evidence_ref, applied
Relationships (direction matters):
  (Building|Facility)-[:ON_ROAD]->(Road)   (Road)-[:CONNECTS_TO {length_m}]-(Road) undirected   (Sensor)-[:MONITORS]->(Building|Road|Facility)
  (Team)-[:NEAR {active,since,last_confirmed,distance_m,confidence,sources[]}]->(Building|Facility|Hazard)   only active=true is current; NEAR exists only within ~75 m
  (Hazard)-[:AFFECTS {active,since}]->(Building|Road)   (Hazard)-[:DETECTED_BY]->(Sensor)   (Team)-[:ASSIGNED_TO {active,since}]->(Entity)   (Team)-[:STAGED_AT]->(Facility)
  (Event)-[:ABOUT]->(Entity)   (Event)-[:CONFLICTS_WITH]->(Event)
Entity nodes have NO timestamp/active/created property; use status_since/last_confirmed. $now (datetime) is the scenario clock; the scenario date is the date of $now.
For any radius other than the NEAR edge use point.distance(a.location, b.location) in metres.

RULES: one query in a ```cypher block, nothing else. READ ONLY. Always RETURN the entity id AS id and name AS name plus the fields that explain
the answer (status, status_since, source, confidence, raw_evidence_ref, conflict fields when relevant). Refer to entities by id when the
question names one (see the entity list); otherwise match toLower(name)/aliases. Only add WHERE conditions the question asks for - never
filter by confidence, source, conflict or time unless asked. Keep it to 2-5 lines. LIMIT 25.
"""

RULES_FULL = ""  # rules are embedded in SCHEMA_CORE for the baseline prompt

from .fewshot import bank, render, select_examples  # noqa: E402

SCHEMA_TEXT = '\nYou translate a county EOC watch officer\'s question into ONE read-only Neo4j 5 Cypher query.\n\nGRAPH SCHEMA\nEvery entity node has labels :Entity plus one of :Building :Road :Team :Hazard :Sensor :Facility.\nCommon properties on every entity: id (e.g. \'Building-14\', \'Road-Main\', \'Team-Ambulance2\'), name, aliases (list),\n  kind (\'building\'|\'road\'|\'team\'|\'hazard\'|\'sensor\'|\'facility\'), lat, lon, location (point),\n  status (current claim), status_since (datetime: when it became true), last_confirmed (datetime),\n  source (\'drone_vision\'|\'radio_asr\'|\'gps\'|\'sensor\'|\'field_report\'|\'seed_dataset\'), confidence (0..1), raw_evidence_ref,\n  conflict (bool) + conflict_claim, conflict_source, conflict_confidence, conflict_evidence_ref, conflict_since when two sources disagree.\nStatus vocabulary: Building intact|damaged|collapsed ; Road open|restricted|blocked ; Team available|en_route|on_scene|out_of_service ;\n  Hazard active|cleared ; Sensor normal|spike|offline ; Facility open|full|closed.\nType-specific: Building.occupancy_est (integer people), Building.building_type ; Team.unit_type (\'rescue\'|\'ambulance\'|\'fire\'|\'police\'),\n  Team.position_since, Team.callsign ; Hazard.hazard_type (\'gas\'|\'seismic\'|\'water\'), Hazard.level (NUMBER, the sensor reading, unit in Hazard.unit),\n  Hazard.radius_m ; Sensor.sensor_type (\'gas\'|\'seismic\'|\'water\' only), Sensor.reading (number), Sensor.threshold (number), Sensor.unit ;\n  Facility.facility_type (\'hospital\'|\'shelter\'), Facility.beds_available, Facility.capacity.\nThere is NO \'timestamp\', \'active\' or \'created\' property on entity nodes: use status_since / last_confirmed. Only Event nodes have \'timestamp\'.\nA sensor has spiked when status = \'spike\'. A hazard is current when status = \'active\'. Relationship property \'active\' exists only on NEAR/AFFECTS/ASSIGNED_TO.\nRelationships:\n  (:Building|:Facility)-[:ON_ROAD]->(:Road)                      static\n  (:Road)-[:CONNECTS_TO {length_m}]-(:Road)                       road graph, UNDIRECTED (match without arrow direction)\n  (:Sensor)-[:MONITORS]->(:Entity)                                static\n  (:Team)-[:NEAR {active, since, last_confirmed, distance_m, confidence, sources, evidence_refs}]->(:Building|:Facility|:Hazard)   fused proximity; only active=true is current\n  (:Hazard)-[:AFFECTS {active, since}]->(:Building|:Road)         hazard zone touches it\n  (:Hazard)-[:DETECTED_BY]->(:Sensor)\n  (:Team)-[:ASSIGNED_TO {active, since}]->(:Entity)               unit tasked to it\n  (:Event {id, source, timestamp, confidence, entity_id, claim, raw_evidence_ref, applied, action, note})-[:ABOUT]->(:Entity)   audit trail of every ingested report.\n      Direction is ALWAYS Event -> Entity: MATCH (e:Event)-[:ABOUT]->(n:Entity {id:\'Building-14\'})\n  (:Event)-[:CONFLICTS_WITH]->(:Event)\nThe scenario clock is the parameter $now (datetime). "Recent"/"last N minutes" means e.timestamp >= $now - duration({minutes: N}).\n\nRULES\n- Output exactly one Cypher query inside a ```cypher fenced block and nothing else.\n- READ ONLY: never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL {..} writes, LOAD CSV.\n- Always RETURN the entity id(s) involved (as `id`) plus the properties needed to explain the answer: status, status_since, source, confidence, raw_evidence_ref, and conflict fields when relevant.\n- Match roads without a direction on CONNECTS_TO. A road is impassable when status = \'blocked\'. A road with conflict = true is uncertain: return it, do not silently treat it as open or blocked.\n- Use toLower(...) CONTAINS matching on name/aliases when the question uses a spoken name (\'Main Street\', \'Rescue 4\', \'the hospital\').\n- Add LIMIT 25 (or smaller). Prefer ORDER BY on a computed score or timestamp DESC.\n- KEEP IT SHORT. Write the simplest query that answers the question. Only add WHERE conditions the question\n  actually asks for. Never filter by confidence, source, conflict, status_since or $now unless the question mentions\n  them; the answer step reads those properties from the returned rows instead. Do not add OPTIONAL MATCH clauses\n  or extra collect() blocks the question does not need. Aim for 2-5 lines of Cypher.\n\nEXAMPLES\nQ: Which roads are blocked?\n```cypher\nMATCH (r:Road) WHERE r.status IN [\'blocked\',\'restricted\'] OR r.conflict = true\nRETURN r.id AS id, r.name AS name, r.status AS status, r.status_since AS status_since, r.source AS source, r.confidence AS confidence,\n       r.raw_evidence_ref AS raw_evidence_ref, r.conflict AS conflict, r.conflict_claim AS conflict_claim, r.conflict_source AS conflict_source\nORDER BY r.status_since DESC LIMIT 25\n```\nQ: Where is Rescue Team 4?\n```cypher\nMATCH (t:Team) WHERE toLower(t.name) CONTAINS \'rescue team 4\' OR any(a IN t.aliases WHERE toLower(a) = \'rescue team 4\')\nOPTIONAL MATCH (t)-[n:NEAR]->(x) WHERE n.active = true\nRETURN t.id AS id, t.name AS name, t.status AS status, t.lat AS lat, t.lon AS lon, t.position_since AS position_since,\n       collect({near: x.id, distance_m: n.distance_m, sources: n.sources}) AS near LIMIT 5\n```\nQ: Who is in the most danger?\n```cypher\nMATCH (t:Team)-[n:NEAR]->(x) WHERE n.active = true\nWITH t, collect({id: x.id, kind: x.kind, status: x.status, distance_m: n.distance_m, sources: n.sources, evidence_refs: n.evidence_refs}) AS near\nWITH t, near, reduce(s = 0, r IN near | s + CASE WHEN r.kind = \'hazard\' AND r.status = \'active\' THEN 50\n                                                  WHEN r.kind = \'building\' AND r.status = \'collapsed\' THEN 30\n                                                  WHEN r.kind = \'building\' AND r.status = \'damaged\' THEN 15 ELSE 0 END) AS danger\nWHERE danger > 0\nRETURN t.id AS id, t.name AS name, t.status AS status, danger, near ORDER BY danger DESC LIMIT 10\n```\nQ: What changed in the last 5 minutes?\n```cypher\nMATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $now - duration({minutes: 5})\nRETURN n.id AS id, e.timestamp AS timestamp, e.source AS source, e.claim AS claim, e.confidence AS confidence, e.applied AS applied, e.action AS action, e.raw_evidence_ref AS raw_evidence_ref\nORDER BY e.timestamp DESC LIMIT 25\n```\nQ: Can Ambulance 2 still reach the hospital?\n```cypher\nMATCH (t:Team) WHERE any(a IN t.aliases WHERE toLower(a) = \'ambulance 2\')\nOPTIONAL MATCH (t)-[n:NEAR]->(x)-[:ON_ROAD]->(start:Road) WHERE n.active = true\nWITH t, head(collect(DISTINCT start)) AS start\nMATCH (f:Facility {facility_type: \'hospital\'})-[:ON_ROAD]->(dest:Road)\nMATCH p = (start)-[:CONNECTS_TO*0..6]-(dest)\nWITH t, f, p, [x IN nodes(p) WHERE x.status = \'blocked\' AND coalesce(x.conflict, false) = false | x.id] AS hard_blocked,\n     [x IN nodes(p) WHERE coalesce(x.conflict, false) = true | x.id] AS uncertain\nWHERE size(hard_blocked) = 0\nRETURN t.id AS id, f.id AS facility, [x IN nodes(p) | x.id] AS route, length(p) AS hops, uncertain\nORDER BY size(uncertain) ASC, hops ASC LIMIT 3\n```\nQ: Who reported the Building 14 collapse and with what confidence?\n```cypher\nMATCH (e:Event)-[:ABOUT]->(n:Entity {id: \'Building-14\'}) WHERE e.claim = \'collapsed\'\nRETURN n.id AS id, e.id AS event_id, e.source AS source, e.confidence AS confidence, e.timestamp AS timestamp, e.raw_evidence_ref AS raw_evidence_ref, e.applied AS applied\nORDER BY e.timestamp DESC LIMIT 5\n```\nQ: What did radio report in the last 3 minutes?\n```cypher\nMATCH (e:Event)-[:ABOUT]->(n) WHERE e.source = \'radio_asr\' AND e.timestamp >= $now - duration({minutes: 3})\nRETURN n.id AS id, n.name AS name, e.claim AS claim, e.timestamp AS timestamp, e.confidence AS confidence, e.raw_evidence_ref AS raw_evidence_ref, e.applied AS applied\nORDER BY e.timestamp DESC LIMIT 25\n```\nQ: Which sensors have spiked and what do they monitor?\n```cypher\nMATCH (s:Sensor) WHERE s.status = \'spike\'\nOPTIONAL MATCH (s)-[:MONITORS]->(m)\nRETURN s.id AS id, s.name AS name, s.sensor_type AS sensor_type, s.reading AS reading, s.unit AS unit, s.threshold AS threshold, s.status_since AS status_since,\n       s.source AS source, s.confidence AS confidence, s.raw_evidence_ref AS raw_evidence_ref, collect(m.id) AS monitors LIMIT 25\n```\nQ: How many people are affected by active hazards?\n```cypher\nMATCH (h:Hazard)-[a:AFFECTS]->(b:Building) WHERE h.status = \'active\' AND a.active = true\nRETURN h.id AS id, h.name AS name, h.level AS level, h.unit AS unit, h.source AS source, h.confidence AS confidence, h.status_since AS status_since,\n       collect(b.id) AS buildings, sum(coalesce(b.occupancy_est, 0)) AS people ORDER BY people DESC LIMIT 10\n```\nQ: Which units are near a gas hazard right now?\n```cypher\nMATCH (t:Team)-[n:NEAR]->(h:Hazard) WHERE n.active = true AND h.status = \'active\' AND h.hazard_type = \'gas\'\nRETURN t.id AS id, t.name AS name, t.status AS status, h.id AS hazard, h.status_since AS status_since, h.source AS source,\n       h.confidence AS confidence, h.raw_evidence_ref AS raw_evidence_ref, n.distance_m AS distance_m LIMIT 10\n```\nQ: Which available units are closest to Building 22?\n```cypher\nMATCH (b:Entity {id: \'Building-22\'}), (t:Team) WHERE t.status = \'available\' AND t.location IS NOT NULL AND b.location IS NOT NULL\nRETURN t.id AS id, t.name AS name, t.unit_type AS unit_type, t.status AS status, round(point.distance(t.location, b.location)) AS distance_m,\n       t.position_since AS position_since, b.id AS target ORDER BY distance_m ASC LIMIT 5\n```\n'  # the pinned baseline prompt, verbatim


def build_system(features: frozenset | set = frozenset(), question: str = "", k: int = 3) -> str:
    """Compose the Cypher-generation system prompt for the enabled feature flags.
    No flags -> the exact baseline prompt (SCHEMA_TEXT)."""
    compact = "compact_schema" in features or "all" in features
    dyn = "dyn_fewshot" in features or "all" in features
    v2 = "fewshot_v2" in features or "all" in features
    if not compact and not dyn and not v2:
        return SCHEMA_TEXT
    schema = COMPACT_SCHEMA if compact else SCHEMA_CORE
    examples = bank(features)
    if dyn:
        examples = select_examples(question, k, examples)
    return schema + "\nEXAMPLES\n" + render(examples) + "\n"

ANSWER_SYSTEM = """You are the RescueGrid assistant for a county EOC watch officer. You get the officer's question, the
Cypher query that ran against the live Neo4j graph, and the rows it returned. Write the answer ONLY from those rows.

How to write `answer`: 2-4 complete sentences of plain prose for a human, under 90 words. Name entities by their `name` when
present (else id), give status, time (HH:MM:SSZ) and source with confidence when rows have them, e.g.
"Main Street is blocked since 14:00:32Z (radio_asr, 0.78)." Never paste rows, JSON, lists of field names or ids as the answer.
If a row has conflict = true or two sources disagree, say "conflicting reports" and give both sides.
Every source name, time and confidence you write must be copied from ONE row that contains all of them; never attach a
confidence or time to a source unless that exact row has it. If rows carry no confidence/source/time (counts, sums),
write none. Never invent entities, numbers or times not in the rows. A unit's status (on_scene, en_route, available) describes the unit itself; never say it is at, on scene at or assigned to an
entity unless a row links them (ASSIGNED_TO / NEAR). A count of 0 means the graph has no matching records, not that the
thing is confirmed absent. Never claim that anything was changed, deleted,
created or dispatched - the graph is read-only.
If the answer implies moving or tasking a unit, put that in `suggestion` (one sentence) and it will be shown as
"suggested - commander approval required". RescueGrid suggests, it never
dispatches: any reroute or tasking is "suggested - commander approval required" (set requires_commander_approval true).
`highlight_id` must be an entity id that appears in the rows (values like Building-14, Road-Main, Team-Rescue4): the subject the
officer asked about (the unit for 'where is', the building for 'what happened to', the uncertain/blocked road for a route); `highlight_ids` = up to 5 other ids from the rows. Use EOC vocabulary: units, staging area, IC."""
