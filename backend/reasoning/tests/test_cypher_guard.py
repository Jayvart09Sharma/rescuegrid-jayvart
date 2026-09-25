import pytest

from rescuegrid.qa.llm import strip_thinking
from rescuegrid.qa.text2cypher import UnsafeCypher, extract_cypher, validate_cypher


@pytest.mark.parametrize("bad", [
    "MATCH (n) SET n.status = 'open' RETURN n",
    "MERGE (n:Road {id:'x'}) RETURN n",
    "MATCH (n) DETACH DELETE n RETURN 1",
    "MATCH (n) REMOVE n.status RETURN n",
    "CREATE (n:Road) RETURN n",
    "CALL { MATCH (n) SET n.x = 1 } RETURN 1",
    "CALL dbms.components() YIELD name RETURN name",
    "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
    "MATCH (n) RETURN n; DROP INDEX x",
])
def test_writes_and_admin_are_rejected(bad):
    with pytest.raises(UnsafeCypher):
        validate_cypher(bad)


def test_read_query_passes_and_gets_a_limit():
    out = validate_cypher("MATCH (r:Road) WHERE r.status = 'blocked' RETURN r.id AS id ORDER BY r.status_since DESC")
    assert out.endswith("LIMIT 25")
    assert validate_cypher("MATCH (n) RETURN n LIMIT 5000").endswith("LIMIT 100")
    assert validate_cypher("MATCH (n) RETURN n LIMIT 10").endswith("LIMIT 10")


def test_property_named_like_a_keyword_is_fine():
    # 'reset' / 'settings' / 'dataset' contain SET but are not clauses
    validate_cypher("MATCH (n) WHERE n.source = 'seed_dataset' RETURN n.settings, n.reset")


def test_fulltext_procedure_allowed_other_procedures_not():
    validate_cypher("CALL db.index.fulltext.queryNodes('entity_search', 'main') YIELD node RETURN node.id AS id")
    with pytest.raises(UnsafeCypher):
        validate_cypher("CALL db.labels() YIELD label RETURN label")


def test_no_return_rejected():
    with pytest.raises(UnsafeCypher):
        validate_cypher("MATCH (n:Road) WHERE n.status = 'blocked'")


def test_extract_from_fence_and_strip_thinking():
    assert extract_cypher("Here you go:\n```cypher\nMATCH (n) RETURN n;\n```\nDone.") == "MATCH (n) RETURN n"
    assert strip_thinking("We need to think.\n</think>\n```cypher\nMATCH (n) RETURN n\n```") == "```cypher\nMATCH (n) RETURN n\n```"
    assert strip_thinking("<think></think>plain") == "plain"
