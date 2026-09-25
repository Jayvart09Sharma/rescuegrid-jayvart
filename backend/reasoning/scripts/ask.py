#!/usr/bin/env python3
"""Ask the graph a question.
  .venv/bin/python scripts/ask.py "Who is in the most danger?"
  .venv/bin/python scripts/ask.py --mode llm "Which buildings have a unit assigned?"
  .venv/bin/python scripts/ask.py --json "Can Ambulance 2 still reach the hospital?"
  .venv/bin/python scripts/ask.py --demo            # run every pre-baked question in both modes"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuegrid.graph import GraphStore  # noqa: E402
from rescuegrid.qa import QAEngine  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("question", nargs="?")
ap.add_argument("--mode", choices=["auto", "llm", "fallback"], default="auto")
ap.add_argument("--json", action="store_true", help="print the full QAResponse contract")
ap.add_argument("--demo", action="store_true")
ap.add_argument("--verbose", action="store_true", help="also print the Cypher that produced the answer")
args = ap.parse_args()


def show(resp, elapsed):
    if args.json:
        print(resp.model_dump_json(indent=2, exclude={"evidence"} if not args.demo else set()))
        return
    print(f"Q: {resp.question}   [{resp.mode}{'/' + resp.intent if resp.intent else ''}, {elapsed:.1f}s, conf {resp.confidence:.2f}]")
    print(f"A: {resp.answer}")
    if resp.suggestion:
        print(f"   suggestion: {resp.suggestion}")
    h = resp.highlight
    print(f"   highlight: {h.type} {h.id} {h.action}" + (f" +{h.ids}" if h.ids else ""))
    for w in resp.warnings:
        print(f"   warning: {w}")
    if args.verbose and resp.cypher:
        print("   cypher: " + resp.cypher.strip().replace("\n", "\n           ") + f"\n   evidence rows: {len(resp.evidence)}")


with GraphStore() as g:
    engine = QAEngine(g)
    if args.demo:
        for q in engine.fallback.examples():
            for mode in ("fallback", "llm") if engine.llm else ("fallback",):
                t = time.time(); show(engine.answer(q, mode=mode), time.time() - t); print()
        sys.exit(0)
    if not args.question:
        ap.error("question required (or --demo)")
    t = time.time()
    show(engine.answer(args.question, mode=args.mode), time.time() - t)
