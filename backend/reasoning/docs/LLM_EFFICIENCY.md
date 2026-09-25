# RescueGrid Q&A: a benchmarked, token-efficient answer layer over a live Neo4j graph

Status: living document. Numbers come from `bench/results/*.json`; every run records the git commit, a dirty-tree flag,
feature flags, the few-shot bank hash, and memory on the box, so any row can be reproduced with
`scripts/restore_state.sh <tag>` and the command in the table.

## 1. Problem and constraints

A county EOC watch officer asks free-text questions ("can Ambulance 2 still reach the hospital?") over a property graph that
changes every second and carries provenance on every fact. Answers must be grounded in query rows, refuse honestly when the graph
has nothing, never write, and run offline on one NVIDIA GB10 (121 GB unified memory shared with a vision model and ASR) using a
30B-A3B hybrid Mamba/MoE model served by vLLM 0.26 through HP ZRT. Decode is ~45 tok/s; prefill ~5.5k tok/s. The model's
attention block is 4,176 tokens, so vLLM prefix caching never reuses our 2,764-token schema prompt (measured 0% hits; caching was
also disabled in the launch). Every prompt token is therefore paid for on every call, which is what makes token efficiency a
latency problem here, not a billing one.

## 2. What the research said (docs/research/LLM_QA_PROPOSAL.md)

Five research lenses, three judges, a red team and a revision (2026-09-25). Decision: keep **LLM + knowledge graph via
text-to-Cypher** (templates first for known intents, generated read-only Cypher for the tail). Rejected with reasons: whole-graph-in-context
(7.6-8.9k tokens today, 15-60k at scale, and it got an aggregation wrong), vector RAG (cannot express time windows, sums, routes),
GraphRAG variants (built for unstructured corpora; this graph is already typed and fresh), agentic tool loops (3-4x calls at 3 s
each), decomposition/schema-linking pipelines (the schema already fits in 815 tokens), fine-tuning the 30B (needs ~60 GB free),
speculative decoding (no MTP head; ngram has silent-corruption bugs on hybrid models in this vLLM), prompt compression (deletes the
identifiers Cypher must copy). Kept for the research week: a held-out, contamination-controlled test set as the research object;
deterministic rendering of known row shapes; a logprob-gated slot router; best-of-N with execution agreement; a schema-compiled
EBNF grammar; a prefix-caching relaunch (`--enable-prefix-caching --mamba-cache-mode align --prefix-match-unit 16`, team decision).

The red team's two fatal findings were accepted: (1) the extended few-shot bank leaked six benchmark questions verbatim, so the
bank was rewritten and every run now reports **Correct_unseen** (questions with token-Jaccard < 0.7 to any rendered few-shot);
(2) the demo-window plan was cut to bug fixes, a renderer, and a frozen template mode.

## 3. Benchmark (bench/)

* `bench/questions.json`: 60 dev questions in 10 categories (status, provenance, temporal, proximity, route, aggregation, conflict,
  units, unanswerable, paraphrase), each with gold Cypher (executed live, so the gold adapts to the graph), a row-comparison mode
  (id set, subset, any-of-columns, row count, value, contains-any) and required/forbidden answer facts. Unanswerables (out of
  schema, missing entities, write and dispatch requests) must produce an honest refusal.
* `bench/questions_heldout.json` (in progress): ~100 questions authored independently with entity swaps and three tiers
  (plain, paraphrase, radio-speak) and ~20 categorised unanswerables; disjoint from the dev set and the few-shot banks.
* `bench/run_bench.py`: instruments every model call (role: cypher / repair_k / zero_row_retry / compose / compose_retry, prompt and
  completion tokens, seconds, finish reason), scores, and writes one JSON per run. `--repeats K` resets the graph between repeats
  and reports mean, standard error and pass^K. `--compare` tabulates runs.

Metrics per question: **exec_acc** (system rows match gold rows under the question's comparison mode), **grounded** (no source,
time or confidence in the answer that is absent from the rows, conflicts and provenance; computed only when rows exist),
**facts** (required strings present, forbidden absent), **abstain** (unanswerables). **correct** = exec_acc AND grounded AND facts
(answerables) or abstain (unanswerables). Aggregates: correct, correct_unseen, exec_acc, grounded, abstain, error rate, prompt and
completion tokens per question (and per model-answered question, by role), calls per model-answered question, latency p50/p95,
guard and schema rejections, repairs, truncations, per-category and per-tier rates.

## 4. Baseline (tag `baseline-v0`, results `bench/results/baseline-*-6d30e04.json`)

| Run | correct | exec_acc | grounded | abstain | prompt tok/q | completion tok/q | p50 | p95 | repairs |
|---|---|---|---|---|---|---|---|---|---|
| pure model path (`--mode llm`) | 0.733 | 0.804 | 0.944 | 1.00 | 4,304 | 217 | 5.1 s | 11.6 s | 18 |
| production (`--mode auto`, router first) | 0.750 | 0.725 | 0.963 | 1.00 | 3,071 | 141 | 4.7 s | 7.1 s | 13 |

Failure analysis of the baseline model path (categories: temporal 14%, route 20%, proximity 50%, conflict 50%): spoken names used
as ids (`{id: 'Gas Sensor 3'}`), reversed relationship directions (`(Building)-[:AFFECTS]->(Hazard)`), the "where is" few-shot copied
onto unrelated questions, unrequested time filters, `CONNECTS_TO` matched with an arrow (the road graph is stored one way), and a
parser bug that fed an unterminated code fence to the guard three times (21.7 s on one question).

## 5. Interventions (all behind `QA_FEATURES` flags so each is an ablation cell)

| Flag / change | What it does | Prompt tokens (call A) |
|---|---|---|
| D1 fixes (always on from 2f29803) | strip unterminated fences; a truncated query gets a "write a shorter query" repair instead of three guard rejections; `CONNECTS_TO` arrows rewritten to undirected; an unknown id short-circuits to an honest "no such entity" without repair rounds; no hidden client retries; per-call timeout | 2,764 |
| `entity_link` | resolve entity mentions in the question to ids and list them before the question | +~60 |
| `schema_check` | static validation of generated Cypher (relationship direction and endpoints, property names per label, id literals, id in RETURN) before execution; violations become precise repair messages | 0 |
| `fewshot_v2` | 16-example decontaminated bank (radius, time range, assignment, single-entity, source-filtered examples) | 3,191 |
| `dyn_fewshot` | keep only the 3 most similar examples (TF-IDF token overlap) | 1,549 (V0) / 1,513 (V2) |
| `compact_schema` | terser schema description | 1,091 with V2+dyn |
| `det_render` | render known row shapes (single-entity status, event list, single aggregate) with templates instead of the compose call | removes call B (~850 prompt + ~90 completion tokens, ~2 s) when it applies |
| `router_v2` | pre-baked router: explicit time ranges, decline questions with joins it cannot do, staged-at | 0 |
| `QA_MAX_REPAIRS` | repair budget (default 2) | fewer calls on failures |

Early ladder (contaminated bank, before D1; kept for the record in `bench/results/*-8d1c86c.json`): the full flag set scored
0.800 correct on the dev set but with 29 repairs (vs 18) and a higher p50; `entity_link` and `schema_check` alone did not improve
correctness and increased repair rounds. That negative result motivated the D1 fixes (most extra rounds were truncation, arrows and
unknown ids) and the decontamination.

## 6. Results after D1 (filled from `bench/results/d1-*.json`)

_pending_

## 7. Recoverability

`git tag`: `baseline-v0` (pre-work), `features-v1` (flags, unmeasured). `scripts/snapshot_state.sh <tag> "<msg>"` commits, tags
and dumps the private graph to `bench/snapshots/<tag>/graph.json`; `scripts/restore_state.sh <tag>` checks the tag out and reloads
that graph. Benchmarks refuse to run against the shared graph (port 7688).
