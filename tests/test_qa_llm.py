"""LLM text->Cypher path against the local model. Skipped when no LLM endpoint answers."""
import pytest

from rescuegrid.config import settings
from rescuegrid.fusion import FusionAgent
from rescuegrid.graph import GraphStore
from rescuegrid.qa import QAEngine
from rescuegrid.qa.llm import make_llm
from rescuegrid.sources import JsonFileSource
from tests.conftest import requires_neo4j


def _llm_up():
    try:
        llm = make_llm(settings)
        return llm is not None and "ok" in llm.complete("Reply with the word ok.", "ping", max_tokens=8).lower()
    except Exception:
        return False


pytestmark = [requires_neo4j, pytest.mark.skipif(not _llm_up(), reason="no LLM endpoint reachable")]


@pytest.fixture(scope="module")
def engine():
    with GraphStore() as g:
        g.reset()
        FusionAgent(g).run(JsonFileSource("events/dummy_events.json"))
        yield QAEngine(g)


def test_llm_generates_read_only_cypher_and_grounded_answer(engine):
    r = engine.answer("Which roads are blocked?", mode="llm")
    assert r.mode == "llm", r.warnings
    assert r.cypher and "RETURN" in r.cypher.upper() and "SET " not in r.cypher.upper()
    assert r.evidence and any(row.get("id") == "Road-Main" for row in r.evidence)
    assert "Main Street" in r.answer or "Road-Main" in r.answer


def test_llm_handles_question_without_prebaked_intent(engine):
    r = engine.answer("Which buildings have a unit assigned to them right now?", mode="llm")
    assert r.mode == "llm", r.warnings
    assert any("Building-14" in str(v) for row in r.evidence for v in row.values())
    assert "Building 14" in r.answer or "Building-14" in r.answer
