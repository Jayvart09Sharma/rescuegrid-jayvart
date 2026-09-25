"""Feature-flagged improvements to the LLM path: static schema check, few-shot selection, prompt builder,
entity linking and router precision. DB-free tests run everywhere; graph tests need the private Neo4j."""
import pytest

from rescuegrid.config import Settings
from rescuegrid.qa.fewshot import EXAMPLES_V0, EXAMPLES_V2, select_examples
from rescuegrid.qa.schema_check import check_cypher
from rescuegrid.qa.schema_prompt import SCHEMA_TEXT, build_system
from tests.conftest import requires_neo4j


# ---------------------------------------------------------------- schema check (pure)
@pytest.mark.parametrize("bad,needle", [
    ("MATCH (b:Building)-[:AFFECTS]->(h:Hazard) RETURN b.id AS id", "AFFECTS never starts at Building"),
    ("MATCH (s:Sensor)-[:MONITORS]->(h:Hazard) RETURN h.id AS id", "MONITORS never ends at Hazard"),
    ("MATCH (h:Hazard)<-[:DETECTED_BY]-(x) RETURN h.id AS id", "DETECTED_BY never ends at Hazard"),
    ("MATCH (s:Sensor {id: 'Gas Sensor 3'}) RETURN s.id AS id", "not an entity id"),
    ("MATCH (t:Team) WHERE t.timestamp > $now RETURN t.id AS id", "only Event nodes have timestamp"),
    ("MATCH (e:Event) RETURN e.claim AS claim ORDER BY e.timestamp LIMIT 1", "RETURN must include the entity id"),
    ("MATCH (r:Road) WHERE r.blocked = true RETURN r.id AS id", "does not exist on Road"),
    ("MATCH (a)-[:FLOWS_TO]->(b) RETURN a.id AS id", "does not exist; known"),
    ("MATCH (t:Team {id: 'Team-Ambulance2'})-[:NEAR]->(x) WHERE n.active = true RETURN x.id AS id", "variable n is used but never bound"),
    ("MATCH (e:Event {source:'radio'})-[:ABOUT]->(r) RETURN r.id AS id", "source 'radio' is not a valid value"),
    ("MATCH (h:Hazard) WHERE h.hazard_type = 'fire' RETURN h.id AS id", "hazard_type 'fire' is not a valid value"),
])
def test_schema_check_catches_baseline_failure_classes(bad, needle):
    problems = check_cypher(bad, name_to_id={"gas sensor 3": "Sensor-Gas3"}).problems
    assert any(needle in p for p in problems), problems


@pytest.mark.parametrize("good", [
    "MATCH (e:Event)-[:ABOUT]->(n:Entity {id: 'Building-14'}) WHERE e.claim = 'collapsed' RETURN n.id AS id, e.source AS source",
    "MATCH (:Road {id:'Road-Oak'})-[:CONNECTS_TO]-(r:Road) RETURN r.id AS id",
    "MATCH (r:Road) WHERE r.status = 'blocked' RETURN count(r) AS n",
    "MATCH (t:Team)-[n:NEAR]->(x) WHERE n.active = true RETURN t.id AS id, x.id AS near, n.distance_m AS d",
    "MATCH (h:Hazard)-[:DETECTED_BY]->(s:Sensor) RETURN h.id AS id, s.id AS sensor, h.status_since AS status_since",
    "MATCH (f:Facility {facility_type: 'hospital'}), (t:Team) WHERE point.distance(t.location, f.location) <= 200 RETURN t.id AS id",
    "MATCH (t:Team {id: 'Team-Rescue4'}) RETURN t.id AS id, t.status_since AS status_since, t.position_since AS position_since",
])
def test_schema_check_accepts_valid_queries(good):
    assert check_cypher(good).problems == []


def test_schema_check_uses_known_ids():
    assert any("does not exist in the graph" in p for p in check_cypher("MATCH (n:Entity {id: 'Building-99'}) RETURN n.id AS id", known_ids={"Building-14"}).problems)
    assert check_cypher("MATCH (n:Entity {id: 'Building-14'}) RETURN n.id AS id", known_ids={"Building-14"}).problems == []


# ---------------------------------------------------------------- few-shots + prompt builder (pure)
def test_baseline_prompt_is_pinned():
    assert build_system(frozenset(), "anything") == SCHEMA_TEXT
    assert len(EXAMPLES_V0) == 11 and len(EXAMPLES_V2) >= 16
    assert all(ex["q"] in SCHEMA_TEXT for ex in EXAMPLES_V0)


def test_dynamic_selection_prefers_relevant_examples():
    qs = [e["q"] for e in select_examples("Which units are within 200 metres of the hospital?", 3, EXAMPLES_V2)]
    assert "List units no more than 150 metres from the shelter." in qs
    qs = [e["q"] for e in select_examples("Which reports about the bridge disagree?", 3, EXAMPLES_V2)]
    assert "Which entities have disputed status right now?" in qs
    assert len(select_examples("zzz qqq", 3, EXAMPLES_V2)) == 3  # no overlap -> deterministic fallback


def test_variant_prompts_are_smaller_and_still_carry_the_schema():
    full = build_system(frozenset(), "Which roads are blocked?")
    dyn = build_system(frozenset({"fewshot_v2", "dyn_fewshot"}), "Which roads are blocked?")
    compact = build_system(frozenset({"compact_schema", "fewshot_v2", "dyn_fewshot"}), "Which roads are blocked?")
    assert len(compact) < len(dyn) < len(full)
    for text in (dyn, compact):
        assert "ABOUT" in text and "CONNECTS_TO" in text and "Which roads are blocked?" in text and "```cypher" in text


# ---------------------------------------------------------------- graph-backed
@pytest.fixture(scope="module")
def graph():
    from rescuegrid.fusion import FusionAgent
    from rescuegrid.graph import GraphStore
    from rescuegrid.sources import JsonFileSource
    with GraphStore() as g:
        g.reset()
        FusionAgent(g).run(JsonFileSource("events/dummy_events.json"))
        yield g


@requires_neo4j
def test_entity_linking_preamble(graph):
    from rescuegrid.qa.text2cypher import Text2Cypher
    import dataclasses
    from rescuegrid.config import settings
    t2c = Text2Cypher(llm=None, graph=graph, cfg=dataclasses.replace(settings, qa_features=frozenset({"entity_link"})))
    pre, n2i = t2c._entity_preamble("What is the reading on Gas Sensor 3, and is the hospital open?")
    assert "Sensor-Gas3" in pre and "Facility-CountyGeneral" in pre
    assert n2i["gas sensor 3"] == "Sensor-Gas3" and n2i["hospital"] == "Facility-CountyGeneral"
    assert "Team-Rescue4" not in pre


@requires_neo4j
def test_router_v2_time_range_and_declines(graph):
    import dataclasses
    from rescuegrid.config import settings
    from rescuegrid.qa import QAEngine
    cfg = dataclasses.replace(settings, qa_features=frozenset({"router_v2"}))
    e = QAEngine(graph, llm=None, cfg=cfg, auto_llm=False)
    r = e.answer("What happened between 14:00 and 14:01?", mode="fallback")
    assert r.intent == "recent_changes" and r.answer.startswith("5 events between 14:00:00Z and 14:01:00Z")
    r = e.answer("Which blocked roads have buildings on them?", mode="fallback")
    assert r.confidence == 0.0 and "No pre-baked query" in r.answer          # declined -> would go to the LLM in auto mode
    r = e.answer("Where is Ambulance 1 staged?", mode="fallback")
    assert "staged at County General Hospital" in r.answer and "Facility-CountyGeneral" in r.highlight.ids
    base = QAEngine(graph, llm=None, auto_llm=False).answer("Which blocked roads have buildings on them?", mode="fallback")
    assert base.intent == "blocked_roads"                                     # baseline behaviour unchanged without the flag


# ---------------------------------------------------------------- D1 parser / loop fixes (pure)
def test_unterminated_fence_and_trailing_fence_are_stripped():
    from rescuegrid.qa.text2cypher import extract_cypher
    assert extract_cypher("```cypher\nMATCH (n) RETURN n.id AS id\nORDER BY") == "MATCH (n) RETURN n.id AS id\nORDER BY"
    assert extract_cypher("```cypher\nMATCH (n) RETURN n.id AS id;\n```\nDone") == "MATCH (n) RETURN n.id AS id"
    assert extract_cypher("MATCH (n) RETURN n.id AS id") == "MATCH (n) RETURN n.id AS id"


def test_connects_to_is_made_undirected():
    from rescuegrid.qa.text2cypher import normalize_cypher
    assert normalize_cypher("MATCH (r:Road)-[:CONNECTS_TO]->(o:Road {id:'Road-Oak'}) RETURN r.id AS id") == "MATCH (r:Road)-[:CONNECTS_TO]-(o:Road {id:'Road-Oak'}) RETURN r.id AS id"
    assert normalize_cypher("MATCH (a)<-[c:CONNECTS_TO]-(b) RETURN a.id AS id") == "MATCH (a)-[c:CONNECTS_TO]-(b) RETURN a.id AS id"
    assert normalize_cypher("MATCH p=(s)-[:CONNECTS_TO*0..6]-(d) RETURN p") == "MATCH p=(s)-[:CONNECTS_TO*0..6]-(d) RETURN p"
    assert normalize_cypher("MATCH (a)-[:ON_ROAD]->(r) RETURN a.id AS id") == "MATCH (a)-[:ON_ROAD]->(r) RETURN a.id AS id"


class _ScriptedLLM:
    """Returns scripted Cypher strings in order and records finish reasons."""
    name = "scripted"

    def __init__(self, outputs, finishes=None):
        self.outputs, self.finishes, self.calls, self.last_finish = list(outputs), list(finishes or []), [], None

    def complete(self, system, user, *, max_tokens=1024, temperature=0.0, thinking=None):
        self.calls.append(user)
        i = len(self.calls) - 1
        self.last_finish = self.finishes[i] if i < len(self.finishes) else "stop"
        return self.outputs[min(i, len(self.outputs) - 1)]

    def complete_json(self, system, user, model_cls, *, max_tokens=1024, thinking=None):
        return model_cls(answer="unused")


@requires_neo4j
def test_unknown_id_short_circuits_without_repairs(graph):
    import dataclasses
    from datetime import datetime, timezone
    from rescuegrid.config import settings
    from rescuegrid.qa.text2cypher import Text2Cypher
    llm = _ScriptedLLM(["```cypher\nMATCH (n:Entity {id: 'Building-500'}) RETURN n.id AS id, n.status AS status\n```"])
    t2c = Text2Cypher(llm, graph, cfg=dataclasses.replace(settings, qa_features=frozenset({"schema_check"})))
    res = t2c.run("What is the status of Building 500?", datetime(2026, 9, 25, 14, 2, 45, tzinfo=timezone.utc))
    assert res.error.startswith("unknown entity") and len(llm.calls) == 1 and res.rows == []


@requires_neo4j
def test_truncated_query_gets_a_shorten_repair(graph):
    from datetime import datetime, timezone
    from rescuegrid.qa.text2cypher import Text2Cypher
    llm = _ScriptedLLM(["```cypher\nMATCH (r:Road) RETURN r.id AS id, r.name AS name, r.status AS status, r.lanes AS lanes, r.source AS src", "```cypher\nMATCH (r:Road) RETURN r.id AS id\n```"], finishes=["length", "stop"])
    res = Text2Cypher(llm, graph).run("Which roads exist?", datetime(2026, 9, 25, 14, 2, 45, tzinfo=timezone.utc))
    assert len(llm.calls) == 2 and "cut off at the token limit" in llm.calls[1] and len(res.rows) == 5


@requires_neo4j
def test_det_render_skips_the_compose_call(graph):
    import dataclasses
    from rescuegrid.config import settings
    from rescuegrid.qa import QAEngine
    llm = _ScriptedLLM(["```cypher\nMATCH (n:Entity {id: 'Building-14'}) RETURN n.id AS id, n.name AS name, n.status AS status, n.status_since AS status_since, n.source AS source, n.confidence AS confidence\n```"])
    e = QAEngine(graph, llm=llm, cfg=dataclasses.replace(settings, qa_features=frozenset({"det_render"})))
    r = e.answer("What is the status of Building 14?", mode="llm")
    assert r.answer == "Building 14 is collapsed since 14:00:15Z (drone_vision, 0.91)." and r.highlight.id == "Building-14"
    assert any("template" in w for w in r.warnings) and len(llm.calls) == 1
