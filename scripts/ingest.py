#!/usr/bin/env python3
"""Run the fusion agent against an event source.
  .venv/bin/python scripts/ingest.py --reset                       # wipe+schema+seed, then replay events/dummy_events.json
  .venv/bin/python scripts/ingest.py --file events/extra_team_moves_away.json
  cat events.jsonl | .venv/bin/python scripts/ingest.py --source stdin
  .venv/bin/python scripts/ingest.py --source mqtt --topic 'rescuegrid/events/#' --host 127.0.0.1"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuegrid.fusion import FusionAgent  # noqa: E402
from rescuegrid.graph import GraphStore  # noqa: E402
from rescuegrid.sources import JsonFileSource, JsonlStdinSource, MqttSource  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--source", choices=["json", "stdin", "mqtt"], default="json")
ap.add_argument("--file", default="events/dummy_events.json")
ap.add_argument("--topic", action="append")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--reset", action="store_true", help="wipe the graph, re-apply schema + static seed first")
ap.add_argument("--quiet", action="store_true")
args = ap.parse_args()

if args.source == "json":
    source = JsonFileSource(args.file)
elif args.source == "stdin":
    source = JsonlStdinSource()
else:
    source = MqttSource(topics=args.topic or ["rescuegrid/events/#"], host=args.host)

with GraphStore() as g:
    g.ping()
    if args.reset:
        g.reset(); print("graph reset: schema + static world seeded")
    agent = FusionAgent(g)
    results = agent.run(source, on_result=None if args.quiet else lambda r: print(r.summary()))
    applied = sum(r.applied for r in results); errors = [r for r in results if r.error]
    print(f"\n{len(results)} events processed: {applied} applied, {len(results) - applied - len(errors)} recorded-only/duplicate, {len(errors)} errors")
    for r in errors:
        print("  ERROR", r.event_id, r.error)
    sys.exit(1 if errors else 0)
