#!/usr/bin/env python3
"""Apply constraints/indexes and load the static world into the configured Neo4j.
Usage: .venv/bin/python scripts/apply_schema.py [--wipe]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuegrid.graph import GraphStore  # noqa: E402

wipe = "--wipe" in sys.argv
with GraphStore() as g:
    g.ping()
    if wipe:
        g.wipe(); print("wiped all nodes/relationships")
    n = g.apply_schema(); print(f"schema: {n} statements applied")
    m = g.seed_static_world(); print(f"seed:   {m} statements applied")
    print("constraints:", ", ".join(g.constraints()))
    print("indexes:    ", ", ".join(g.indexes()))
    for k, v in g.counts().items():
        print(f"  {k:<22} {v}")
