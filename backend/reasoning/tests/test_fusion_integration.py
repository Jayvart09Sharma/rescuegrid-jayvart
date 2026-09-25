"""End-to-end: replay the dummy scenario into the local Neo4j and check the graph."""
from datetime import datetime, timezone

import pytest

from rescuegrid.fusion import FusionAgent
from rescuegrid.graph import GraphStore, to_native
from rescuegrid.sources import InMemorySource, JsonFileSource
from tests.conftest import requires_neo4j

pytestmark = requires_neo4j


@pytest.fixture(scope="module")
def graph():
    with GraphStore() as g:
        g.reset()
        agent = FusionAgent(g)
        results = agent.run(JsonFileSource("events/dummy_events.json"))
        assert not [r for r in results if r.error], [r.error for r in results if r.error]
        yield g


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_building_collapse_has_timestamp_and_provenance(graph):
    b = graph.get_entity("Building-14")
    assert b["status"] == "collapsed"
    assert to_native(b["status_since"]) == ts("2026-09-25T14:00:15Z")
    assert b["source"] == "drone_vision" and b["confidence"] == 0.91 and b["raw_evidence_ref"] == "drone/cam1/frame_000450.jpg"


def test_spoken_name_resolves_to_road_and_blocks_it(graph):
    r = graph.get_entity("Road-Main")
    assert r["status"] == "blocked" and r["source"] == "radio_asr"
    assert to_native(r["status_since"]) == ts("2026-09-25T14:00:32Z")


def test_gps_plus_vision_fuse_into_one_near_relationship(graph):
    rows = graph.read_dicts("MATCH (t:Team {id:'Team-Rescue4'})-[r:NEAR]->(b:Building {id:'Building-14'}) RETURN properties(r) AS r")
    assert len(rows) == 1, "exactly one NEAR relationship, not two disconnected facts"
    r = rows[0]["r"]
    assert r["active"] is True
    assert set(r["sources"]) == {"gps", "drone_vision"}
    assert r["confidence"] > 0.95  # noisy-OR of 0.95 and 0.82
    assert to_native(r["since"]) == ts("2026-09-25T14:00:40Z") and to_native(r["last_confirmed"]) == ts("2026-09-25T14:00:45Z")
    assert 10 < r["distance_m"] < 40
    assert "drone/cam1/frame_000520.jpg" in r["evidence_refs"] and "mqtt/gps/team-rescue4/1790344840" in r["evidence_refs"]


def test_gas_spike_creates_hazard_affecting_building_and_unit_inside_radius(graph):
    h = graph.get_entity("Hazard-gas-Sensor-Gas3")
    assert h and h["status"] == "active" and h["source"] == "sensor"
    assert graph.read_dicts("MATCH (:Hazard {id:'Hazard-gas-Sensor-Gas3'})-[a:AFFECTS]->(b:Building {id:'Building-14'}) RETURN a.active AS a")[0]["a"] is True
    assert graph.read_dicts("MATCH (:Hazard {id:'Hazard-gas-Sensor-Gas3'})-[:DETECTED_BY]->(s:Sensor {id:'Sensor-Gas3'}) RETURN s.id AS id")
    near = graph.read_dicts("MATCH (t:Team {id:'Team-Rescue4'})-[r:NEAR]->(:Hazard {id:'Hazard-gas-Sensor-Gas3'}) RETURN r.active AS a, r.distance_m AS d")
    assert near and near[0]["a"] is True and near[0]["d"] < 100
    assert graph.get_entity("Sensor-Gas3")["status"] == "spike"


def test_field_report_assigns_unit_and_sets_status(graph):
    t = graph.get_entity("Team-Rescue4")
    assert t["status"] == "on_scene" and t["source"] == "field_report"
    rows = graph.read_dicts("MATCH (:Team {id:'Team-Rescue4'})-[a:ASSIGNED_TO]->(b) RETURN b.id AS id, a.active AS active")
    assert rows == [{"id": "Building-14", "active": True}]


def test_conflicting_reports_are_surfaced_not_silently_picked(graph):
    r = graph.get_entity("Road-Bridge")
    assert r["conflict"] is True
    assert r["status"] == "blocked" and r["source"] == "drone_vision"          # higher-confidence report is the status
    assert r["conflict_claim"] == "open" and r["conflict_source"] == "radio_asr"  # the other side stays visible
    assert r["conflict_evidence_ref"] == "radio/ch3/clip_000150.wav" and r["raw_evidence_ref"] == "drone/cam2/frame_000910.jpg"
    assert graph.read_dicts("MATCH (:Event {id:'evt-0010'})-[:CONFLICTS_WITH]->(:Event {id:'evt-0009'}) RETURN 1 AS x")


def test_every_event_is_in_the_audit_trail_with_about_edge(graph):
    rows = graph.read_dicts("MATCH (e:Event) OPTIONAL MATCH (e)-[:ABOUT]->(n) RETURN count(e) AS events, count(n) AS linked")
    assert rows[0]["events"] == 10 and rows[0]["linked"] == 10


def test_replaying_the_same_file_is_idempotent(graph):
    before = graph.counts()
    results = FusionAgent(graph).run(JsonFileSource("events/dummy_events.json"))
    assert all(r.action == "duplicate" for r in results)
    assert graph.counts() == before


def test_out_of_order_older_event_does_not_overwrite(graph):
    stale = {"source": "field_report", "timestamp": "2026-09-25T14:00:05Z", "confidence": 0.9, "entity": "Building-14",
             "claim": "intact", "raw_evidence_ref": "fieldreport/late_arrival"}
    res = FusionAgent(graph).handle(stale)
    assert res.action == "stale" and not res.applied
    assert graph.get_entity("Building-14")["status"] == "collapsed"
    assert graph.read_dicts("MATCH (e:Event {id:$id}) RETURN e.applied AS a", id=res.event_id)[0]["a"] is False


def test_unit_moving_away_closes_near_and_opens_new_one(graph):
    res = FusionAgent(graph).run(JsonFileSource("events/extra_team_moves_away.json"))
    assert res[0].applied
    old = graph.read_dicts("MATCH (:Team {id:'Team-Rescue4'})-[r:NEAR]->(:Building {id:'Building-14'}) RETURN r.active AS a, r.ended_at AS e")[0]
    assert old["a"] is False and to_native(old["e"]) == ts("2026-09-25T14:05:00Z")
    new = graph.read_dicts("MATCH (:Team {id:'Team-Rescue4'})-[r:NEAR]->(:Building {id:'Building-7'}) RETURN r.active AS a")
    assert new and new[0]["a"] is True


def test_unknown_entity_is_created_and_flagged(graph):
    res = FusionAgent(graph).handle({"source": "radio_asr", "timestamp": "2026-09-25T14:06:00Z", "confidence": 0.6,
                                     "entity": "Elm Street", "claim": "blocked", "raw_evidence_ref": "radio/x"})
    assert res.created_entity and res.entity_id == "Road-ElmStreet"
    e = graph.get_entity("Road-ElmStreet")
    assert e["auto_created"] is True and e["status"] == "blocked" and "Road" in e["labels"]
