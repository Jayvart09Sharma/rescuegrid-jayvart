# RescueGrid answer layer: architecture decision, baseline, roadmap (2026-09-25)

## 0. What is actually on the box today (measured)

- **Code**: `/home/hp2/kenil/rescuegrid-reasoning` at commit `5556dbc` (5 commits, tag `baseline-v0`). `bench/questions.json` has 60 questions (status 8, provenance 7, temporal 7, proximity 6, route 5, aggregation 6, conflict 4, units 5, unanswerable 6, paraphrase 6; 51 with gold Cypher; sha256 `fea4829d…`). Three **single-run** result files exist. Six feature flags already exist in `rescuegrid/qa/features.py` (`QA_FEATURES=entity_link,schema_check,fewshot_v2,dyn_fewshot,compact_schema,router_v2`) but **none has been benchmarked**. The baseline prompt is pinned verbatim (2,710 tokens = schema 815 + rules 304 + 11 few-shots 1,591; `ANSWER_SYSTEM` 504).
- **Server**: vLLM 0.26.0 serving `NVIDIA-Nemotron-3-Nano-30B-A3B-FP8` as `llm` with only `--max-model-len=32768 --trust-remote-code`; `enable_prefix_caching=False`, attention block 4,176 tokens, fp8 KV, no speculative decoding (`/opt/hp/zrt/run/vllm-llm.json`, `vllm-llm.log`). `free -g`: 12 GB free / 14 GB available, **not 19**.
- **Latest run** (`baseline-llm-6d30e04.json`, llm mode): correct 0.733, exec_acc 0.804, grounded 0.944, facts 0.741, abstain 6/6, **4,377 prompt + 221 completion tokens per LLM question over 2.14 calls**, p50 5.08 s, p95 11.62 s, 18 repairs, 0 guard rejections. Per call: **call A** (Cypher/repair) 77 calls, mean 2,818 prompt / 116 completion / 3.05 s; **call B** (compose) 49 calls, mean 842 / 84 / 2.13 s. Two commits earlier the same questions scored 0.633: scoring changes plus T=0 non-determinism. Single runs cannot rank options.
- **Failure signature** (16/60 wrong): temporal 6/7 (time-window idioms `tm03/tm06/tm07`, ungrounded numbers `tm02/tm05`, omitted entity `tm04`), value lookups (`st06` beds, `st07` reading, `pv03` detected-by), proximity radius (`px04`), an error after 3 calls (`px05`), `rt05`, `ag01`, `un02`, `cf01`, `pp06` must-mention misses. Not one is a guard rejection or a write attempt: the errors are **semantic and projection-shaped**, not syntactic.
- **Cost arithmetic**: decode ~45 tok/s (22 ms/token) vs prefill ~5.5k tok/s (0.18 ms/token) ([memory: nemotron-30b-test-results](file:///home/hp2/.claude/projects/-home-hp2-kenil/memory/nemotron-30b-test-results.md)). **One completion token costs the wall time of ~120 prompt tokens.** Per LLM question ~0.75 s is prefill and ~4.4 s is decode + call overhead. Output tokens and call count are the levers; prompt compression is worth ≤0.5 s.

## 1. Decision

**LLM + KG via text-to-Cypher stays the core; the LLM leaves the prose.** A three-tier, grounded-by-construction answer layer where the model produces *structure* (typed slots, or Cypher plus a slot-bound answer template) and code renders every sentence from Neo4j rows:

| Tier | Trigger | Model work | Answer | Cost (target) |
|---|---|---|---|---|
| 1 (exists) | regex intent router, 7 intents | none | parameterised Cypher → deterministic renderer | ~0.05 s, 0 tokens |
| 2 (new, demo) | json_schema slot router: enumerated intents/sources/unit types, explicit `other`, confidence ≥ 0.6 | ~250 prompt + ~30 completion tokens | same template library, grown 7 → ~13 | ~1 s |
| 3 (exists, tightened) | everything else | compact schema + k=3 dynamic few-shots (1,152–1,301 tokens measured) → Cypher; static `schema_check`; 1 repair + zero-row retry; 8 s deadline | deterministic renderer for known row shapes, LLM compose (60-word cap) only for novel shapes; post-demo: slot-bound answer template in the same call | ~3 s, ~1.5–2.3k tokens |

Every path still runs the read-only guard, a read transaction, `_finish()` (conflicts, highlight, provenance) and the `fact_check`. The research object is the **benchmark** around this (K-repeat paired statistics, multi-snapshot execution accuracy, penalty-scored abstention, tokens-per-correct) plus pre-registered ablations of best-of-N selection and schema-compiled grammar decoding on Tier 3. This follows Neo4j's own guidance to use parameterised Cypher for common questions and Text2Cypher only as the fallback ([Neo4j Text2Cypher guide](https://neo4j.com/blog/genai/text2cypher-guide/)) and TeCoD's template-constrained result (up to 36% higher execution accuracy, 2.2× lower latency on matched queries; [arXiv 2604.28028](https://arxiv.org/abs/2604.28028)).

## 2. Why not the alternatives

| Alternative | Verdict | Reason (with numbers) |
|---|---|---|
| LLM-only, whole graph in prompt | reference row only | 7.6–8.9k tokens now, 15–60k at 100–500 events vs 32,768 max; probe invented an entity on the aggregation question; no rows → no provenance, highlight or fact-check |
| Vector RAG over event text | no | cannot express time windows, sums, routes, conflicts; fulltext clause already allowlisted in Cypher |
| GraphRAG / LightRAG / HippoRAG / ToG | no | they build a graph from text; ours exists and is queried exactly; stale in seconds; graphs help multi-hop over corpora, not fact lookups ([2502.11371](https://arxiv.org/html/2502.11371), [GraphRAG-Bench](https://arxiv.org/abs/2506.05690)) |
| Agentic ReAct / MCP loop | no | needs tool-parser relaunch; 3–4× calls at ~3 s; Neo4j's MCP agent scored 0.71 with Claude 3.7 Sonnet at 3.6 calls/q ([Neo4j](https://neo4j.com/blog/developer/evaluating-graph-retrieval-in-mcp-agentic-systems/)); Nemotron BFCL v4 53.2 ([model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8)) |
| DIN-SQL / MAC-SQL / schema linking | no | schema fits (815 tokens); linking costs 2.7–4.8 EX when it fits ([2408.07702](https://arxiv.org/html/2408.07702)); 4–8 sequential calls at 45 tok/s |
| LoRA on the 30B; hybrid students via Unsloth/GRPO | no | ~60 GB for 16-bit LoRA ([Unsloth](https://unsloth.ai/docs/models/nemotron-3)) vs 12–14 GB free; FP8 base; missing sm_121a kernels; no docker |
| Speculative decoding now | no | no MTP head in the checkpoint; ngram on hybrid recurrent models silently corrupts (Nemotron 12/60 correct before fix, PR open) ([vLLM #56531](https://github.com/vllm-project/vllm/pull/56531)); already reverted here |
| LLMLingua-2 compression | negative row at most | ceiling ~0.35 s; deletes schema identifiers Cypher must copy ([2403.12968](https://arxiv.org/abs/2403.12968)) |
| Schema-compiled grammar as the thesis | ablation, not product | 0 guard rejections in the baseline; 3/3 probes grammatical-but-wrong; filtering raises empties 1 → 24 → 36 ([Ozsoy 2605.10318](https://arxiv.org/abs/2605.10318)); closest prior art [Huawei GQL grammar](https://ceur-ws.org/Vol-4085/paper12.pdf), [PICARD](https://aclanthology.org/2021.emnlp-main.779/) |
| Neo4j Gemma-3-4B specialist | post-demo ablation at most | ~8 GB + KV in 12–14 GB; no published accuracy; team decision |
| Server relaunch before demo | no | ≤0.7 s upside; Mamba caching experimental; PR #46384 lists shared-prefix-then-divergent workloads as a limitation ([vLLM #46384](https://github.com/vllm-project/vllm/pull/46384)) |

## 3. Baseline (exact, to be frozen as `baseline-v1`)

Code `5556dbc`, `QA_FEATURES` unset (pinned prompt), `LLM_THINKING=false`, temperature 0, call A `max_tokens=320`, call B json_schema `COMPOSE_MAX_TOKENS=320`, `max_repairs=2` + zero-row retry, one fact-check regeneration; graph restored from `bench/snapshots/baseline-v0/graph.json` (sha256 `7f101435…`, 20 entities / 10 events / 36 rels) with `RESCUEGRID_CLOCK=replay`; the 60-question file above; the server configuration above; box idle (record vLLM running requests).

```
NEO4J_URI=bolt://127.0.0.1:7687 .venv/bin/python bench/run_bench.py --label baseline-v1-k1 --mode llm   # repeat k1..k3, then --mode auto
scripts/snapshot_state.sh baseline-v1 "baseline-v1: K=3 llm+auto, fingerprinted"
scripts/restore_state.sh baseline-v1   # code at tag + private graph reloaded from the dump
```

Reference numbers to reproduce within CI are the `6d30e04` run listed in §0. Three additions to `run_bench.py` before running: (a) a **role** per call (`cypher`, `repair_k`, `zero_row_retry`, `compose`, `compose_retry`, `router`) set via a module-level context variable that `Text2Cypher.run` / `QAEngine._compose` update, since today every ledger entry is anonymous; (b) a **fingerprint** block: git sha, sha256 of `SCHEMA_TEXT`+`ANSWER_SYSTEM`, sha256 of `questions.json` and the snapshot, the contents of `vllm-llm.json`, vLLM version, `QA_FEATURES`, K, temperature, and the concurrent-request count; (c) **K repeats** with mean ± seed-clustered SE, Wilson 95% CI, `pass^3` and paired exact McNemar vs a named baseline file ([Miller 2024](https://arxiv.org/html/2411.00640), [Wilson](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval), [tau-bench pass^k](https://arxiv.org/abs/2406.12045)). Mark the 11 few-shot and 7 template questions `seen` (`rt01` is verbatim in the few-shots).

## 4. Metrics

**Correct** = for answerable: `EX_strict ∧ Grounded ∧ Facts ∧ HighlightAcc`; for unanswerable: correct abstention. **EX_strict**: multiset row equality up to column permutation, ordered iff gold has `ORDER BY`, floats 3 dp, ISO datetimes, nested lists sorted, empty==empty → 1 (CypherBench semantics, [scorer](https://github.com/megagonlabs/cypherbench/blob/main/cypherbench/metrics/execution_accuracy.py)); today's key-column set match becomes the lenient **EX_key**. **EX_multi** = min over 4–6 replayed snapshots of EX_strict with each snapshot's clock as `$now` (test-suite accuracy adapted to a time-versioned graph, [Zhong et al. 2020](https://aclanthology.org/2020.emnlp-main.29/)). **EvJ** = Jaccard of entity-id sets in rows. **Grounded** = `fact_check` finds no source/time/confidence/percent outside the rows. **Facts** = must_mention/must_not_mention. **HighlightAcc** = `highlight.id` equals the gold subject. **AbstentionRecall / FalseRefusal** from an explicit `answerable` field (regex until then). **RS_c** (TrustSQL, [2403.15879](https://arxiv.org/html/2403.15879v6)): +1 correct or correct abstention, 0 abstain-on-answerable, −c wrong or answered-unanswerable, c ∈ {0, 10, N}; **RS_10 is the pre-registered primary metric**. **pass^3**, **ConsistencyRate** over paraphrase groups. **Cost**: prompt/completion/cached tokens per call role, calls per LLM question, LLM-seconds, wall p50/p95, **TokensPerCorrect** and **SecondsPerCorrect** ([Cost-of-Pass](https://arxiv.org/abs/2504.13359), [Kapoor et al.](https://arxiv.org/html/2407.01502)), every variant on an RS_10-vs-tokens Pareto chart. **Router**: coverage and false-route rate as a curve over the gate threshold. **Error taxonomy** (one code per failure; 20 double-labelled).

## 5. Roadmap (each step = hypothesis → measurement; D = before the demo, R = research week)

| # | Step | Hypothesis | Expected gain | Effort | Measure |
|---|---|---|---|---|---|
| S0 D | Freeze `baseline-v1` (roles, fingerprint, K=3, seen flags) | numbers today are single-run and stale | defensible table, CI ±11 pp at n=60 | 3 h + 45 min box | restore reproduces mean within CI |
| S1 D | Sweep the six existing flags as cells | `compact_schema+dyn_fewshot` cuts call-A prompt 2,818 → ~1,300 (measured 2,710 → 1,301; 1,152 with `fewshot_v2`) at no loss; `entity_link+schema_check` cut repairs 18 → <10 and value-lookup/linking misses | prompt/LLM-q 4.4k → ~2.3k; Correct +5–10 pp on temporal/units (unverified) | 1 h + ~50 min box per cell | paired McNemar per cell; accept only if Correct not worse and abstain 6/6 |
| S2 D | `max_repairs` 2 → 1, structured schema-check errors, 8 s deadline → honest/pre-baked fallback | round 2 rarely rescues (`tm05` 5 calls 14.4 s wrong; `px05` error); adaptive budgeting saves 21–25% wall for 0.2–0.5% ([2609.02324](https://arxiv.org/abs/2609.02324)) | p95 11.6 → <8 s; ~2.9k tokens per avoided round | 2 h | p95, repairs, paired Correct, deadline hits |
| S3 D | Skip call B: deterministic renderer for known row shapes (status rows, event lists, routes, aggregates) on `_rows_summary`/fallback builders; LLM compose with 60-word cap only for novel shapes | call B = 842+84 tokens, 2.13 s on 49/59 questions and the entry point of ungrounded numbers (`tm02/tm05/px05`) | −2.1 s, −900 tokens on ≥60% of LLM questions; grounded 0.944 → ~1.0 | 4–5 h | Grounded, Facts, tokens by role (compose calls/q < 0.4), latency; renderer benchmarked like any path, per-shape unit test |
| S4 D (stretch) | Tier 2 slot router (json_schema, enumerated vocab, `other`, gate ≥0.6, 3 few-shots) + templates 7 → ~13: value_lookup, event_detail/first_event, time_range_events, status_since, confirmed_since, within_radius, near_unit, staged_at, blocked_roads_with_buildings, people_in_status | failing categories are recurrent shapes; TeCoD +36% EX / 2.2× latency on matched queries | routed ~250+30 tokens, ~1 s; coverage ≥50% of tail, false-route <5%; temporal 0.14 → ≥0.7; Correct ≥0.85 (unverified) | 5–6 h; ship the green subset | coverage/false-route curve; one regression test per template |
| S5 D | Demo freeze `demo-v1`: mode=auto, rehearse scripted questions ×3, no relaunch, no T>0, fusion-agent LLM calls off the QA path | co-tenant load halves decode (16–34 tok/s measured) | scripted <2 s, pass^3 = 1.0; tail bounded 8 s | 1 h | `--only <ids>` at K=3 |
| S6 R | Evaluation upgrade: EX_strict/EvJ scorer, explicit `answerable`/`abstain_reason`, RS_c, multi-snapshot EX (ingest subsets + per-snapshot `$now`), +15 unanswerables, short/radio-speak tiers to ~140 q, taxonomy with second annotator | single snapshot hides over-filtering (2.5% avg / 8.1% worst-case false negatives in [Zhong 2020](https://aclanthology.org/2020.emnlp-main.29/)); short-NL drops accuracy sharply ([Jackal](https://arxiv.org/abs/2509.23579): 86.0% long vs 35.7% short) | reviewable benchmark; CI ±7.5 pp; EX_strict−EX_multi gap | 1 day + 3 h | re-score frozen files first (rows stored) |
| S7 R | Projection macro: model emits MATCH…LIMIT + `RETURN` marker; code appends canonical provenance projection; explicit RETURN for computed values | RETURN…LIMIT is 50% of few-shot Cypher tokens; 1 output token ≈ 120 prompt tokens; fixes omitted-id misses | call-A completion 116 → ~65 (−1.1 s), prompt −700 | 0.5 day | completion by role, projection_omission count |
| S8 R | One-call Cypher + **slot-bound answer template** (`{name} is {status} since {status_since} ({source}, {confidence})`), slots validated against RETURN aliases, literals outside slots rejected | grounding enforced at the interface; prior art templates the query (TeCoD, [AWS](https://aws.amazon.com/blogs/architecture/reducing-text2sql-latency-with-parameterized-query-templates/)), not the verbalisation | compose removed on ~all LLM questions; Grounded 1.0 by construction; +30–50 tokens on call A | 1 day | compose ablation {LLM, renderer, template} + 20-answer blind fluency rating |
| S9 R | Best-of-4 in one request (T=0.7; n>1 with json_schema/EBNF verified on this endpoint), guard+schema filter, execute all, select by agreement/non-empty/mean logprob | greedy is already non-deterministic; 77.6 → 85.4% with confidence selection on Gemma-2-9B ([2605.10318](https://arxiv.org/abs/2605.10318)); gains from 3 samples ([2503.24364](https://arxiv.org/html/2503.24364)); n=4 = 1.5× wall here | tail exec +5–10 pp at ~3.5× call-A completion; cannot fix unanimous misreads | 0.5–1 day | selection-signal ablation on EX_strict, RS_10, tokens-per-correct |
| S10 R | Schema-compiled typed EBNF via xgrammar `structured_outputs.grammar`, per-pattern variable binding, coverage test over all 51 gold queries | grammar fixes form not pattern choice; beam > sample+vote under constraints, larger model > more compute ([2608.25761](https://arxiv.org/abs/2608.25761); its constrained-vs-unconstrained Spider numbers are **unverified**, lenses disagree) | rejections → 0; EX ±5 pp; empties measured | 1.5–2 days | 2×2 {few-shots} × {grammar} + schema_check on/off |
| S11 R | Team relaunch bundle: `--enable-prefix-caching --mamba-cache-mode align --prefix-match-unit 16 --enable-prompt-tokens-details` (optional `--kv-cache-dtype auto`), warm-up request ending at the shared prefix boundary | caching is simply off; flags exist in 0.26.0 ([#46384](https://github.com/vllm-project/vllm/pull/46384), [#40696](https://github.com/vllm-project/vllm/issues/40696), [#44377](https://github.com/vllm-project/vllm/issues/44377)) | −0.4 to −0.7 s per LLM question; cached_tokens reported; possibly more variance | 2 h + 4 min restart | `prefix_cache_hits_total>0`, cached_tokens, pass^3 before/after; revert if 0 |
| S12 R (conditional) | Pattern-first synthetic pairs → LoRA r=16 BF16 on Qwen2.5-Coder-0.5B/1.5B (PEFT smoke test: 1,692 tok/s, 9.8 GB peak), merged, third ZRT label at ~0.08; data-size curve 250/500/1k/2k; free-Cypher vs `template_id+params` student | small fine-tuned models approach frontier Text2Cypher ([CYQUARK 2606.14325](https://arxiv.org/abs/2606.14325); exact averages inconsistent across sources, unverified); pairs-per-fixed-schema is unpublished | call-A prompt ~300 tokens; decode only 1.5–2× faster at 273 GB/s | 3–5 days; needs team OK and idle vision model | model-swap cell on held-out compositional split |

Expected end state after S1–S4 (to be measured, not assumed): per question mean ~1.0–1.5k tokens (vs 4.3k), p50 ~1–3 s (vs 5.1 s), p95 <8 s (vs 11.6 s), Correct ≥0.85 with abstain 6/6.

## 6. Ablation plan (pre-registered; K=3; paired vs baseline-v1; RS_10 primary, EX_strict secondary)

A0 reference rows: llm-only / auto / router-only / whole-graph LLM-only. A1 prompt: pinned vs compact vs dyn-k3 vs compact+dyn vs compact+v2+dyn (leakage-controlled). A2 validation/repair: schema_check × max_repairs {0,1,2} × zero-row retry. A3 compose: LLM / LLM+cap / deterministic / slot-template (+ blind fluency). A4 Tier-2 router off/on × gate {0.5, 0.6, 0.8} as coverage-vs-risk. A5 decoding: greedy vs best-of-4 × selection signal. A6 grammar off/typed × few-shots static/kNN with coverage. A7 thinking off/on (abstention expected to worsen, [AbstentionBench](https://arxiv.org/abs/2506.09038)). A8 prefix caching off/on (latency, cached_tokens, pass^3). A9 negative rows: LLMLingua-2; ngram spec-decode greedy-equality test. A10 stretch: student × data source × data size. Box time: ~7 min per llm pass × K=3 → a 10-cell grid is ~3.5 h overnight.

## 7. Recoverability protocol

Every experiment = one env-flag configuration (`QA_FEATURES=…`, baseline defaults when unset) + `bench/results/<label>-<sha>-k<i>.json` (never overwritten, fingerprinted) + `scripts/snapshot_state.sh <tag>` (git tag + graph dump + copies of results). `scripts/restore_state.sh <tag>` checks out the tag, stashes uncommitted work and reloads the private graph from the dump; both scripts refuse the shared 7688 instance. Add to the snapshot: the few-shot bank, the template library, the router prompt and `vllm-llm.json`, so a restore reproduces the exact prompt and server contract. "Come back to now" = `scripts/restore_state.sh baseline-v1` (or `demo-v1`).

## 8. Resume framing

"RescueGrid-QA: a provenance- and time-first text-to-Cypher benchmark and answer layer for an offline EOC assistant on a 30B-A3B hybrid Mamba/MoE (FP8, vLLM 0.26) on an NVIDIA GB10." Contributions: (1) a measured decode-bound cost model (1 output token ≈ 120 prompt tokens) that motivates output-token-first design over prompt compression; (2) grounded-by-construction verbalisation (slots or Cypher + slot-bound answer template, rendered from rows) that removes the compose call and makes provenance grounding 1.0 by construction; (3) router/template/text-to-Cypher tiers reported as a coverage-vs-risk curve with penalty-scored abstention (RS_10), so "humans stay in control" is a measured property; (4) multi-snapshot execution accuracy for a time-versioned property graph; (5) pre-registered ablations (prompt, repair budget, best-of-N selection, schema-compiled grammar) under K-repeat paired statistics that quantify serving nondeterminism ([Thinking Machines](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)). What would make it look like prompt-tweaking: a single-run accuracy on 60 questions that include the few-shots, no CIs, no cost axis, a self-judged faithfulness score.

## 9. Risks

Stale single-run baseline (fix: S0 first). Co-tenant load during the demo (scripted questions on Tier 1/2, 8 s deadline, load recorded). Confident mis-routes (gate ≥0.6, `other`, per-template tests, no auto-promotion of executed LLM queries: exec_acc ~0.8 means ~1 in 5 is wrong). Mechanical prose (renderer benchmarked; LLM compose for novel shapes). Few-shot/benchmark leakage (`rt01` verbatim; seen flag, frozen set before any bank generation). n=60 noise (pairing; extend to ~140). Grammar coercion and empties (coverage test; ablation only). Best-of-N token cost and variance (post-demo; tokens-per-correct). Prefix caching may not hit and may add variance (verify hits, K=3). Memory 12–14 GB (sequential train/serve, MAX_JOBS cap, team decision). Shared-schema drift (pinned snapshot; schema-driven templates/check). Self-judged metrics (deterministic headline; calibrate any judge on ~80 labels). Over-claiming (cite TeCoD, PICARD, Huawei GQL grammar, Ozsoy 2026, Zhong 2020 as the closest prior art).

## References

- vLLM PR #46384 partial prefix-cache hit for hybrid models: https://github.com/vllm-project/vllm/pull/46384
- vLLM issue #40696 hybrid block cliff: https://github.com/vllm-project/vllm/issues/40696
- vLLM PR #56531 ngram corruption on hybrid recurrent models: https://github.com/vllm-project/vllm/pull/56531
- vLLM issue #44377 cached_tokens reporting: https://github.com/vllm-project/vllm/issues/44377
- vLLM structured outputs: https://docs.vllm.ai/en/latest/features/structured_outputs/
- Nemotron-3-Nano-30B-A3B-FP8 model card: https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8
- Unsloth Nemotron 3 fine-tuning: https://unsloth.ai/docs/models/nemotron-3
- TeCoD template-constrained decoding: https://arxiv.org/abs/2604.28028
- Neo4j Text2Cypher guide (2026): https://neo4j.com/blog/genai/text2cypher-guide/
- Neo4j MCP agent evaluation: https://neo4j.com/blog/developer/evaluating-graph-retrieval-in-mcp-agentic-systems/
- Ozsoy et al., confidence/grammar/schema filtering for Text2Cypher: https://arxiv.org/abs/2605.10318
- Adaptive test-time inference for Text2Cypher: https://arxiv.org/abs/2609.02324
- Query-and-Conquer execution-guided selection: https://arxiv.org/html/2503.24364
- Grammar-constrained text-to-SQL with Qwen2.5: https://arxiv.org/abs/2608.25761
- CYGNET/RAMPART validation gate: https://arxiv.org/abs/2606.04645
- CYQUARK grounded synthetic Text2Cypher data: https://arxiv.org/abs/2606.14325
- Schema-constrained grammar-guided GQL (Huawei, CEUR 2024): https://ceur-ws.org/Vol-4085/paper12.pdf
- PICARD: https://aclanthology.org/2021.emnlp-main.779/
- CypherBench: https://arxiv.org/html/2412.18702v1 ; scorer: https://github.com/megagonlabs/cypherbench/blob/main/cypherbench/metrics/execution_accuracy.py
- Test-suite accuracy (Zhong et al. 2020): https://aclanthology.org/2020.emnlp-main.29/
- TrustSQL reliability score: https://arxiv.org/html/2403.15879v6
- AbstentionBench: https://arxiv.org/abs/2506.09038
- Jackal (long vs short NL): https://arxiv.org/abs/2509.23579
- Neo4j schema filtering: https://arxiv.org/html/2505.05118v1
- DAIL-SQL example selection: https://arxiv.org/abs/2308.15363
- The Death of Schema Linking: https://arxiv.org/html/2408.07702
- DIN-SQL: https://arxiv.org/abs/2304.11015
- RAG vs GraphRAG: https://arxiv.org/html/2502.11371 ; GraphRAG-Bench: https://arxiv.org/abs/2506.05690
- LLMLingua-2: https://arxiv.org/abs/2403.12968
- Adding error bars to evals (Miller 2024): https://arxiv.org/html/2411.00640
- Defeating nondeterminism in LLM inference: https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- AI Agents That Matter (cost-controlled evaluation): https://arxiv.org/html/2407.01502 ; Cost-of-Pass: https://arxiv.org/abs/2504.13359
- tau-bench pass^k: https://arxiv.org/abs/2406.12045 ; Wilson interval: https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval
- AWS parameterized query templates: https://aws.amazon.com/blogs/architecture/reducing-text2sql-latency-with-parameterized-query-templates/
- Local: /opt/hp/zrt/run/vllm-llm.json, /opt/hp/zrt/run/vllm-llm.log, /home/hp2/kenil/rescuegrid-reasoning/bench/results/baseline-llm-6d30e04.json, /home/hp2/.claude/projects/-home-hp2-kenil/memory/nemotron-30b-test-results.md