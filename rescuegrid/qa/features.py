"""Feature flags for the LLM answer layer so every improvement is switchable and benchmarkable.
Set QA_FEATURES=entity_link,schema_check,dyn_fewshot,compact_schema,router_v2 (any subset).

  entity_link     resolve entity mentions in the question to graph ids and tell the model ("Gas Sensor 3" -> Sensor-Gas3)
  schema_check    statically validate generated Cypher against the schema (relationship direction, property names, id in RETURN)
                  before executing it; violations feed the repair round with a precise message
  fewshot_v2      extended few-shot bank (16 examples incl. radius, time-range, assignment, single-entity lookups) instead of the original 11
  dyn_fewshot     include only the k most similar few-shot examples instead of all of them
  compact_schema  terser schema description (fewer prompt tokens)
  router_v2       pre-baked router precision fixes (explicit time ranges, greedy road intent, staged-at)
"""
from __future__ import annotations

ALL = ("entity_link", "schema_check", "fewshot_v2", "dyn_fewshot", "compact_schema", "router_v2")


def enabled(cfg, name: str) -> bool:
    feats = getattr(cfg, "qa_features", frozenset())
    return name in feats or "all" in feats
