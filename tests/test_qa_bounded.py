"""Bounded answer writing (no runaway JSON) and filter-preserving fallback after an empty LLM query."""
import pytest

from rescuegrid.fusion import FusionAgent
from rescuegrid.graph import GraphStore
from rescuegrid.qa import QAEngine
from rescuegrid.qa.engine import ANSWER_MAX_CHARS, COMPOSE_MAX_TOKENS, AnswerDraft
from rescuegrid.qa.fallback import source_filters
from rescuegrid.qa.llm import LLMError
from rescuegrid.sources import JsonFileSource
from tests.conftest import requires_neo4j


class FakeLLM:
    """Scripted model: returns a fixed Cypher query; records the compose calls it receives."""
    name = "fake"

    def __init__(self, cypher: str, compose_fails: int = 0):
        self.cypher, self.compose_fails, self.compose_calls = cypher, compose_fails, []

    def complete(self, system, user, *, max_tokens=1024, temperature=0.0, thinking=None):
        return f"```cypher\n{self.cypher}\n```"

    def complete_json(self, system, user, model_cls, *, max_tokens=1024, thinking=None):
        self.compose_calls.append(max_tokens)
        if len(self.compose_calls) <= self.compose_fails:
            raise LLMError("invalid JSON output: Unterminated string starting at: line 3 column 17")
        return model_cls(answer="Main Street is blocked since 14:00:32Z (radio_asr, 0.78).", highlight_id="Road-Main")


def test_answer_schema_is_bounded():
    s = AnswerDraft.model_json_schema()["properties"]
    assert s["answer"]["maxLength"] == ANSWER_MAX_CHARS and s["highlight_ids"]["maxItems"] == 5
    assert s["highlight_ids"]["items"]["maxLength"] == 64
    assert COMPOSE_MAX_TOKENS <= 350


def test_source_filters():
    assert source_filters("What did radio report in the last 3 minutes?") == ["radio_asr"]
    assert source_filters("Which roads are blocked?") == []
    assert source_filters("What did Engine 7 report?") == []                 # "report" alone is not field_report
    assert source_filters("any new field reports?") == ["field_report"]


@pytest.fixture(scope="module")
def graph():
    with GraphStore() as g:
        g.reset()
        FusionAgent(g).run(JsonFileSource("events/dummy_events.json"))
        yield g


@requires_neo4j
def test_compose_uses_capped_budget_and_retries_once(graph):
    llm = FakeLLM("MATCH (r:Road {id:'Road-Main'}) RETURN r.id AS id, r.name AS name, r.status AS status, r.source AS source, r.confidence AS confidence, r.status_since AS status_since", compose_fails=1)
    r = QAEngine(graph, llm=llm).answer("Is Main Street passable for units?", mode="llm")
    assert llm.compose_calls == [COMPOSE_MAX_TOKENS, COMPOSE_MAX_TOKENS]
    assert r.mode == "llm" and "Main Street" in r.answer and r.highlight.id == "Road-Main"


@requires_neo4j
def test_compose_failing_twice_lists_rows_without_the_model(graph):
    llm = FakeLLM("MATCH (r:Road {id:'Road-Main'}) RETURN r.id AS id, r.name AS name, r.status AS status, r.source AS source", compose_fails=2)
    r = QAEngine(graph, llm=llm).answer("Is Main Street passable for units?", mode="llm")
    assert len(llm.compose_calls) == 2 and r.answer.startswith("1 matching record(s): Main Street blocked radio_asr")
    assert any("without the model" in w for w in r.warnings)


@requires_neo4j
def test_prebaked_recent_changes_honours_source(graph):
    r = QAEngine(graph, llm=None, auto_llm=False).answer("What did radio report in the last 3 minutes?", mode="fallback")
    assert r.intent == "recent_changes" and r.answer.startswith("2 radio_asr events in the last 3 minutes")
    assert all(p.source == "radio_asr" for p in r.provenance) and "drone_vision" not in r.answer


@requires_neo4j
def test_empty_llm_query_falls_back_only_when_filters_are_kept(graph):
    empty = "MATCH (e:Event) WHERE e.source = 'nothing' RETURN e.id AS id"
    ok = QAEngine(graph, llm=FakeLLM(empty)).answer("What did radio report in the last 3 minutes?", mode="llm")
    assert ok.mode == "llm+fallback" and ok.answer.startswith("2 radio_asr events")
    # blocked_roads cannot filter by source -> must NOT substitute an unfiltered answer
    honest = QAEngine(graph, llm=FakeLLM(empty)).answer("Which roads did radio report as blocked?", mode="llm")
    assert honest.mode == "llm" and honest.answer.startswith("The graph query found no matching records") and honest.confidence <= 0.3
