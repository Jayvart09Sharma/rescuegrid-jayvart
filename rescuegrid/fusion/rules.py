"""Correlation rules: how a single raw event becomes relationships, not just isolated facts.
Each rule is small and independent; the agent runs every rule whose `matches` returns True."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import Settings
from ..contracts import Event
from ..graph import GraphStore
from .geo import haversine_m
from .resolve import EntityResolver

HAZARD_RADIUS_M = {"gas": 100.0, "seismic": 250.0, "water": 150.0, "flood": 150.0, "fire": 200.0}
TEAM_STATUS_CLAIMS = {"on_scene", "en_route", "available", "out_of_service", "assigned", "staged"}


@dataclass
class Context:
    g: GraphStore
    cfg: Settings
    resolver: EntityResolver
    ev: Event
    entity: dict[str, Any]
    created: bool
    notes: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)

    def note(self, s: str) -> None:
        self.notes.append(s)

    def rel(self, s: str) -> None:
        self.relationships.append(s)


class Rule:
    name = "rule"

    def matches(self, ctx: Context) -> bool:  # pragma: no cover - interface
        return False

    def apply(self, ctx: Context) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ProximityRule(Rule):
    """GPS position update -> Team NEAR every building/facility/hazard within NEAR_RADIUS_M,
    and NEAR relationships the unit has moved away from are closed (active=false, ended_at)."""

    name = "proximity"

    def matches(self, ctx: Context) -> bool:
        return ctx.ev.claim == "position_update" and "lat" in ctx.ev.details and "lon" in ctx.ev.details

    def apply(self, ctx: Context) -> None:
        ev, g = ctx.ev, ctx.g
        lat, lon = float(ev.details["lat"]), float(ev.details["lon"])
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValueError(f"lat/lon out of range: {lat}, {lon}")
        moved = g.update_position(ctx.entity["id"], lat=lat, lon=lon, ts=ev.timestamp, source=ev.source, ref=ev.raw_evidence_ref,
                                  event_id=ev.event_id, speed=ev.details.get("speed_mps"))
        if not moved:
            ctx.note("position fix older than the one held - ignored")
            return
        ctx.note(f"position -> ({lat:.5f}, {lon:.5f})")
        for e in g.entities_within(lat, lon, ctx.cfg.near_radius_m, exclude_id=ctx.entity["id"]):
            r = g.upsert_near(ctx.entity["id"], e["id"], ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
                              ref=ev.raw_evidence_ref, event_id=ev.event_id, distance_m=e["distance_m"])
            if r.get("stale"):
                ctx.note(f"NEAR {e['id']}: newer evidence already held - not rewound")
                continue
            ctx.rel(f"NEAR {e['id']} ({e['distance_m']} m of {e['radius_m']:.0f}, conf {r.get('confidence', 0):.2f}, sources {r.get('sources')})")
        for ended in g.expire_near_beyond(ctx.entity["id"], ctx.cfg.near_radius_m, ev.timestamp, ev.event_id):
            ctx.rel(f"NEAR {ended} ended (moved away)")


class VisionNearRule(Rule):
    """Vision / field report says '<unit> near <target>' -> same NEAR relationship as GPS.
    Sources and evidence accumulate; confidence is the noisy-OR of the independent sources."""

    name = "vision_near"

    def matches(self, ctx: Context) -> bool:
        return ctx.ev.claim == "near"

    def apply(self, ctx: Context) -> None:
        ev, g = ctx.ev, ctx.g
        target_text = ev.details.get("target")
        if not target_text:
            ctx.note("'near' claim without details.target - recorded only")
            return
        target, created = ctx.resolver.resolve_or_create(ev, str(target_text))
        if created:
            ctx.note(f"created unknown target entity {target['id']}")
        distance = None
        if all(ctx.entity.get(k) is not None for k in ("lat", "lon")) and all(target.get(k) is not None for k in ("lat", "lon")):
            distance = round(haversine_m(ctx.entity["lat"], ctx.entity["lon"], target["lat"], target["lon"]), 1)
        r = g.upsert_near(ctx.entity["id"], target["id"], ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
                          ref=ev.raw_evidence_ref, event_id=ev.event_id, distance_m=distance)
        if r.get("stale"):
            ctx.note(f"NEAR {target['id']}: newer evidence already held - not rewound")
            return
        ctx.rel(f"NEAR {target['id']} confirmed by {ev.source} (conf {r.get('confidence', 0):.2f}, sources {r.get('sources')})")


class SensorHazardRule(Rule):
    """Sensor spike -> Hazard node (active) that AFFECTS whatever the sensor MONITORS and is
    DETECTED_BY the sensor; units already within the hazard radius get NEAR the hazard.
    Sensor 'normal' -> hazard cleared, AFFECTS/NEAR closed."""

    name = "sensor_hazard"

    def matches(self, ctx: Context) -> bool:
        return "Sensor" in (ctx.entity.get("labels") or []) and ctx.ev.claim in ("spike", "normal")

    def apply(self, ctx: Context) -> None:
        ev, g, s = ctx.ev, ctx.g, ctx.entity
        stype = str(ev.details.get("sensor_type") or s.get("sensor_type") or "unknown")
        hazard_id = f"Hazard-{stype}-{s['id']}"
        reading = ev.details.get("reading")
        if reading is not None:
            g.set_props(s["id"], {"reading": float(reading), "reading_at": ev.timestamp, "unit": ev.details.get("unit") or s.get("unit")})
        if ev.claim == "spike":
            radius = float(ev.details.get("radius_m") or HAZARD_RADIUS_M.get(stype, 100.0))
            h = g.upsert_hazard(hazard_id, name=f"{stype.capitalize()} hazard at {s.get('name') or s['id']}", hazard_type=stype,
                                level=float(reading) if reading is not None else None, unit=ev.details.get("unit"),
                                ts=ev.timestamp, source=ev.source, confidence=ev.confidence, ref=ev.raw_evidence_ref,
                                event_id=ev.event_id, sensor_id=s["id"], lat=s.get("lat"), lon=s.get("lon"), radius_m=radius)
            ctx.rel(f"Hazard {hazard_id} active (level {h.get('level')}) DETECTED_BY {s['id']}")
            for eid in g.link_hazard_affects(hazard_id, s["id"], ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
                                             ref=ev.raw_evidence_ref, event_id=ev.event_id):
                ctx.rel(f"{hazard_id} AFFECTS {eid}")
            if s.get("lat") is not None and s.get("lon") is not None:
                for t in g.teams_within(s["lat"], s["lon"], radius):
                    # provenance: the sensor event created the zone; the unit's own position fix is the second piece of evidence
                    r = g.upsert_near(t["id"], hazard_id, ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
                                      ref=ev.raw_evidence_ref, event_id=ev.event_id, distance_m=t["distance_m"])
                    if r.get("stale"):
                        continue
                    if t.get("position_evidence_ref"):
                        g.write("MATCH (t:Entity {id:$t})-[r:NEAR]->(h:Entity {id:$h}) SET r.evidence_refs = r.evidence_refs + $ref, "
                                "r.sources = CASE WHEN $src IN r.sources THEN r.sources ELSE r.sources + $src END",
                                t=t["id"], h=hazard_id, ref=t["position_evidence_ref"], src=t.get("position_source") or "gps")
                    ctx.rel(f"{t['id']} NEAR {hazard_id} ({t['distance_m']} m) - unit inside hazard radius")
        else:
            if g.clear_hazard(hazard_id, ts=ev.timestamp, source=ev.source, confidence=ev.confidence, ref=ev.raw_evidence_ref, event_id=ev.event_id):
                ctx.rel(f"Hazard {hazard_id} cleared")
            else:
                ctx.note(f"no active hazard for {s['id']} - reading recorded only")


VISION_HAZARDS = {"fire": "fire", "structure_fire": "fire", "smoke": "fire", "wildfire_smoke": "fire", "burning_building": "fire",
                  "flooded_road": "water", "flood_water": "water"}


class VisionHazardRule(Rule):
    """A camera sees fire / smoke / flood water at an entity (Aditya's vision v2 puts the detector classes in
    details.hazard) -> a Hazard node, active, AFFECTS that entity, units within the hazard radius NEAR it. The
    entity's own status claim (damaged / blocked) is handled by the policy as usual; this rule adds the zone.
    Vision never clears a hazard (a hazard leaving the frame is not evidence it is out). Added 2026-09-25 (Shresth)."""

    name = "vision_hazard"

    def matches(self, ctx: Context) -> bool:
        kinds = ctx.ev.details.get("hazard") or []
        return ctx.ev.source == "drone_vision" and any(str(k) in VISION_HAZARDS for k in kinds)

    def apply(self, ctx: Context) -> None:
        ev, g, e = ctx.ev, ctx.g, ctx.entity
        types = {VISION_HAZARDS[str(k)] for k in ev.details.get("hazard") or [] if str(k) in VISION_HAZARDS}
        vlm = ev.details.get("vlm") if isinstance(ev.details.get("vlm"), dict) else {}
        for htype in sorted(types):
            hazard_id = f"Hazard-{htype}-{e['id']}"
            radius = float(ev.details.get("radius_m") or HAZARD_RADIUS_M.get(htype, 100.0))
            h = g.upsert_vision_hazard(hazard_id, name=f"{htype.capitalize()} hazard at {e.get('name') or e['id']}", hazard_type=htype,
                                       entity_id=e["id"], ts=ev.timestamp, source=ev.source, confidence=ev.confidence, ref=ev.raw_evidence_ref,
                                       event_id=ev.event_id, lat=e.get("lat"), lon=e.get("lon"), radius_m=radius, description=vlm.get("description"))
            ctx.rel(f"Hazard {hazard_id} active (seen by {ev.details.get('camera') or 'camera'}) AFFECTS {h.get('affects', e['id'])}")
            if e.get("lat") is not None and e.get("lon") is not None:
                for t in g.teams_within(e["lat"], e["lon"], radius):
                    r = g.upsert_near(t["id"], hazard_id, ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
                                      ref=ev.raw_evidence_ref, event_id=ev.event_id, distance_m=t["distance_m"])
                    if not r.get("stale"):
                        ctx.rel(f"{t['id']} NEAR {hazard_id} ({t['distance_m']} m) - unit inside hazard radius")


class TeamAssignmentRule(Rule):
    """Field report / radio: '<unit> on_scene at <target>' -> ASSIGNED_TO relationship
    (status itself is handled by the generic status policy)."""

    name = "team_assignment"

    def matches(self, ctx: Context) -> bool:
        return "Team" in (ctx.entity.get("labels") or []) and ctx.ev.claim in TEAM_STATUS_CLAIMS

    def apply(self, ctx: Context) -> None:
        ev, g = ctx.ev, ctx.g
        target_text = ev.details.get("target")
        if ev.claim in ("available", "out_of_service"):
            n = g.end_assignments(ctx.entity["id"], ev.timestamp, ev.event_id)
            if n:
                ctx.rel(f"{n} ASSIGNED_TO ended ({ev.claim})")
            return
        if not target_text:
            return
        target, created = ctx.resolver.resolve_or_create(ev, str(target_text))
        g.upsert_assignment(ctx.entity["id"], target["id"], ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
                            ref=ev.raw_evidence_ref, event_id=ev.event_id)
        ctx.rel(f"ASSIGNED_TO {target['id']} ({ev.claim})")


DEFAULT_RULES: list[Rule] = [ProximityRule(), VisionNearRule(), SensorHazardRule(), VisionHazardRule(), TeamAssignmentRule()]
