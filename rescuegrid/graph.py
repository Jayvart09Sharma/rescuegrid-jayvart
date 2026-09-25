"""Neo4j access layer. Every Cypher statement that touches the database lives here; sources,
rules and the Q&A layer call these methods and never build write queries themselves."""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from neo4j import Driver, GraphDatabase, Record

from .config import Settings, settings as default_settings

_STATEMENT_SPLIT = re.compile(r";\s*(?:\n|$)")
_SAFE_LABEL = re.compile(r"^[A-Za-z]+$")

ENTITY_FIELDS = (
    "n.id AS id, n.kind AS kind, labels(n) AS labels, n.name AS name, n.aliases AS aliases, n.lat AS lat, n.lon AS lon, "
    "n.status AS status, n.status_since AS status_since, n.last_confirmed AS last_confirmed, n.source AS source, "
    "n.confidence AS confidence, n.raw_evidence_ref AS raw_evidence_ref, n.status_event_id AS status_event_id, "
    "n.conflict AS conflict, n.conflict_claim AS conflict_claim, n.conflict_source AS conflict_source, "
    "n.conflict_confidence AS conflict_confidence, n.conflict_evidence_ref AS conflict_evidence_ref, "
    "n.conflict_since AS conflict_since, n.conflict_event_id AS conflict_event_id, n.sensor_type AS sensor_type, "
    "n.unit_type AS unit_type, n.facility_type AS facility_type, n.position_source AS position_source, "
    "n.position_evidence_ref AS position_evidence_ref, n.position_since AS position_since, n.auto_created AS auto_created, "
    "n.confirmed_sources AS confirmed_sources, n.last_confirmed_source AS last_confirmed_source, n.radius_m AS radius_m"
)


def split_cypher_file(text: str) -> list[str]:
    """Split a .cypher file into statements on ';' at line end, dropping comment-only chunks."""
    out = []
    for chunk in _STATEMENT_SPLIT.split(text):
        lines = [l for l in chunk.splitlines() if l.strip() and not l.strip().startswith("//")]
        if lines:
            out.append("\n".join(lines).strip())
    return out


def to_native(v: Any) -> Any:
    """neo4j.time.DateTime -> datetime, recursively for dicts/lists (for JSON output)."""
    if hasattr(v, "to_native"):
        return v.to_native()
    if isinstance(v, dict):
        return {k: to_native(x) for k, x in v.items()}
    if isinstance(v, list):
        return [to_native(x) for x in v]
    return v


class GraphStore:
    def __init__(self, cfg: Settings | None = None, driver: Driver | None = None):
        self.cfg = cfg or default_settings
        self._driver = driver or GraphDatabase.driver(self.cfg.neo4j_uri, auth=(self.cfg.neo4j_user, self.cfg.neo4j_password),
                                        notifications_min_severity="OFF")  # queries reference keys that may not exist yet
        self.database = self.cfg.neo4j_database
        self._tx = None  # set while inside transaction(): all reads/writes then share one transaction

    @contextmanager
    def transaction(self) -> Iterator["GraphStore"]:
        """Unit of work: every write()/read() inside the block runs in ONE transaction that commits at
        the end or rolls back on any exception, so an event is applied fully or not at all."""
        if self._tx is not None:  # nested use: join the outer transaction
            yield self
            return
        with self._driver.session(database=self.database) as session:
            tx = session.begin_transaction()
            self._tx = tx
            try:
                yield self
                tx.commit()
            except BaseException:
                tx.rollback()
                raise
            finally:
                self._tx = None

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def ping(self) -> bool:
        self._driver.verify_connectivity()
        return True

    # ------------------------------------------------------------------ generic
    def write(self, cypher: str, **params: Any) -> list[Record]:
        if self._tx is not None:
            return list(self._tx.run(cypher, **params))
        with self._driver.session(database=self.database) as s:
            return s.execute_write(lambda tx: list(tx.run(cypher, **params)))

    def read(self, cypher: str, **params: Any) -> list[Record]:
        if self._tx is not None:
            return list(self._tx.run(cypher, **params))
        with self._driver.session(database=self.database) as s:
            return s.execute_read(lambda tx: list(tx.run(cypher, **params)))

    def read_dicts(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        return [r.data() for r in self.read(cypher, **params)]

    def write_dicts(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        return [r.data() for r in self.write(cypher, **params)]

    def run_file(self, path: Path) -> int:
        stmts = split_cypher_file(Path(path).read_text())
        with self._driver.session(database=self.database) as s:
            for stmt in stmts:
                s.run(stmt).consume()
        return len(stmts)

    # ------------------------------------------------------------------ setup
    def apply_schema(self) -> int:
        return self.run_file(self.cfg.schema_file)

    def seed_static_world(self) -> int:
        return self.run_file(self.cfg.seed_file)

    def wipe(self) -> None:
        """Delete ALL data (not the schema). Local dev only."""
        self.write("MATCH (n) DETACH DELETE n")

    def reset(self) -> None:
        self.wipe(); self.apply_schema(); self.seed_static_world()

    # ------------------------------------------------------------------ introspection
    def counts(self) -> dict[str, int]:
        labels = self.read_dicts("MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS c ORDER BY l")
        rels = self.read_dicts("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY t")
        return {**{f"node:{d['label']}": d["c"] for d in labels}, **{f"rel:{d['t']}": d["c"] for d in rels}}

    def constraints(self) -> list[str]:
        return [r["name"] for r in self.read("SHOW CONSTRAINTS")]

    def indexes(self) -> list[str]:
        return [f"{r['name']}({r['type']})" for r in self.read("SHOW INDEXES")]

    def replay_now(self) -> Optional[datetime]:
        """The scenario clock: timestamp of the newest ingested event."""
        rows = self.read_dicts("MATCH (e:Event) RETURN max(e.timestamp) AS t")
        return to_native(rows[0]["t"]) if rows and rows[0]["t"] else None

    # ------------------------------------------------------------------ entities
    def get_entity(self, id: str) -> Optional[dict[str, Any]]:
        rows = self.read_dicts(f"MATCH (n:Entity {{id:$id}}) RETURN {ENTITY_FIELDS}", id=id)
        return rows[0] if rows else None

    def all_entity_names(self) -> list[dict[str, Any]]:
        return self.read_dicts("MATCH (n:Entity) RETURN n.id AS id, n.name AS name, n.aliases AS aliases, n.kind AS kind")

    def fulltext_phrase(self, phrase: str) -> list[dict[str, Any]]:
        q = '"' + re.sub(r'["\\]', " ", phrase).strip() + '"'
        return self.read_dicts(
            "CALL db.index.fulltext.queryNodes('entity_search', $q) YIELD node, score "
            "RETURN node.id AS id, labels(node) AS labels, score ORDER BY score DESC LIMIT 3", q=q)

    def fulltext_terms(self, text: str) -> list[dict[str, Any]]:
        """AND-of-terms fallback: 'gas sensor' -> gas AND sensor (every term must hit)."""
        terms = [t for t in re.findall(r"[A-Za-z0-9]+", text) if len(t) > 1]
        if not terms:
            return []
        q = " AND ".join(terms)
        return self.read_dicts(
            "CALL db.index.fulltext.queryNodes('entity_search', $q) YIELD node, score "
            "RETURN node.id AS id, labels(node) AS labels, score ORDER BY score DESC LIMIT 3", q=q)

    def create_entity(self, id: str, label: str, *, name: str, ts: datetime, source: str, confidence: float,
                      ref: str, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
        if not _SAFE_LABEL.match(label):
            raise ValueError(f"bad label {label!r}")
        labels = ":Entity" + (f":{label}" if label != "Entity" else "")
        self.write(
            f"CREATE (n{labels} {{id:$id}}) "
            "SET n.kind=$kind, n.name=$name, n.aliases=[$name], n.status='unknown', n.status_since=$ts, n.last_confirmed=$ts, "
            "n.source=$source, n.confidence=$confidence, n.raw_evidence_ref=$ref, n.auto_created=true, n.seeded=false, "
            "n.created_at=datetime(), n.updated_at=datetime(), n.lat=$lat, n.lon=$lon, "
            "n.location = CASE WHEN $lat IS NULL OR $lon IS NULL THEN null ELSE point({latitude:$lat, longitude:$lon}) END",
            id=id, kind=label.lower(), name=name, ts=ts, source=source, confidence=confidence, ref=ref, lat=lat, lon=lon)
        return self.get_entity(id)  # type: ignore[return-value]

    def set_props(self, id: str, props: dict[str, Any]) -> None:
        self.write("MATCH (n:Entity {id:$id}) SET n += $props, n.updated_at=datetime()", id=id, props=props)

    # ------------------------------------------------------------------ events (audit trail)
    def event_exists(self, event_id: str) -> bool:
        return bool(self.read_dicts("MATCH (e:Event {id:$id}) RETURN e.id AS id", id=event_id))

    def record_event(self, ev, entity_id: Optional[str], applied: bool, note: str, action: str) -> None:
        text = ev.details.get("transcript") or ev.details.get("text") or ev.details.get("note")
        self.write(
            "MERGE (e:Event {id:$id}) "
            "SET e.source=$source, e.timestamp=$ts, e.confidence=$conf, e.entity=$entity, e.entity_id=$entity_id, "
            "e.claim=$claim, e.raw_evidence_ref=$ref, e.details=$details, e.text=$text, e.ingested_at=datetime(), "
            "e.applied=$applied, e.action=$action, e.note=$note "
            "WITH e OPTIONAL MATCH (n:Entity {id:$entity_id}) "
            "FOREACH (_ IN CASE WHEN n IS NULL THEN [] ELSE [1] END | MERGE (e)-[:ABOUT]->(n))",
            id=ev.event_id, source=ev.source, ts=ev.timestamp, conf=ev.confidence, entity=ev.entity, entity_id=entity_id,
            claim=ev.claim, ref=ev.raw_evidence_ref, details=ev.details_json, text=text, applied=applied, action=action, note=note)

    def link_conflict(self, event_a: str, event_b: Optional[str]) -> None:
        if not event_b:
            return
        self.write("MATCH (x:Event {id:$a}), (y:Event {id:$b}) MERGE (x)-[r:CONFLICTS_WITH]->(y) SET r.detected_at=datetime()", a=event_a, b=event_b)

    # ------------------------------------------------------------------ status + provenance
    def override_status(self, id: str, ev) -> None:
        self.write(
            "MATCH (n:Entity {id:$id}) "
            "SET n.previous_status=n.status, n.previous_status_since=n.status_since, n.previous_source=n.source, "
            "n.status=$claim, n.status_since=$ts, n.last_confirmed=$ts, n.source=$source, n.confidence=$conf, "
            "n.raw_evidence_ref=$ref, n.status_event_id=$eid, n.confirmations=0, n.confirmed_sources=[$source], "
            "n.last_confirmed_source=$source, n.last_confirmed_evidence_ref=$ref, "
            "n.conflict=false, n.conflict_claim=null, n.conflict_source=null, n.conflict_confidence=null, "
            "n.conflict_evidence_ref=null, n.conflict_since=null, n.conflict_event_id=null, n.updated_at=datetime()",
            id=id, claim=ev.claim, ts=ev.timestamp, source=ev.source, conf=ev.confidence, ref=ev.raw_evidence_ref, eid=ev.event_id)

    def confirm_status(self, id: str, ev) -> None:
        """Same claim again: bump last_confirmed, keep the higher confidence. If the current status
        came from the static seed, the first live observation takes over as its provenance (the seed
        is a prior, not evidence) while status_since stays at the seed time."""
        self.write(
            "MATCH (n:Entity {id:$id}) "
            "WITH n, (n.source = 'seed_dataset') AS from_seed "
            "SET n.last_confirmed = CASE WHEN n.last_confirmed IS NULL OR $ts > n.last_confirmed THEN $ts ELSE n.last_confirmed END, "
            "n.confidence = CASE WHEN from_seed THEN $conf WHEN $conf > coalesce(n.confidence, 0.0) THEN $conf ELSE n.confidence END, "
            "n.source = CASE WHEN from_seed THEN $source ELSE n.source END, "
            "n.raw_evidence_ref = CASE WHEN from_seed THEN $ref ELSE n.raw_evidence_ref END, "
            "n.status_event_id = CASE WHEN from_seed THEN $eid ELSE n.status_event_id END, "
            "n.confirmations = CASE WHEN from_seed THEN 1 ELSE coalesce(n.confirmations, 0) + 1 END, "
            "n.confirmed_sources = CASE WHEN from_seed THEN [$source] WHEN $source IN coalesce(n.confirmed_sources, []) THEN n.confirmed_sources ELSE coalesce(n.confirmed_sources, []) + $source END, "
            "n.last_confirmed_source=$source, n.last_confirmed_evidence_ref=$ref, n.updated_at=datetime()",
            id=id, ts=ev.timestamp, conf=ev.confidence, source=ev.source, ref=ev.raw_evidence_ref, eid=ev.event_id)

    def set_conflict(self, id: str, *, claim: str, source: str, confidence: float, ref: str, since: datetime, event_id: Optional[str]) -> None:
        self.write(
            "MATCH (n:Entity {id:$id}) SET n.conflict=true, n.conflict_claim=$claim, n.conflict_source=$source, "
            "n.conflict_confidence=$conf, n.conflict_evidence_ref=$ref, n.conflict_since=$since, n.conflict_event_id=$eid, "
            "n.conflict_detected_at=datetime(), n.updated_at=datetime()",
            id=id, claim=claim, source=source, conf=confidence, ref=ref, since=since, eid=event_id)

    def clear_conflict(self, id: str, ts: datetime, note: str) -> None:
        self.write(
            "MATCH (n:Entity {id:$id}) SET n.conflict=false, n.conflict_resolved_at=$ts, n.conflict_resolution=$note, "
            "n.conflict_claim=null, n.conflict_source=null, n.conflict_confidence=null, n.conflict_evidence_ref=null, "
            "n.conflict_since=null, n.conflict_event_id=null, n.updated_at=datetime()", id=id, ts=ts, note=note)

    # ------------------------------------------------------------------ positions + NEAR
    def update_position(self, id: str, *, lat: float, lon: float, ts: datetime, source: str, ref: str,
                        event_id: str, speed: float | None = None) -> bool:
        """Returns False (and changes nothing) when the fix is older than the position we already hold."""
        rows = self.write_dicts(
            "MATCH (n:Entity {id:$id}) WHERE n.position_since IS NULL OR $ts >= n.position_since "
            "SET n.lat=$lat, n.lon=$lon, n.location=point({latitude:$lat, longitude:$lon}), "
            "n.position_since=$ts, n.position_source=$source, n.position_evidence_ref=$ref, n.position_event_id=$eid, "
            "n.speed_mps=$speed, n.updated_at=datetime() RETURN n.id AS id",
            id=id, lat=lat, lon=lon, ts=ts, source=source, ref=ref, eid=event_id, speed=speed)
        return bool(rows)

    def entities_within(self, lat: float, lon: float, radius_m: float, *, exclude_id: str = "",
                        labels: tuple[str, ...] = ("Building", "Facility", "Hazard")) -> list[dict[str, Any]]:
        return self.read_dicts(
            "WITH point({latitude:$lat, longitude:$lon}) AS p "
            "MATCH (e:Entity) WHERE point.distance(e.location, p) <= $maxr AND e.id <> $exclude "
            "AND any(l IN labels(e) WHERE l IN $labels) AND coalesce(e.status, '') <> 'cleared' "
            "WITH e, point.distance(e.location, p) AS d WHERE d <= coalesce(e.radius_m, $radius) "
            "RETURN e.id AS id, e.kind AS kind, e.name AS name, e.status AS status, round(d, 1) AS distance_m, "
            "coalesce(e.radius_m, $radius) AS radius_m ORDER BY d",
            lat=lat, lon=lon, radius=radius_m, maxr=max(radius_m, 1000.0), exclude=exclude_id, labels=list(labels))

    def teams_within(self, lat: float, lon: float, radius_m: float) -> list[dict[str, Any]]:
        return self.read_dicts(
            "WITH point({latitude:$lat, longitude:$lon}) AS p MATCH (t:Team) WHERE point.distance(t.location, p) <= $radius "
            "WITH t, point.distance(t.location, p) AS d "
            "RETURN t.id AS id, t.name AS name, round(d, 1) AS distance_m, t.position_source AS position_source, "
            "t.position_evidence_ref AS position_evidence_ref ORDER BY d", lat=lat, lon=lon, radius=radius_m)

    def upsert_near(self, team_id: str, target_id: str, *, ts: datetime, source: str, confidence: float, ref: str,
                    event_id: str, distance_m: float | None = None) -> dict[str, Any]:
        """Create/refresh the NEAR edge. Older-than-known evidence is ignored (returns {'stale': True}).
        Re-activating an ended edge starts a fresh epoch: sources/evidence/confidence restart."""
        rows = self.write_dicts(
            "MATCH (t:Entity {id:$team}), (e:Entity {id:$target}) "
            "MERGE (t)-[r:NEAR]->(e) "
            "ON CREATE SET r.active=false, r.sources=[], r.evidence_refs=[], r.event_ids=[], r.confidence=0.0, r.epochs=0 "
            "WITH r WHERE r.last_confirmed IS NULL OR $ts >= r.last_confirmed "
            "SET r.since = CASE WHEN r.active THEN r.since ELSE $ts END, "
            "    r.epochs = CASE WHEN r.active THEN r.epochs ELSE r.epochs + 1 END, "
            "    r.sources = CASE WHEN r.active THEN r.sources ELSE [] END, "
            "    r.evidence_refs = CASE WHEN r.active THEN r.evidence_refs ELSE [] END, "
            "    r.event_ids = CASE WHEN r.active THEN r.event_ids ELSE [] END, "
            "    r.confidence = CASE WHEN r.active THEN r.confidence ELSE 0.0 END "
            "WITH r, ($source IN r.sources) AS seen "
            "SET r.active=true, r.ended_at=null, r.ended_by_event=null, r.last_confirmed=$ts, "
            "r.distance_m = CASE WHEN $distance IS NULL THEN r.distance_m ELSE $distance END, "
            "r.confidence = CASE WHEN seen THEN (CASE WHEN $conf > r.confidence THEN $conf ELSE r.confidence END) "
            "                    ELSE 1.0 - (1.0 - r.confidence) * (1.0 - $conf) END, "
            "r.sources = CASE WHEN seen THEN r.sources ELSE r.sources + $source END, "
            "r.evidence_refs = r.evidence_refs + $ref, r.event_ids = r.event_ids + $eid, r.last_source=$source "
            "RETURN r.since AS since, r.last_confirmed AS last_confirmed, r.sources AS sources, r.confidence AS confidence, r.distance_m AS distance_m",
            team=team_id, target=target_id, ts=ts, source=source, conf=confidence, ref=ref, eid=event_id, distance=distance_m)
        return rows[0] if rows else {"stale": True}

    def expire_near_beyond(self, team_id: str, radius_m: float, ts: datetime, event_id: str) -> list[str]:
        rows = self.write_dicts(
            "MATCH (t:Entity {id:$team})-[r:NEAR]->(e:Entity) "
            "WHERE r.active = true AND t.location IS NOT NULL AND e.location IS NOT NULL "
            "AND (r.last_confirmed IS NULL OR $ts >= r.last_confirmed) "
            "AND point.distance(t.location, e.location) > coalesce(e.radius_m, $radius) "
            "SET r.active=false, r.ended_at=$ts, r.ended_by_event=$eid RETURN e.id AS id", team=team_id, radius=radius_m, ts=ts, eid=event_id)
        return [r["id"] for r in rows]

    # ------------------------------------------------------------------ hazards
    def upsert_hazard(self, hazard_id: str, *, name: str, hazard_type: str, level: float | None, unit: str | None,
                      ts: datetime, source: str, confidence: float, ref: str, event_id: str, sensor_id: str,
                      lat: float | None, lon: float | None, radius_m: float) -> dict[str, Any]:
        rows = self.write_dicts(
            "MERGE (h:Entity:Hazard {id:$id}) "
            "ON CREATE SET h.kind='hazard', h.name=$name, h.aliases=[$name], h.hazard_type=$type, h.status='inactive', "
            "h.created_at=datetime(), h.seeded=false "
            "SET h.status_since = CASE WHEN h.status = 'active' THEN h.status_since ELSE $ts END, "
            "h.source = CASE WHEN h.status = 'active' THEN h.source ELSE $source END, "
            "h.raw_evidence_ref = CASE WHEN h.status = 'active' THEN h.raw_evidence_ref ELSE $ref END, "
            "h.status_event_id = CASE WHEN h.status = 'active' THEN h.status_event_id ELSE $eid END, "
            "h.confidence = CASE WHEN h.status = 'active' AND h.confidence > $conf THEN h.confidence ELSE $conf END "
            "SET h.status='active', h.last_confirmed=$ts, h.last_confirmed_source=$source, h.last_confirmed_evidence_ref=$ref, "
            "h.level=$level, h.unit=$unit, h.radius_m=$radius, h.lat=$lat, h.lon=$lon, h.updated_at=datetime(), "
            "h.location = CASE WHEN $lat IS NULL OR $lon IS NULL THEN h.location ELSE point({latitude:$lat, longitude:$lon}) END "
            "WITH h MATCH (s:Sensor {id:$sensor}) "
            "MERGE (h)-[d:DETECTED_BY]->(s) ON CREATE SET d.since=$ts SET d.last_confirmed=$ts, d.source=$source "
            "RETURN h.id AS id, h.status_since AS status_since, h.level AS level",
            id=hazard_id, name=name, type=hazard_type, ts=ts, source=source, conf=confidence, ref=ref, eid=event_id,
            level=level, unit=unit, radius=radius_m, lat=lat, lon=lon, sensor=sensor_id)
        return rows[0] if rows else {}

    def upsert_vision_hazard(self, hazard_id: str, *, name: str, hazard_type: str, entity_id: str, ts: datetime, source: str,
                             confidence: float, ref: str, event_id: str, lat: float | None, lon: float | None, radius_m: float,
                             description: str | None = None) -> dict[str, Any]:
        """A hazard seen by a camera (fire, smoke, flood water) at an entity: same node shape as a sensor hazard, but
        DETECTED_BY nothing (no sensor) and AFFECTS the entity it was seen at. Added 2026-09-25 (Shresth)."""
        rows = self.write_dicts(
            "MERGE (h:Entity:Hazard {id:$id}) "
            "ON CREATE SET h.kind='hazard', h.name=$name, h.aliases=[$name], h.hazard_type=$type, h.status='inactive', "
            "h.created_at=datetime(), h.seeded=false "
            "SET h.status_since = CASE WHEN h.status = 'active' THEN h.status_since ELSE $ts END, "
            "h.source = CASE WHEN h.status = 'active' THEN h.source ELSE $source END, "
            "h.raw_evidence_ref = CASE WHEN h.status = 'active' THEN h.raw_evidence_ref ELSE $ref END, "
            "h.status_event_id = CASE WHEN h.status = 'active' THEN h.status_event_id ELSE $eid END, "
            "h.confidence = CASE WHEN h.status = 'active' AND h.confidence > $conf THEN h.confidence ELSE $conf END "
            "SET h.status='active', h.last_confirmed=$ts, h.last_confirmed_source=$source, h.last_confirmed_evidence_ref=$ref, "
            "h.radius_m=$radius, h.lat=$lat, h.lon=$lon, h.description=$desc, h.detected_by_camera=true, h.updated_at=datetime(), "
            "h.location = CASE WHEN $lat IS NULL OR $lon IS NULL THEN h.location ELSE point({latitude:$lat, longitude:$lon}) END "
            "WITH h MATCH (e:Entity {id:$entity}) "
            "MERGE (h)-[a:AFFECTS]->(e) ON CREATE SET a.active=false, a.evidence_refs=[], a.event_ids=[] "
            "SET a.since = CASE WHEN a.active THEN a.since ELSE $ts END "
            "SET a.active=true, a.ended_at=null, a.last_confirmed=$ts, a.source=$source, a.confidence=$conf, "
            "a.evidence_refs=a.evidence_refs + $ref, a.event_ids=a.event_ids + $eid "
            "RETURN h.id AS id, h.status_since AS status_since, e.id AS affects",
            id=hazard_id, name=name, type=hazard_type, ts=ts, source=source, conf=confidence, ref=ref, eid=event_id,
            radius=radius_m, lat=lat, lon=lon, desc=description, entity=entity_id)
        return rows[0] if rows else {}

    def link_hazard_affects(self, hazard_id: str, sensor_id: str, *, ts: datetime, source: str, confidence: float,
                            ref: str, event_id: str) -> list[str]:
        rows = self.write_dicts(
            "MATCH (h:Hazard {id:$hid}), (s:Sensor {id:$sid})-[:MONITORS]->(e:Entity) "
            "MERGE (h)-[a:AFFECTS]->(e) ON CREATE SET a.active=false, a.evidence_refs=[], a.event_ids=[] "
            "SET a.since = CASE WHEN a.active THEN a.since ELSE $ts END "
            "SET a.active=true, a.ended_at=null, a.last_confirmed=$ts, a.source=$source, a.confidence=$conf, "
            "a.evidence_refs=a.evidence_refs + $ref, a.event_ids=a.event_ids + $eid RETURN e.id AS id",
            hid=hazard_id, sid=sensor_id, ts=ts, source=source, conf=confidence, ref=ref, eid=event_id)
        return [r["id"] for r in rows]

    def clear_hazard(self, hazard_id: str, *, ts: datetime, source: str, confidence: float, ref: str, event_id: str) -> bool:
        rows = self.write_dicts(
            "MATCH (h:Hazard {id:$id}) WHERE h.status = 'active' SET h.previous_status=h.status, h.status='cleared', h.status_since=$ts, "
            "h.last_confirmed=$ts, h.source=$source, h.confidence=$conf, h.raw_evidence_ref=$ref, h.status_event_id=$eid, h.updated_at=datetime() "
            "WITH h OPTIONAL MATCH (h)-[a:AFFECTS]->() SET a.active=false, a.ended_at=$ts "
            "WITH h OPTIONAL MATCH ()-[r:NEAR]->(h) WHERE r.active SET r.active=false, r.ended_at=$ts, r.ended_by_event=$eid "
            "RETURN DISTINCT h.id AS id",
            id=hazard_id, ts=ts, source=source, conf=confidence, ref=ref, eid=event_id)
        return bool(rows)

    # ------------------------------------------------------------------ assignments
    def upsert_assignment(self, team_id: str, target_id: str, *, ts: datetime, source: str, confidence: float, ref: str, event_id: str) -> None:
        self.write(
            "MATCH (t:Entity {id:$team}), (e:Entity {id:$target}) "
            "MERGE (t)-[a:ASSIGNED_TO]->(e) ON CREATE SET a.active=false, a.evidence_refs=[], a.event_ids=[] "
            "SET a.since = CASE WHEN a.active THEN a.since ELSE $ts END "
            "SET a.active=true, a.ended_at=null, a.ended_by_event=null, a.last_confirmed=$ts, a.source=$source, a.confidence=$conf, "
            "a.evidence_refs=a.evidence_refs + $ref, a.event_ids=a.event_ids + $eid",
            team=team_id, target=target_id, ts=ts, source=source, conf=confidence, ref=ref, eid=event_id)

    def end_assignments(self, team_id: str, ts: datetime, event_id: str) -> int:
        rows = self.write_dicts(
            "MATCH (t:Entity {id:$team})-[a:ASSIGNED_TO]->() WHERE a.active SET a.active=false, a.ended_at=$ts, a.ended_by_event=$eid RETURN count(a) AS c",
            team=team_id, ts=ts, eid=event_id)
        return rows[0]["c"] if rows else 0
