"""Pre-baked, LLM-free answers for the demo's scripted questions. Each intent = a regex, a Cypher
query (the same ones used as few-shot examples for the LLM path) and a small answer builder.
These must work on Friday no matter what the model does. Every statement in an answer comes from
rows these queries returned; when nothing was evaluated, the answer says so instead of guessing."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from ..contracts import Highlight, Provenance, QAResponse
from ..fusion.resolve import AmbiguousEntity, EntityResolver, clean_mention, normalize
from ..graph import GraphStore, to_native

KIND_TO_TYPE = {"building": "building", "road": "road", "team": "team", "hazard": "hazard", "sensor": "sensor", "facility": "facility"}
NOT_FOUND_CONF = 0.3  # answers below 0.6 let mode=auto try the LLM path instead


def hhmm(v: Any) -> str:
    v = to_native(v)
    return v.strftime("%H:%M:%SZ") if hasattr(v, "strftime") else "?"


def conf(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "?"


def prov(row: dict[str, Any], prefix: str = "") -> Provenance:
    return Provenance(source=row.get(prefix + "source") or "?", timestamp=to_native(row.get(prefix + "status_since") or row.get(prefix + "since")),
                      confidence=row.get(prefix + "confidence"), raw_evidence_ref=row.get(prefix + "raw_evidence_ref") or row.get(prefix + "evidence_ref"))


def dist(x: dict[str, Any]) -> str:
    d = x.get("distance_m")
    return f"{d:.0f} m from" if isinstance(d, (int, float)) else "near"


def _hl(kind: str, id: str, ids: list[str] | None = None, action: str = "fly_to") -> Highlight:
    seen, extra = {id}, []
    for i in ids or []:
        if i and i not in seen:
            seen.add(i); extra.append(i)
    return Highlight(type=KIND_TO_TYPE.get(kind, "none"), id=id, ids=extra, action=action)


_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45, "sixty": 60}
_TIME_PHRASE = re.compile(r"\b(?:last|past|previous)\s+(?:(?P<half>half\s+an?\s+hour)|(?:(?P<n>\d+|a|an|one|two|three|four|five|six|ten|fifteen|twenty|thirty|forty-five|sixty)\s*)?(?P<unit>minutes?|mins?|m\b|seconds?|secs?|s\b|hours?|hrs?|h\b))", re.I)


def parse_window(question: str) -> tuple[timedelta, str, bool]:
    """-> (window, label, explicit). 'last hour' = 1 h, 'last 2 hrs', 'past half an hour', 'last 30 seconds'."""
    m = _TIME_PHRASE.search(question)
    if not m:
        return timedelta(minutes=5), "5 minutes", False
    if m.group("half"):
        return timedelta(minutes=30), "30 minutes", True
    raw = (m.group("n") or "1").lower()
    n = int(raw) if raw.isdigit() else _WORD_NUM.get(raw, 1)
    u = m.group("unit").lower()
    if u.startswith("h"):
        return timedelta(hours=n), f"{n} hour{'s' if n != 1 else ''}", True
    if u.startswith("s"):
        return timedelta(seconds=n), f"{n} second{'s' if n != 1 else ''}", True
    return timedelta(minutes=n), f"{n} minute{'s' if n != 1 else ''}", True


# spoken source words -> event source values. "report" alone is NOT field_report ("what did radio report").
_SOURCE_WORDS = [("radio_asr", r"\b(radio|asr|transmissions?|dispatch\s+channel)\b"), ("drone_vision", r"\b(drones?|vision|camera|cameras|aerial|video|footage)\b"),
                 ("gps", r"\b(gps|trackers?|position\s+fix(?:es)?)\b"), ("sensor", r"\b(sensors?|gauges?|seismometers?|detectors?)\b"),
                 ("field_report", r"\b(field\s+reports?|ground\s+(?:teams?|crews?)\s+report)")]


def source_filters(question: str) -> list[str]:
    """Event sources the question restricts to, e.g. 'What did radio report...' -> ['radio_asr']."""
    return [src for src, rx in _SOURCE_WORDS if re.search(rx, question, re.I)]


class Intent:
    def __init__(self, name: str, pattern: str | list[str], handler: Callable[..., QAResponse], example: str):
        pats = [pattern] if isinstance(pattern, str) else pattern
        self.name, self.rxs, self.handler, self.example = name, [re.compile(p, re.I) for p in pats], handler, example

    def search(self, q: str) -> Optional[re.Match]:
        for rx in self.rxs:
            m = rx.search(q)
            if m:
                return m
        return None


class Fallback:
    def __init__(self, graph: GraphStore):
        self.g = graph
        self.resolver = EntityResolver(graph)
        U = r"(?P<unit>.+?)"
        D = r"(?P<dest>.+?)"
        self.intents: list[Intent] = [
            Intent("reachability", [
                   rf"\bcan\s+(?:we|you|units?)\s+(?:still\s+)?get\s+from\s+{U}\s+to\s+{D}\s*[?.!]*$",
                   rf"\bcan\s+{U}\s+(?:still\s+)?(?:reach|get\s+to|make\s+it\s+to|access|drive\s+to|go\s+to|travel\s+to)\s+{D}\s*[?.!]*$",
                   rf"\bis\s+{D}\s+(?:still\s+)?(?:reachable|accessible)\s+(?:from|for|by)\s+{U}\s*[?.!]*$",
                   rf"\b(?:route|path|way)\s+from\s+{U}\s+to\s+{D}\s*[?.!]*$"],
                   self.reachability, "Can Ambulance 2 still reach the hospital?"),
            Intent("most_danger", r"\b(most\s+danger|at\s+risk|in\s+danger|most\s+exposed|endangered|who.*\bdanger)", self.most_danger, "Who is in the most danger?"),
            Intent("blocked_roads", r"\b(which|what)\s+roads?\b.*\b(blocked|closed|impassable|open|passable|status)\b|\broads?\s+(?:status|closures?)\b"
                                    r"|\bblocked\s+roads?\b|\bstatus\s+of\s+(?:the\s+|all\s+)?roads\b|\bany\s+(?:road\s+)?closures\b",
                   self.blocked_roads, "Which roads are blocked?"),
            Intent("conflicts", r"\b(conflict|conflicting|disagree|contradict|dispute)", self.conflicts, "Are there any conflicting reports?"),
            Intent("entity_status", r"\b(?:what\s+happened\s+(?:to|at|with)|what(?:'s|s|\s+has)?\s+changed\s+(?:with|to|at|for)|status\s+of|what(?:'s|s|\s+is)\s+the\s+status\s+of"
                                    r"|tell\s+me\s+about|history\s+of|what(?:'s|s|\s+is)\s+going\s+on\s+(?:at|with))\s+(?P<entity>.+?)\s*[?.!]*$",
                   self.entity_status, "What happened to Building 14?"),
            Intent("recent_changes", r"\bwhat(?:'s|s|\s+has|\s+is)?\s+changed\b|\bwhat\s+happened\b|\bwhat(?:'s|s|\s+is)\s+new\b|\b(?:recent|latest)\s+(?:events|changes|updates)\b"
                                     r"|\bnew\s+events\b|\bany(?:thing)?\s+new\b|\b(?:last|past|previous)\s+(?:\d+\s*)?(?:minutes?|mins?|hours?|hrs?|seconds?)\b",
                   self.recent_changes, "What changed in the last 5 minutes?"),
            Intent("unit_location", rf"\bwhere(?:\s+(?:is|are)|'s|s)\s+{U}\s*[?.!]*$", self.unit_location, "Where is Rescue Team 4?"),
        ]

    # ------------------------------------------------------------------ dispatch
    def match(self, question: str) -> Optional[Intent]:
        q = question.strip()
        for it in self.intents:
            if it.search(q):
                return it
        return None

    def answer(self, question: str, now: datetime) -> Optional[QAResponse]:
        it = self.match(question)
        if not it:
            return None
        m = it.search(question.strip())
        try:
            resp = it.handler(m, now)
        except AmbiguousEntity as e:
            names = [self.g.get_entity(c)["name"] for c in e.candidates if self.g.get_entity(c)]
            resp = QAResponse(answer=f"'{e.text}' could mean {', '.join(names)}. Which one?", confidence=NOT_FOUND_CONF,
                              highlight=Highlight(type="none", ids=e.candidates, action="highlight"))
        resp.intent, resp.mode, resp.question, resp.as_of = it.name, "fallback", question, now
        return resp

    def examples(self) -> list[str]:
        return [it.example for it in self.intents]

    # intents whose query honours a source filter ("radio", "drone", ...) in the question
    SOURCE_AWARE = {"recent_changes", "read_only_refusal"}

    def covers(self, question: str, intent: Optional[str]) -> bool:
        """Can this pre-baked intent answer the question without silently dropping one of its filters?
        Used before substituting a pre-baked answer for a failed / empty LLM query."""
        if source_filters(question) and intent not in self.SOURCE_AWARE:
            return False
        return True

    def _resolve(self, text: str) -> Optional[dict[str, Any]]:
        return self.resolver.resolve(text.strip())

    def _not_found(self, text: str) -> QAResponse:
        return QAResponse(answer=f"I have no entity called '{clean_mention(text) or text}' in the graph.", confidence=NOT_FOUND_CONF)

    def _mentioned(self, question: str, labels: set[str]) -> list[dict[str, Any]]:
        """Entities whose name or alias appears verbatim (word-bounded) in the question, longest match first."""
        q = question.lower()
        hits = []
        for c in self.g.read_dicts("MATCH (n:Entity) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN n.id AS id, n.name AS name, n.aliases AS aliases", labels=list(labels)):
            for a in [c["name"], c["id"]] + list(c.get("aliases") or []):
                if a and len(a) > 2 and re.search(rf"(?<!\w){re.escape(a.lower())}(?!\w)", q):
                    hits.append((len(a), c["id"])); break
        seen, out = set(), []
        for _, i in sorted(hits, reverse=True):
            if i not in seen:
                seen.add(i); out.append(self.g.get_entity(i))
        return out

    # ------------------------------------------------------------------ most danger
    MOST_DANGER = """
MATCH (t:Team)-[n:NEAR]->(x) WHERE n.active = true
WITH t, collect({id: x.id, name: x.name, kind: x.kind, status: x.status, distance_m: n.distance_m, sources: n.sources,
                 evidence_refs: n.evidence_refs, since: n.since, confidence: n.confidence, level: x.level, unit: x.unit,
                 x_source: x.source, x_confidence: x.confidence, x_evidence: x.raw_evidence_ref}) AS near
WITH t, near, reduce(s = 0, r IN near | s + CASE WHEN r.kind = 'hazard' AND r.status = 'active' THEN 50
                                                  WHEN r.kind = 'building' AND r.status = 'collapsed' THEN 30
                                                  WHEN r.kind = 'building' AND r.status = 'damaged' THEN 15 ELSE 0 END) AS danger
WHERE danger > 0
RETURN t.id AS id, t.name AS name, t.status AS status, t.status_since AS status_since, t.source AS source, t.confidence AS confidence,
       t.raw_evidence_ref AS raw_evidence_ref, danger, near ORDER BY danger DESC LIMIT 10"""
    CIVILIAN_RISK = """
MATCH (b:Building) WHERE b.status IN ['collapsed', 'damaged']
OPTIONAL MATCH (h:Hazard)-[a:AFFECTS]->(b) WHERE a.active = true
OPTIONAL MATCH (t:Team)-[r:ASSIGNED_TO]->(b) WHERE r.active = true
RETURN b.id AS id, b.name AS name, b.status AS status, b.occupancy_est AS occupancy_est, b.status_since AS status_since, b.source AS source,
       b.confidence AS confidence, b.raw_evidence_ref AS raw_evidence_ref, collect(DISTINCT h.name) AS hazards, collect(DISTINCT t.name) AS units
ORDER BY CASE b.status WHEN 'collapsed' THEN 2 ELSE 1 END DESC, b.occupancy_est DESC LIMIT 10"""
    UNIT_NEAR = """
MATCH (t:Team {id: $id}) OPTIONAL MATCH (t)-[n:NEAR]->(x) WHERE n.active = true
RETURN t.id AS id, t.name AS name, t.status AS status, t.status_since AS status_since, t.source AS source, t.confidence AS confidence,
       t.raw_evidence_ref AS raw_evidence_ref,
       [r IN collect({id: x.id, name: x.name, kind: x.kind, status: x.status, distance_m: n.distance_m, sources: n.sources}) WHERE r.id IS NOT NULL] AS near"""

    def _reasons(self, near: list[dict[str, Any]]) -> list[str]:
        out = []
        for r in near:
            if r["kind"] == "hazard" and r["status"] == "active":
                lvl = f", {r['level']} {r['unit']}" if r.get("level") is not None else ""
                out.append(f"inside the {r['name']} zone ({dist(r).replace(' from', '')}{lvl}; {r.get('x_source')} {conf(r.get('x_confidence'))})")
            elif r["kind"] == "building" and r["status"] in ("collapsed", "damaged"):
                out.append(f"{dist(r)} {r['status']} {r['name']} ({', '.join(r.get('sources') or [])}; {r.get('x_source')} {conf(r.get('x_confidence'))})")
        return out

    def most_danger(self, m: re.Match, now: datetime) -> QAResponse:
        units = [to_native(r) for r in self.g.read_dicts(self.MOST_DANGER)]
        named_teams = self._mentioned(m.string, {"Team"})
        places = self._mentioned(m.string, {"Building", "Road", "Facility", "Hazard"})
        if named_teams:  # "Is Medic 2 at risk?" -> answer about that unit only
            t = named_teams[0]
            row = next((u for u in units if u["id"] == t["id"]), None)
            if row:
                return QAResponse(answer=f"Yes. {row['name']} ({row['status'].replace('_', ' ')} since {hhmm(row['status_since'])}) is " + "; ".join(self._reasons(row["near"])) + ".",
                                  highlight=_hl("team", row["id"], [r["id"] for r in row["near"]]), confidence=0.85, cypher=self.MOST_DANGER.strip(),
                                  evidence=[row], provenance=[prov(row)])
            info = to_native(self.g.read_dicts(self.UNIT_NEAR, id=t["id"])[0])
            near_txt = ", ".join(f"{dist(x)} {x['name']} ({x['status']})" for x in info["near"]) or "no tracked building, facility or hazard within range"
            return QAResponse(answer=f"No. {info['name']} is not near any active hazard or damaged structure in the graph; nearby: {near_txt}.",
                              highlight=_hl("team", info["id"], [x["id"] for x in info["near"]]), confidence=0.85, cypher=self.UNIT_NEAR.strip(), evidence=[info],
                              provenance=[prov(info)])
        if places:  # "Any units at risk near the bridge?" -> only units NEAR that place
            ids = {p["id"] for p in places}
            units = [u for u in units if any(r["id"] in ids for r in u["near"])]
            if not units:
                names = ", ".join(p["name"] for p in places)
                return QAResponse(answer=f"No unit near {names} is currently near an active hazard or damaged structure.", confidence=0.8,
                                  highlight=_hl(places[0]["kind"], places[0]["id"]), cypher=self.MOST_DANGER.strip())
        bldgs = [] if places else [to_native(r) for r in self.g.read_dicts(self.CIVILIAN_RISK)]
        if not units and not bldgs:
            return QAResponse(answer="No unit is currently near an active hazard or a damaged structure, and no building is reported damaged.", confidence=0.9,
                              cypher=self.MOST_DANGER.strip())
        parts, ids, provs = [], [], []
        if units:
            u = units[0]
            parts.append(f"Highest risk unit: {u['name']} ({u['status'].replace('_', ' ')} since {hhmm(u['status_since'])}) - " + "; ".join(self._reasons(u["near"])) + ".")
            ids += [u["id"]] + [r["id"] for r in u["near"]]; provs.append(prov(u))
            if len(units) > 1:
                parts.append("Also exposed: " + ", ".join(f"{x['name']} (score {x['danger']})" for x in units[1:4]) + ".")
                ids += [x["id"] for x in units[1:4]]
        if bldgs:
            b = bldgs[0]
            hz = f", {', '.join(h for h in b['hazards'] if h)} active" if any(b["hazards"]) else ""
            un = f"; {', '.join(x for x in b['units'] if x)} on scene" if any(b["units"]) else "; no unit assigned"
            parts.append(f"Civilians: {b['name']} {b['status']} since {hhmm(b['status_since'])} ({b['source']} {conf(b['confidence'])}), est. {b['occupancy_est']} occupants{hz}{un}.")
            ids.append(b["id"]); provs.append(prov(b))
        return QAResponse(answer=" ".join(parts), highlight=_hl("team" if units else "building", ids[0], ids), confidence=0.85,
                          cypher=self.MOST_DANGER.strip(), evidence=units + bldgs, provenance=provs)

    # ------------------------------------------------------------------ reachability
    PATHS = """
MATCH (s:Road {id: $start}), (d:Road {id: $dest})
MATCH p = (s)-[:CONNECTS_TO*0..6]-(d)
WITH p, [x IN nodes(p)[1..] WHERE x.status = 'blocked' AND coalesce(x.conflict, false) = false | x.id] AS hard_blocked,
     [x IN nodes(p) WHERE coalesce(x.conflict, false) = true | x.id] AS uncertain
RETURN [x IN nodes(p) | x.id] AS route, [x IN nodes(p) | x.name] AS names, length(p) AS hops, hard_blocked, uncertain,
       reduce(m = 0, r IN relationships(p) | m + coalesce(r.length_m, 0)) AS length_m
ORDER BY size(hard_blocked) ASC, size(uncertain) ASC, hops ASC, length_m ASC LIMIT 50"""
    BLOCKERS = """
MATCH (r:Road) WHERE r.status IN ['blocked', 'restricted'] OR r.conflict = true
RETURN r.id AS id, r.name AS name, r.status AS status, r.status_since AS status_since, r.source AS source, r.confidence AS confidence,
       r.raw_evidence_ref AS raw_evidence_ref, r.conflict AS conflict, r.conflict_claim AS conflict_claim, r.conflict_source AS conflict_source,
       r.conflict_confidence AS conflict_confidence, r.conflict_evidence_ref AS conflict_evidence_ref, r.conflict_since AS conflict_since
ORDER BY r.status_since DESC LIMIT 25"""

    def _road_for(self, ent: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The road an entity is on: itself (Road), ON_ROAD (Building/Facility), the road of the building
        a unit is NEAR (Team), else the nearest road by distance."""
        labels = set(ent.get("labels") or [])
        if "Road" in labels:
            return {"id": ent["id"], "name": ent["name"], "via": None}
        rows = self.g.read_dicts("MATCH (x:Entity {id:$id})-[:ON_ROAD]->(r:Road) RETURN r.id AS id, r.name AS name, null AS via LIMIT 1", id=ent["id"])
        if not rows and "Team" in labels:
            rows = self.g.read_dicts("MATCH (t:Team {id:$id})-[n:NEAR]->(x)-[:ON_ROAD]->(r:Road) WHERE n.active = true "
                                     "RETURN r.id AS id, r.name AS name, x.name AS via ORDER BY n.distance_m LIMIT 1", id=ent["id"])
        if not rows:
            rows = self.g.read_dicts("MATCH (x:Entity {id:$id}), (r:Road) WHERE x.location IS NOT NULL AND r.location IS NOT NULL "
                                     "RETURN r.id AS id, r.name AS name, null AS via ORDER BY point.distance(x.location, r.location) LIMIT 1", id=ent["id"])
        return rows[0] if rows else None

    def reachability(self, m: re.Match, now: datetime) -> QAResponse:
        unit_txt, dest_txt = m.group("unit").strip(), m.group("dest").strip()
        unit = self._resolve(unit_txt)
        if not unit:
            return self._not_found(unit_txt)
        dest = self._resolve(dest_txt)
        if not dest:
            for word, ftype in (("hospital", "hospital"), ("shelter|staging", "shelter")):
                if re.search(word, dest_txt, re.I):
                    rows = self.g.read_dicts("MATCH (f:Facility {facility_type:$t}) RETURN f.id AS id LIMIT 1", t=ftype)
                    dest = self.g.get_entity(rows[0]["id"]) if rows else None
        if not dest:
            return self._not_found(dest_txt)
        if unit["id"] == dest["id"]:
            return QAResponse(answer=f"{unit['name']} and the destination are the same entity.", confidence=NOT_FOUND_CONF)
        ukind = unit.get("kind") or "team"

        # already there? (fused NEAR edge)
        here = self.g.read_dicts("MATCH (:Entity {id:$u})-[n:NEAR]->(:Entity {id:$d}) WHERE n.active = true "
                                 "RETURN n.distance_m AS distance_m, n.sources AS sources, n.since AS since, n.confidence AS confidence", u=unit["id"], d=dest["id"])
        if here:
            h = to_native(here[0])
            return QAResponse(answer=f"{unit['name']} is already at {dest['name']} ({dist(h)} it since {hhmm(h['since'])}; {', '.join(h['sources'] or [])}, {conf(h['confidence'])}).",
                              highlight=_hl(ukind, unit["id"], [dest["id"]]), confidence=0.9, evidence=[h])

        start, goal = self._road_for(unit), self._road_for(dest)
        if not start or not goal:
            missing = unit["name"] if not start else dest["name"]
            return QAResponse(answer=f"I cannot place {missing} on the road network, so I cannot evaluate a route.", confidence=NOT_FOUND_CONF,
                              highlight=_hl(ukind, unit["id"], [dest["id"]]))
        paths = [to_native(r) for r in self.g.read_dicts(self.PATHS, start=start["id"], dest=goal["id"])]
        where = f"{unit['name']} is on {start['name']}" + (f" (near {start['via']})" if start.get("via") else "")
        road_by_id = {b["id"]: b for b in (to_native(r) for r in self.g.read_dicts(self.BLOCKERS))}
        if not paths:
            return QAResponse(answer=f"The road graph has no connection between {start['name']} and {goal['name']} within 6 segments, so I cannot confirm a route to {dest['name']}.",
                              highlight=_hl(ukind, unit["id"], [start["id"], goal["id"], dest["id"]]), confidence=0.5, cypher=self.PATHS.strip())
        on_paths = {i for p in paths for i in p["route"]}
        used = [road_by_id[i] for i in on_paths if i in road_by_id]
        provs = [prov(b) for b in used]
        start_note = f" Note: {start['name']} itself is reported {road_by_id[start['id']]['status']}." if start["id"] in road_by_id and road_by_id[start["id"]]["status"] == "blocked" else ""
        clean = [p for p in paths if not p["hard_blocked"] and not p["uncertain"]]
        if clean:
            r = clean[0]
            shorter = [p for p in paths if p["hops"] < r["hops"]]  # only roads on genuinely shorter routes were "avoided"
            avoided = [road_by_id[i] for i in sorted({i for p in shorter for i in p["hard_blocked"] + p["uncertain"]}) if i in road_by_id and i not in r["route"]]
            av = "; ".join(f"{b['name']} {b['status']} ({b['source']} {conf(b['confidence'])}, {hhmm(b['status_since'])})" for b in avoided)
            return QAResponse(answer=f"Yes. {where}; open route to {dest['name']}: {' -> '.join(r['names'])} ({r['hops']} segments, ~{r['length_m']:.0f} m)."
                                     + (f" Blocked roads avoided: {av}." if av else "") + start_note,
                              highlight=_hl(ukind, unit["id"], r["route"] + [dest["id"]]), confidence=0.85, cypher=self.PATHS.strip(), evidence=paths[:10], provenance=provs)
        hard_ids = sorted({i for p in paths for i in p["hard_blocked"]})
        hard_txt = "; ".join(f"{road_by_id[i]['name']} blocked ({road_by_id[i]['source']} {conf(road_by_id[i]['confidence'])}, {hhmm(road_by_id[i]['status_since'])})" for i in hard_ids if i in road_by_id)
        unsure = [p for p in paths if not p["hard_blocked"]]
        if unsure:
            r = unsure[0]
            unc = [road_by_id[u] for u in r["uncertain"] if u in road_by_id]
            unc_txt = "; ".join(f"{u['name']}: {u['source']} reports {u['status']} ({conf(u['confidence'])}, {hhmm(u['status_since'])}) but {u['conflict_source']} reports {u['conflict_claim']} ({conf(u['conflict_confidence'])}, {hhmm(u['conflict_since'])})" for u in unc)
            return QAResponse(answer=f"No confirmed route to {dest['name']}. {where}." + (f" Blocked: {hard_txt}." if hard_txt else "")
                                     + f" The only remaining route {' -> '.join(r['names'])} has conflicting reports - {unc_txt}. Recommend verifying before committing the unit." + start_note,
                              suggestion=f"If {', '.join(u['name'] for u in unc)} is verified passable: {' -> '.join(r['names'])} ({r['hops']} segments, ~{r['length_m']:.0f} m) - suggested, commander approval required.",
                              requires_commander_approval=True, highlight=_hl("road", r["uncertain"][0], r["route"] + [unit["id"], dest["id"]]), confidence=0.8,
                              cypher=self.PATHS.strip(), evidence=paths[:10], provenance=provs, warnings=["route depends on a road with conflicting reports"])
        return QAResponse(answer=f"No. {where}; all {len(paths)} candidate routes to {dest['name']} cross a blocked road: {hard_txt}." + start_note,
                          highlight=_hl(ukind, unit["id"], hard_ids + [dest["id"]]), confidence=0.85, cypher=self.PATHS.strip(), evidence=paths[:10], provenance=provs)

    # ------------------------------------------------------------------ roads
    OPEN_ROADS = "MATCH (r:Road) WHERE r.status = 'open' AND coalesce(r.conflict, false) = false RETURN r.id AS id, r.name AS name ORDER BY r.name"

    def blocked_roads(self, m: re.Match, now: datetime) -> QAResponse:
        rows = [to_native(r) for r in self.g.read_dicts(self.BLOCKERS)]
        open_rows = self.g.read_dicts(self.OPEN_ROADS)
        wants_open = bool(re.search(r"\b(open|passable|clear)\b", m.string, re.I))
        open_txt = f"Open: {', '.join(r['name'] for r in open_rows)}." if open_rows else "No road is confirmed open."
        if not rows:
            return QAResponse(answer="All roads are reported open. " + open_txt, confidence=0.9, cypher=self.BLOCKERS.strip())
        bits = []
        for r in rows:
            s = f"{r['name']} {r['status']} since {hhmm(r['status_since'])} ({r['source']} {conf(r['confidence'])})"
            if r.get("conflict"):
                s += f" - CONFLICTING: {r['conflict_source']} reports {r['conflict_claim']} ({conf(r['conflict_confidence'])}, {hhmm(r['conflict_since'])})"
            bits.append(s)
        answer = (open_txt + " Not open: " if wants_open else "") + "; ".join(bits) + "." + ("" if wants_open else " " + open_txt)
        ids = [r["id"] for r in rows] + ([r["id"] for r in open_rows] if wants_open else [])
        return QAResponse(answer=answer, highlight=_hl("road", ids[0], ids), confidence=0.9,
                          cypher=self.BLOCKERS.strip(), evidence=rows + open_rows, provenance=[prov(r) for r in rows])

    # ------------------------------------------------------------------ recent changes
    RECENT = """
MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $since AND ($sources IS NULL OR e.source IN $sources)
RETURN n.id AS id, n.name AS name, n.kind AS kind, e.timestamp AS timestamp, e.source AS source, e.claim AS claim, e.confidence AS confidence,
       e.applied AS applied, e.action AS action, e.note AS note, e.raw_evidence_ref AS raw_evidence_ref ORDER BY e.timestamp DESC LIMIT 25"""

    def recent_changes(self, m: re.Match, now: datetime) -> QAResponse:
        window, label, explicit = parse_window(m.string)
        sources = source_filters(m.string) or None
        rows = [to_native(r) for r in self.g.read_dicts(self.RECENT, since=now - window, sources=sources)]
        prefix = "" if explicit else "No time window given - showing the last 5 minutes. "
        what = f"{' or '.join(sources)} events" if sources else "events"
        if not rows:
            return QAResponse(answer=f"{prefix}No {what} in the last {label} (scenario clock {hhmm(now)}).", confidence=0.9, cypher=self.RECENT.strip())
        bits = [f"{hhmm(r['timestamp'])} {r['name']} {r['claim'].replace('_', ' ')} ({r['source']} {conf(r['confidence'])}{'' if r['applied'] else ', not applied: ' + str(r['note'])})" for r in rows]
        changed = [r["id"] for r in rows if r["applied"] and r["action"] in ("override", "conflict", "create", "confirm_competing")]
        return QAResponse(answer=f"{prefix}{len(rows)} {what} in the last {label}: " + "; ".join(bits) + ".",
                          highlight=_hl(rows[0]["kind"], rows[0]["id"], changed), confidence=0.9, cypher=self.RECENT.strip(), evidence=rows,
                          provenance=[Provenance(source=r["source"], timestamp=r["timestamp"], confidence=r["confidence"], raw_evidence_ref=r["raw_evidence_ref"]) for r in rows])

    # ------------------------------------------------------------------ conflicts
    CONFLICTS = """
MATCH (n:Entity) WHERE n.conflict = true
RETURN n.id AS id, n.name AS name, n.kind AS kind, n.status AS status, n.status_since AS status_since, n.source AS source, n.confidence AS confidence,
       n.raw_evidence_ref AS raw_evidence_ref, n.conflict_claim AS conflict_claim, n.conflict_source AS conflict_source, n.conflict_confidence AS conflict_confidence,
       n.conflict_evidence_ref AS conflict_evidence_ref, n.conflict_since AS conflict_since ORDER BY n.conflict_since DESC LIMIT 25"""

    def conflicts(self, m: re.Match, now: datetime) -> QAResponse:
        rows = [to_native(r) for r in self.g.read_dicts(self.CONFLICTS)]
        if not rows:
            return QAResponse(answer="No conflicting reports at the moment.", confidence=0.9, cypher=self.CONFLICTS.strip())
        bits = [f"{r['name']}: {r['source']} reports {r['status']} ({conf(r['confidence'])}, {hhmm(r['status_since'])}, {r['raw_evidence_ref']}) vs {r['conflict_source']} reports {r['conflict_claim']} ({conf(r['conflict_confidence'])}, {hhmm(r['conflict_since'])}, {r['conflict_evidence_ref']})" for r in rows]
        return QAResponse(answer=f"{len(rows)} conflicting report(s) - " + "; ".join(bits) + ". Status follows the higher-confidence source; both are retained until a third source confirms.",
                          highlight=_hl(rows[0]["kind"], rows[0]["id"], [r["id"] for r in rows]), confidence=0.9, cypher=self.CONFLICTS.strip(), evidence=rows,
                          provenance=[prov(r) for r in rows] + [Provenance(source=r["conflict_source"], timestamp=r["conflict_since"], confidence=r["conflict_confidence"], raw_evidence_ref=r["conflict_evidence_ref"]) for r in rows])

    # ------------------------------------------------------------------ where is
    UNIT = """
MATCH (t:Team {id: $id})
OPTIONAL MATCH (t)-[n:NEAR]->(x) WHERE n.active = true
OPTIONAL MATCH (t)-[a:ASSIGNED_TO]->(y) WHERE a.active = true
RETURN t.id AS id, t.name AS name, t.status AS status, t.status_since AS status_since, t.source AS source, t.confidence AS confidence,
       t.raw_evidence_ref AS raw_evidence_ref, t.lat AS lat, t.lon AS lon, t.position_since AS position_since, t.position_source AS position_source,
       t.position_evidence_ref AS position_evidence_ref,
       collect(DISTINCT {id: x.id, name: x.name, kind: x.kind, status: x.status, distance_m: n.distance_m, sources: n.sources, since: n.since}) AS near,
       collect(DISTINCT y.name) AS assigned LIMIT 1"""

    def unit_location(self, m: re.Match, now: datetime) -> QAResponse:
        unit_txt = m.group("unit").strip()
        unit = self._resolve(unit_txt)
        if not unit:
            return self._not_found(unit_txt)
        if "Team" not in unit["labels"]:
            return self.entity_status(m, now, entity_override=unit)
        r = to_native(self.g.read_dicts(self.UNIT, id=unit["id"])[0])
        near = [x for x in r["near"] if x.get("id")]
        near_txt = ", ".join(f"{dist(x)} {x['name']} ({x['status']}; {', '.join(x.get('sources') or [])})" for x in near) or "no tracked entity within range"
        assigned = ", ".join(a for a in r["assigned"] if a)
        pos = f"Position {r['lat']:.5f}, {r['lon']:.5f} at {hhmm(r['position_since'])} via {r['position_source']}" if r.get("lat") is not None else "No position reported"
        ans = (f"{r['name']}: {r['status'].replace('_', ' ')} since {hhmm(r['status_since'])} ({r['source']} {conf(r['confidence'])})"
               + (f", assigned to {assigned}" if assigned else "") + f". {pos}; {near_txt}.")
        return QAResponse(answer=ans, highlight=_hl("team", r["id"], [x["id"] for x in near]), confidence=0.9, cypher=self.UNIT.strip(), evidence=[r],
                          provenance=[prov(r), Provenance(source=r["position_source"] or "?", timestamp=r["position_since"], raw_evidence_ref=r["position_evidence_ref"])])

    # ------------------------------------------------------------------ what happened to X
    ENTITY = """
MATCH (n:Entity {id: $id})
OPTIONAL MATCH (e:Event)-[:ABOUT]->(n)
WITH n, e ORDER BY e.timestamp DESC
WITH n, collect({timestamp: e.timestamp, source: e.source, claim: e.claim, confidence: e.confidence, applied: e.applied, action: e.action, raw_evidence_ref: e.raw_evidence_ref})[0..6] AS timeline
OPTIONAL MATCH (n)-[r]-(o:Entity) WHERE type(r) IN ['NEAR', 'AFFECTS', 'ASSIGNED_TO', 'DETECTED_BY', 'MONITORS'] AND coalesce(r.active, true) = true
RETURN n.id AS id, n.name AS name, n.kind AS kind, n.status AS status, n.status_since AS status_since, n.last_confirmed AS last_confirmed, n.source AS source,
       n.confidence AS confidence, n.raw_evidence_ref AS raw_evidence_ref, n.conflict AS conflict, n.conflict_claim AS conflict_claim, n.conflict_source AS conflict_source,
       n.conflict_confidence AS conflict_confidence, n.occupancy_est AS occupancy_est, n.reading AS reading, n.unit AS unit, timeline,
       collect(DISTINCT {rel: type(r), other: o.name, other_id: o.id, outgoing: startNode(r) = n}) AS links LIMIT 1"""
    _LINK_TEXT = {("NEAR", True): "near {o}", ("NEAR", False): "{o} is near it", ("AFFECTS", True): "affects {o}", ("AFFECTS", False): "affected by {o}",
                  ("ASSIGNED_TO", True): "assigned to {o}", ("ASSIGNED_TO", False): "{o} assigned to it", ("DETECTED_BY", True): "detected by {o}",
                  ("DETECTED_BY", False): "detected {o}", ("MONITORS", True): "monitors {o}", ("MONITORS", False): "monitored by {o}"}

    def entity_status(self, m: re.Match, now: datetime, entity_override: dict[str, Any] | None = None) -> QAResponse:
        txt = m.group("entity").strip() if "entity" in m.groupdict() and m.group("entity") else m.group("unit").strip()
        ent = entity_override or self._resolve(txt)
        if not ent:
            return self._not_found(txt)
        r = to_native(self.g.read_dicts(self.ENTITY, id=ent["id"])[0])
        tl = [t for t in r["timeline"] if t.get("timestamp")]
        tl_txt = "; ".join(f"{hhmm(t['timestamp'])} {t['source']} said {t['claim'].replace('_', ' ')} ({conf(t['confidence'])}{'' if t['applied'] else ', not applied'})" for t in tl)
        links = [l for l in r["links"] if l.get("other")]
        links_txt = ", ".join(self._LINK_TEXT.get((l["rel"], bool(l["outgoing"])), "{o}").format(o=l["other"]) for l in links)
        ans = f"{r['name']} is {str(r['status']).replace('_', ' ')} since {hhmm(r['status_since'])}, last confirmed {hhmm(r['last_confirmed'])} ({r['source']} {conf(r['confidence'])}, {r['raw_evidence_ref']})"
        if r.get("occupancy_est") is not None:
            ans += f", est. {r['occupancy_est']} occupants"
        if r.get("reading") is not None:
            ans += f", reading {r['reading']} {r.get('unit') or ''}".rstrip()
        if r.get("conflict"):
            ans += f". CONFLICTING: {r['conflict_source']} reports {r['conflict_claim']} ({conf(r['conflict_confidence'])})"
        ans += f". Timeline: {tl_txt or 'seed only'}." + (f" Links: {links_txt}." if links_txt else "")
        return QAResponse(answer=ans, highlight=_hl(r["kind"], r["id"], [l["other_id"] for l in links]), confidence=0.9, cypher=self.ENTITY.strip(), evidence=[r],
                          provenance=[prov(r)] + [Provenance(source=t["source"], timestamp=t["timestamp"], confidence=t["confidence"], raw_evidence_ref=t["raw_evidence_ref"]) for t in tl])
