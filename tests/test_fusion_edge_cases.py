"""Edge cases raised by the adversarial review: out-of-order positional events, atomicity, invalid
payloads, placeholder entities, losing conflict claims, ambiguous names, hazard radius, epochs."""
from datetime import datetime, timezone

import pytest

from rescuegrid.fusion import FusionAgent
from rescuegrid.fusion.rules import DEFAULT_RULES, Rule
from rescuegrid.graph import GraphStore, to_native
from rescuegrid.sources import JsonFileSource, JsonlStdinSource
from tests.conftest import requires_neo4j

pytestmark = requires_neo4j


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def ev(entity, claim, t, source="field_report", conf=0.9, ref=None, **details):
    return {"source": source, "timestamp": t, "confidence": conf, "entity": entity, "claim": claim,
            "raw_evidence_ref": ref or f"test/{entity}/{claim}/{t}", "details": details}


@pytest.fixture()
def graph():
    with GraphStore() as g:
        g.reset()
        FusionAgent(g).run(JsonFileSource("events/dummy_events.json"))
        yield g


def near(g, team, target):
    rows = g.read_dicts("MATCH (:Entity {id:$t})-[r:NEAR]->(:Entity {id:$x}) RETURN properties(r) AS r", t=team, x=target)
    return to_native(rows[0]["r"]) if rows else None


def test_late_gps_fix_does_not_rewind_position_or_near(graph):
    a = FusionAgent(graph)
    a.run(JsonFileSource("events/extra_team_moves_away.json"))          # 14:05:00 -> at Building 7
    res = a.handle(ev("Rescue Team 4", "position_update", "2026-09-25T14:00:50Z", source="gps", lat=37.3353, lon=-121.8891))
    assert res.action == "stale" and not res.applied
    t = graph.get_entity("Team-Rescue4")
    assert to_native(t["position_since"]) == ts("2026-09-25T14:05:00Z") and abs(t["lat"] - 37.3374) < 1e-6
    assert near(graph, "Team-Rescue4", "Building-14")["active"] is False
    assert near(graph, "Team-Rescue4", "Building-7")["active"] is True
    late_vision = a.handle(ev("Rescue Team 4", "near", "2026-09-25T14:00:50Z", source="drone_vision", conf=0.8, target="Building 14"))
    assert late_vision.action == "stale"
    assert near(graph, "Team-Rescue4", "Building-14")["active"] is False


def test_late_contradiction_older_than_latest_confirmation_is_stale(graph):
    a = FusionAgent(graph)
    a.handle(ev("Main Street", "blocked", "2026-09-25T14:03:00Z", source="drone_vision", conf=0.9))   # re-confirms
    res = a.handle(ev("Main Street", "open", "2026-09-25T14:02:00Z", source="field_report", conf=0.9))  # late, older than confirmation
    assert res.action == "stale"
    r = graph.get_entity("Road-Main")
    assert r["status"] == "blocked" and to_native(r["last_confirmed"]) == ts("2026-09-25T14:03:00Z")


def test_invalid_payload_is_reported_not_raised(graph):
    a = FusionAgent(graph)
    res = a.handle({"source": "gps", "timestamp": "not a date", "confidence": 2, "entity": "", "claim": "x", "raw_evidence_ref": "r"})
    assert res.action == "invalid" and res.error and not res.applied
    stream = ["not json", "", "# comment", '{"source":"radio_asr","timestamp":"2026-09-25T14:06:00Z","confidence":0.7,"entity":"Oak Avenue","claim":"restricted","raw_evidence_ref":"radio/x"}']
    results = a.run(JsonlStdinSource(iter(stream), on_error=lambda line, exc: None))
    assert len(results) == 1 and results[0].applied and graph.get_entity("Road-Oak")["status"] == "restricted"


def test_rule_exception_rolls_back_and_leaves_an_audit_event(graph):
    class Boom(Rule):
        name = "boom"
        def matches(self, ctx):
            return ctx.ev.claim == "damaged"
        def apply(self, ctx):
            raise RuntimeError("kaboom")
    a = FusionAgent(graph, rules=list(DEFAULT_RULES) + [Boom()])
    res = a.handle(ev("Building 7", "damaged", "2026-09-25T14:06:00Z", source="drone_vision", conf=0.8, ref="drone/boom"))
    assert res.action == "error" and "kaboom" in res.error
    assert graph.get_entity("Building-7")["status"] == "intact"                     # status write rolled back
    audit = graph.read_dicts("MATCH (e:Event {id:$id}) RETURN e.applied AS applied, e.action AS action, e.note AS note", id=res.event_id)
    assert audit and audit[0]["applied"] is False and audit[0]["action"] == "error" and "kaboom" in audit[0]["note"]


def test_auto_created_placeholder_does_not_conflict_with_first_real_claim(graph):
    a = FusionAgent(graph)
    a.handle(ev("Rescue Team 4", "near", "2026-09-25T14:06:00Z", source="drone_vision", conf=0.8, target="Elm Street"))  # creates Road-ElmStreet (unknown)
    res = a.handle(ev("Elm Street", "blocked", "2026-09-25T14:06:30Z", source="radio_asr", conf=0.8))
    assert res.action == "override"
    e = graph.get_entity("Road-ElmStreet")
    assert e["status"] == "blocked" and e["conflict"] is False


def test_losing_side_of_a_conflict_does_not_drive_rules(graph):
    a = FusionAgent(graph)
    # Rescue Team 4 is on_scene (field_report 0.90). A lower-confidence radio report says 'available' with a different target
    res = a.handle(ev("Rescue Team 4", "available", "2026-09-25T14:03:00Z", source="radio_asr", conf=0.75, target="Building 7"))
    assert res.action == "conflict" and not res.relationships
    t = graph.get_entity("Team-Rescue4")
    assert t["status"] == "on_scene" and t["conflict"] is True and t["conflict_claim"] == "available"
    rows = graph.read_dicts("MATCH (:Team {id:'Team-Rescue4'})-[a:ASSIGNED_TO]->(b) RETURN b.id AS id, a.active AS active")
    assert rows == [{"id": "Building-14", "active": True}]                            # assignment untouched


def test_dissenting_source_recanting_resolves_conflict(graph):
    a = FusionAgent(graph)
    res = a.handle(ev("Bridge Street", "blocked", "2026-09-25T14:04:00Z", source="radio_asr", conf=0.8))
    assert res.action == "resolve_conflict"
    r = graph.get_entity("Road-Bridge")
    assert r["conflict"] is False and r["status"] == "blocked"


def test_ambiguous_spoken_name_is_recorded_not_guessed(graph):
    res = FusionAgent(graph).handle(ev("Ambulance", "en_route", "2026-09-25T14:06:00Z", source="radio_asr", conf=0.7))
    assert res.action == "ambiguous" and not res.applied and res.entity_id is None
    assert graph.get_entity("Team-Ambulance1")["status"] == "available" and graph.get_entity("Team-Ambulance2")["status"] == "available"
    audit = graph.read_dicts("MATCH (e:Event {id:$id}) RETURN e.applied AS a, e.action AS action", id=res.event_id)
    assert audit and audit[0]["a"] is False and audit[0]["action"] == "ambiguous"


def test_unit_inside_hazard_zone_but_beyond_75m_stays_near_hazard(graph):
    a = FusionAgent(graph)
    # ~71 m from the gas sensor (hazard radius 100 m) but ~100 m from Building 14 (NEAR radius 75 m)
    res = a.handle(ev("Rescue Team 4", "position_update", "2026-09-25T14:06:00Z", source="gps", lat=37.3350, lon=-121.8904))
    assert res.applied
    assert near(graph, "Team-Rescue4", "Hazard-gas-Sensor-Gas3")["active"] is True
    assert near(graph, "Team-Rescue4", "Building-14")["active"] is False


def test_reactivated_near_starts_a_fresh_epoch(graph):
    a = FusionAgent(graph)
    a.run(JsonFileSource("events/extra_team_moves_away.json"))                                              # leaves B14 at 14:05
    a.handle(ev("Rescue Team 4", "position_update", "2026-09-25T14:07:00Z", source="gps", conf=0.6, lat=37.3353, lon=-121.8891))  # back at B14
    r = near(graph, "Team-Rescue4", "Building-14")
    assert r["active"] is True and r["since"] == ts("2026-09-25T14:07:00Z") and r["epochs"] == 2
    assert r["sources"] == ["gps"] and abs(r["confidence"] - 0.6) < 1e-9 and len(r["evidence_refs"]) == 1


def test_repeated_normal_readings_do_not_rewrite_cleared_hazard(graph):
    a = FusionAgent(graph)
    a.handle(ev("Gas Sensor 3", "normal", "2026-09-25T14:06:00Z", source="sensor", conf=0.97, reading=3.0))
    first = to_native(graph.get_entity("Hazard-gas-Sensor-Gas3")["status_since"])
    res = a.handle(ev("Gas Sensor 3", "normal", "2026-09-25T14:07:00Z", source="sensor", conf=0.97, reading=2.0))
    assert res.action == "confirm" and not res.relationships
    h = graph.get_entity("Hazard-gas-Sensor-Gas3")
    assert h["status"] == "cleared" and to_native(h["status_since"]) == first == ts("2026-09-25T14:06:00Z")
    assert near(graph, "Team-Rescue4", "Hazard-gas-Sensor-Gas3")["active"] is False


def test_unicode_and_hyphenated_inputs(graph):
    a = FusionAgent(graph)
    res = a.handle(ev("रेस्क्यू टीम ९", "En-Route", "2026-09-25T14:06:00Z", source="radio_asr", conf=0.7, entity_type="team"))
    assert res.applied and res.entity_id and res.entity_id != "Team-" and res.entity_id.startswith("Team-")
    assert graph.get_entity(res.entity_id)["status"] == "en_route"
