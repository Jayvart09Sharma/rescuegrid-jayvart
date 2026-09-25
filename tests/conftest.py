import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _neo4j_up() -> bool:
    try:
        from rescuegrid.graph import GraphStore
        with GraphStore() as g:
            g.ping()
        return True
    except Exception:
        return False


NEO4J_UP = _neo4j_up()
requires_neo4j = pytest.mark.skipif(not NEO4J_UP, reason="local Neo4j not reachable (scripts/neo4j_local.sh start)")
