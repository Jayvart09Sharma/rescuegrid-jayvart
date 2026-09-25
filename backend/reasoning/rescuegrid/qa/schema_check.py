"""Static, schema-aware validation of generated Cypher (no database round-trip).
Catches the failure classes the benchmark exposed: reversed relationship direction
((Building)-[:AFFECTS]->(Hazard)), invented property names (Team.timestamp), spoken names used
as ids ({id: 'Gas Sensor 3'}), and RETURN clauses without an entity id. Each violation becomes a
precise repair message for the model, which is far cheaper than executing, getting 0 rows and guessing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ENTITY_LABELS = {"Building", "Road", "Team", "Hazard", "Sensor", "Facility", "Entity"}
# (relationship, allowed start labels, allowed end labels, directed)
REL_SCHEMA = {
    "ON_ROAD": ({"Building", "Facility", "Entity"}, {"Road", "Entity"}, True),
    "CONNECTS_TO": ({"Road", "Entity"}, {"Road", "Entity"}, False),
    "MONITORS": ({"Sensor", "Entity"}, {"Building", "Road", "Facility", "Entity"}, True),
    "STAGED_AT": ({"Team", "Entity"}, {"Facility", "Entity"}, True),
    "NEAR": ({"Team", "Entity"}, {"Building", "Facility", "Hazard", "Entity"}, True),
    "AFFECTS": ({"Hazard", "Entity"}, {"Building", "Road", "Entity"}, True),
    "DETECTED_BY": ({"Hazard", "Entity"}, {"Sensor", "Entity"}, True),
    "ASSIGNED_TO": ({"Team", "Entity"}, ENTITY_LABELS, True),
    "ABOUT": ({"Event"}, ENTITY_LABELS, True),
    "CONFLICTS_WITH": ({"Event"}, {"Event"}, True),
}
COMMON = {"id", "name", "aliases", "kind", "lat", "lon", "location", "status", "status_since", "last_confirmed", "source", "confidence",
          "raw_evidence_ref", "conflict", "conflict_claim", "conflict_source", "conflict_confidence", "conflict_evidence_ref", "conflict_since",
          "conflict_event_id", "status_event_id", "previous_status", "previous_status_since", "previous_source", "confirmations", "confirmed_sources",
          "last_confirmed_source", "last_confirmed_evidence_ref", "seeded", "auto_created", "created_at", "updated_at", "conflict_note", "conflict_resolved_at", "conflict_resolution"}
NODE_PROPS = {
    "Building": COMMON | {"building_type", "floors", "occupancy_est"},
    "Road": COMMON | {"lanes"},
    "Team": COMMON | {"unit_type", "callsign", "personnel", "position_since", "position_source", "position_evidence_ref", "position_event_id", "speed_mps"},
    "Hazard": COMMON | {"hazard_type", "level", "unit", "radius_m"},
    "Sensor": COMMON | {"sensor_type", "unit", "threshold", "reading", "reading_at"},
    "Facility": COMMON | {"facility_type", "capacity", "beds_available"},
    "Entity": COMMON | {"building_type", "floors", "occupancy_est", "lanes", "unit_type", "callsign", "personnel", "position_since", "position_source",
                        "position_evidence_ref", "speed_mps", "hazard_type", "level", "unit", "radius_m", "sensor_type", "threshold", "reading", "facility_type", "capacity", "beds_available"},
    "Event": {"id", "source", "timestamp", "confidence", "entity", "entity_id", "claim", "raw_evidence_ref", "details", "text", "ingested_at", "applied", "action", "note"},
}
REL_PROPS = {
    "NEAR": {"active", "since", "last_confirmed", "ended_at", "ended_by_event", "distance_m", "confidence", "sources", "evidence_refs", "event_ids", "last_source", "epochs"},
    "AFFECTS": {"active", "since", "last_confirmed", "ended_at", "source", "confidence", "evidence_refs", "event_ids"},
    "ASSIGNED_TO": {"active", "since", "last_confirmed", "ended_at", "ended_by_event", "source", "confidence", "evidence_refs", "event_ids"},
    "DETECTED_BY": {"since", "last_confirmed", "source"}, "CONNECTS_TO": {"length_m", "source"}, "ON_ROAD": {"source"}, "MONITORS": {"source"},
    "STAGED_AT": {"since", "source"}, "ABOUT": set(), "CONFLICTS_WITH": {"detected_at"},
}
ID_RX = re.compile(r"^(?:[A-Z][A-Za-z]+-[\w-]+|evt-[\w-]+)$")  # entity ids and Event ids

_TIME_WORDS = {"timestamp", "time", "date", "datetime", "created", "updated", "when", "at"}
_PRIMARY = ["id", "name", "status", "status_since", "last_confirmed", "source", "confidence", "raw_evidence_ref", "conflict"]


def _hint(prop: str, lab: str, allowed: set[str]) -> str:
    if prop.lower() in _TIME_WORDS and lab != "Event":
        return "entities keep time in status_since / last_confirmed (and Team.position_since); only Event nodes have timestamp"
    near = [p for p in sorted(allowed) if prop.lower() in p.lower() or p.lower() in prop.lower()][:5]
    if near:
        return f"did you mean {', '.join(near)}?"
    extra = sorted(allowed - set(_PRIMARY) - set(COMMON))[:8]
    return f"{lab} has {', '.join(p for p in _PRIMARY if p in allowed)}" + (f", {', '.join(extra)}" if extra else "")


_NODE = re.compile(r"\((\w*)\s*(?::\s*`?(\w+)`?(?::`?\w+`?)*)?\s*(\{[^}]*\})?\s*\)")
_PATTERN = re.compile(
    r"\((?P<v1>\w*)\s*(?::\s*`?(?P<l1>\w+)`?(?::`?\w+`?)*)?\s*(?:\{[^}]*\})?\s*\)\s*"
    r"(?P<left><)?-\s*\[(?P<rv>\w*)\s*(?::\s*`?(?P<rel>\w+)`?(?:\|`?\w+`?)*)?\s*(?:\{[^}]*\})?\s*(?:\*[\d.]*)?\s*\]\s*-\s*(?P<right>>)?\s*"
    r"\((?P<v2>\w*)\s*(?::\s*`?(?P<l2>\w+)`?(?::`?\w+`?)*)?")
_PROP = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
_MAP_PROP = re.compile(r"\((\w*)\s*(?::\s*`?(\w+)`?)?\s*\{([^}]*)\}")
_ID_LITERAL = re.compile(r"\bid\s*[:=]\s*'([^']*)'|\.id\s*=\s*'([^']*)'|\bid\s*IN\s*\[([^\]]*)\]", re.IGNORECASE)
_RETURN = re.compile(r"\bRETURN\b(.*?)(?:\bORDER\b|\bLIMIT\b|\bSKIP\b|$)", re.IGNORECASE | re.S)
_FUNCS = {"count", "sum", "avg", "min", "max", "collect", "size", "head", "coalesce", "round", "point", "datetime", "duration", "toLower", "toUpper",
          "toString", "toInteger", "toFloat", "abs", "exists", "nodes", "relationships", "length", "labels", "type", "keys", "properties", "startNode", "endNode", "reduce", "any", "all", "none", "single", "distance"}


@dataclass
class Check:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _bind_labels(cypher: str) -> dict[str, str]:
    """variable -> label, from every (v:Label) occurrence."""
    labels: dict[str, str] = {}
    for m in _NODE.finditer(cypher):
        v, l = m.group(1), m.group(2)
        if v and l and v not in labels:
            labels[v] = l
    for m in re.finditer(r"\[(\w+)\s*:\s*`?(\w+)`?", cypher):
        labels.setdefault(m.group(1), m.group(2))
    return labels


ENUMS = {
    "source": {"drone_vision", "radio_asr", "gps", "sensor", "field_report", "seed_dataset"},
    "hazard_type": {"gas", "seismic", "water"}, "sensor_type": {"gas", "seismic", "water"},
    "facility_type": {"hospital", "shelter"}, "unit_type": {"rescue", "ambulance", "fire", "police"},
    "kind": {"building", "road", "team", "hazard", "sensor", "facility"},
}
_ENUM_LIT = re.compile(r"\b(?:\w+\.)?(source|hazard_type|sensor_type|facility_type|unit_type|kind)\s*(?:=|:)\s*'([^']*)'", re.IGNORECASE)
_BOUND = re.compile(r"[\(\[]\s*(\w+)\s*[:\)\]\{]|\bAS\s+`?(\w+)`?|\b(\w+)\s*=\s*\(|\bUNWIND\b.*?\bAS\s+(\w+)|\b(\w+)\s+IN\s+", re.IGNORECASE)
_KEYWORDS = {"n", "e", "t", "r", "b", "s", "h", "f", "x", "a", "p"}


def _bound_vars(cypher: str) -> set[str]:
    out = set()
    for m in _BOUND.finditer(cypher):
        out.update(g for g in m.groups() if g)
    return out


def check_cypher(cypher: str, known_ids: set[str] | None = None, name_to_id: dict[str, str] | None = None) -> Check:
    c = Check()
    labels = _bind_labels(cypher)
    # 0) variables used as var.prop but never bound (e.g. WHERE n.active with an anonymous -[:NEAR]->)
    bound = _bound_vars(cypher)
    for var in sorted({v for v, _ in _PROP.findall(cypher)} - bound - _FUNCS):
        if re.fullmatch(r"[a-z]\w{0,11}", var):
            c.problems.append(f"variable {var} is used but never bound; name it in the pattern, e.g. -[{var}:NEAR]-> or ({var}:Label)")
    # 0b) enumerated values
    for m in _ENUM_LIT.finditer(cypher):
        prop, val = m.group(1).lower(), m.group(2)
        if val not in ENUMS[prop]:
            c.problems.append(f"{prop} '{val}' is not a valid value; use one of {', '.join(sorted(ENUMS[prop]))}")
    # 1) relationship patterns: label compatibility + direction
    for m in _PATTERN.finditer(cypher):
        rel = (m.group("rel") or "").upper()
        if not rel or rel not in REL_SCHEMA:
            if rel:
                c.problems.append(f"relationship type {rel} does not exist; known: {', '.join(REL_SCHEMA)}")
            continue
        starts, ends, directed = REL_SCHEMA[rel]
        l1 = m.group("l1") or labels.get(m.group("v1") or "")
        l2 = m.group("l2") or labels.get(m.group("v2") or "")
        left, right = bool(m.group("left")), bool(m.group("right"))
        if left and not right:      # (a)<-[:REL]-(b): b is the start
            l1, l2 = l2, l1
        elif not left and not right and directed:
            pass                    # undirected match on a directed relationship is allowed (just less precise)
        if l1 and l1 not in starts and l1 != "Entity":
            fix = f"({'|'.join(sorted(starts - {'Entity'}))})-[:{rel}]->({'|'.join(sorted(ends - {'Entity'}))})"
            c.problems.append(f"{rel} never starts at {l1}; the pattern is {fix}" + (" (undirected)" if not directed else ""))
        if l2 and l2 not in ends and l2 != "Entity":
            fix = f"({'|'.join(sorted(starts - {'Entity'}))})-[:{rel}]->({'|'.join(sorted(ends - {'Entity'}))})"
            c.problems.append(f"{rel} never ends at {l2}; the pattern is {fix}")
    # 2) property names per label / relationship
    for var, prop in _PROP.findall(cypher):
        if var in _FUNCS or prop in ("id",):
            continue
        lab = labels.get(var)
        if not lab:
            continue
        allowed = NODE_PROPS.get(lab) or REL_PROPS.get(lab.upper())
        if allowed is not None and prop not in allowed:
            c.problems.append(f"property {var}.{prop} does not exist on {lab}; {_hint(prop, lab, allowed)}")
    for var, lab, body in _MAP_PROP.findall(cypher):
        lab = lab or labels.get(var)
        allowed = NODE_PROPS.get(lab) if lab else None
        if allowed:
            for key in re.findall(r"(\w+)\s*:", body):
                if key not in allowed:
                    c.problems.append(f"property {key} does not exist on {lab}")
    # 3) id literals must be real ids, not spoken names
    for m in _ID_LITERAL.finditer(cypher):
        vals = [m.group(1) or m.group(2)] if (m.group(1) or m.group(2)) else re.findall(r"'([^']*)'", m.group(3) or "")
        for v in vals:
            if v and not ID_RX.match(v):
                hint = f" -> use id '{name_to_id[v.lower()]}'" if name_to_id and v.lower() in name_to_id else " (ids look like Building-14, Road-Main, Team-Rescue4; match spoken names on name/aliases instead)"
                c.problems.append(f"'{v}' is not an entity id{hint}")
            elif known_ids and v not in known_ids and not v.startswith("evt-"):
                c.problems.append(f"id '{v}' does not exist in the graph")
    # 4) RETURN must expose an entity id (or a count/sum)
    rm = _RETURN.search(cypher)
    if rm:
        ret = rm.group(1)
        if not re.search(r"\bAS\s+`?id`?\b|\.id\b", ret, re.IGNORECASE) and not re.search(r"\b(count|sum|avg|min|max)\s*\(", ret, re.IGNORECASE):
            c.problems.append("RETURN must include the entity id as `id` (e.g. n.id AS id) so the answer can name what it is about")
    return c
