"""Pre-baked Q&A against the replayed scenario - no LLM involved."""
import pytest

from rescuegrid.fusion import FusionAgent
from rescuegrid.graph import GraphStore
from rescuegrid.qa import QAEngine
from rescuegrid.sources import JsonFileSource
from tests.conftest import requires_neo4j

pytestmark = requires_neo4j


@pytest.fixture(scope="module")
def engine():
    with GraphStore() as g:
        g.reset()
        FusionAgent(g).run(JsonFileSource("events/dummy_events.json"))
        yield QAEngine(g, llm=None, auto_llm=False)


def test_most_danger(engine):
    r = engine.answer("Who is in the most danger?", mode="fallback")
    assert r.intent == "most_danger" and r.mode == "fallback"
    assert "Rescue Team 4" in r.answer and "Building 14" in r.answer and "Gas" in r.answer
    assert r.highlight.type == "team" and r.highlight.id == "Team-Rescue4" and "Building-14" in r.highlight.ids
    assert r.highlight.lat is not None


def test_reachability_surfaces_conflict_and_requires_approval(engine):
    r = engine.answer("Can Ambulance 2 still reach the hospital?", mode="fallback")
    assert r.intent == "reachability"
    assert r.answer.startswith("No confirmed route to County General Hospital")
    assert "Main Street" in r.answer and "Bridge Street" in r.answer and "conflicting" in r.answer
    assert r.requires_commander_approval and "commander approval required" in r.suggestion
    assert r.highlight.id == "Road-Bridge" and "Team-Ambulance2" in r.highlight.ids
    assert r.conflicts and r.conflicts[0].id == "Road-Bridge" and r.conflicts[0].competing_confidence == 0.7


def test_reachability_yes_when_route_is_clean(engine):
    # Ambulance 1 is staged at the hospital itself -> zero-hop route
    r = engine.answer("Can Ambulance 1 reach County General?", mode="fallback")
    assert r.answer.startswith("Yes") and r.highlight.id == "Team-Ambulance1"


def test_blocked_roads(engine):
    r = engine.answer("Which roads are blocked?", mode="fallback")
    assert "Main Street blocked" in r.answer and "Bridge Street blocked" in r.answer and "CONFLICTING" in r.answer
    assert set(r.highlight.ids + [r.highlight.id]) == {"Road-Main", "Road-Bridge"}


def test_recent_changes_uses_replay_clock(engine):
    r = engine.answer("What changed in the last 5 minutes?", mode="fallback")
    assert r.intent == "recent_changes" and r.answer.startswith("10 events")
    r2 = engine.answer("What changed in the last 30 seconds?", mode="fallback")
    assert r2.answer.startswith("2 events")  # 14:02:30 and 14:02:45 relative to the 14:02:45 clock


def test_conflicts(engine):
    r = engine.answer("Are there any conflicting reports?", mode="fallback")
    assert "Bridge Street" in r.answer and "drone_vision" in r.answer and "radio_asr" in r.answer and r.highlight.id == "Road-Bridge"


def test_unit_location(engine):
    r = engine.answer("Where is Rescue Team 4?", mode="fallback")
    assert "on scene" in r.answer and "Building 14" in r.answer and "gps" in r.answer
    assert r.highlight.id == "Team-Rescue4" and "Hazard-gas-Sensor-Gas3" in r.highlight.ids


def test_entity_status_timeline_and_provenance(engine):
    r = engine.answer("What happened to Building 14?", mode="fallback")
    assert "collapsed since 14:00:15Z" in r.answer and "drone/cam1/frame_000450.jpg" in r.answer and "120 occupants" in r.answer
    assert any(p.source == "drone_vision" for p in r.provenance)
    assert r.highlight.type == "building" and r.highlight.id == "Building-14"


def test_unknown_question_in_fallback_mode_says_so(engine):
    r = engine.answer("How many pizzas should we order?", mode="fallback")
    assert r.confidence == 0.0 and "No pre-baked query" in r.answer


def test_contract_minimal_shape(engine):
    d = engine.answer("Which roads are blocked?", mode="fallback").model_dump()
    assert set(d) >= {"answer", "highlight", "confidence", "mode", "cypher", "evidence", "provenance", "conflicts", "as_of"}
    assert set(d["highlight"]) >= {"type", "id", "ids", "action", "lat", "lon"}
