# RescueGrid answer layer: architecture decision, baseline, roadmap (revised after red-team review, 2026-09-25)

## 0. What is actually on the box today (measured)

- **Code**: `/home/hp2/kenil/rescuegrid-reasoning` at HEAD `8d1c86c` (7 commits; tags `baseline-v0`, `features-v1`), with four **untracked** ladder result files (`all-auto`, `all-llm`, `l1-entity`, `l2-entity-schema` at `8d1c86c`, produced from a dirty tree). `bench/questions.json` has 60 questions (fields: id, question, category, answerable, gold_cypher, compare, must_mention, must_not_mention, note; **no** gold highlight id, seed id or phrasing tier). Six feature flags exist in `rescuegrid/qa/features.py`. The baseline prompt is pinned verbatim (2,710 tokens = schema 815 + rules 304 + 11 V0 few-shots 1,591; `ANSWER_SYSTEM` 504).
- **Server**: vLLM 0.26.0 serving `NVIDIA-Nemotron-3-Nano-30B-A3B-FP8` as `llm` with only `--max-model-len=32768 --trust-remote-code`; `enable_prefix_caching=False`, attention block 4,176 tokens, fp8 KV, no speculative decoding (`/opt/hp/zrt/run/vllm-llm.json`). `free -g` today: **9 GB free / 11 GB available** (the task context's 19 GB and the earlier 12-14 GB are stale).
- **Baseline run** (`baseline-llm-6d30e04.json`, llm mode): correct 0.733, exec_acc 0.804, grounded 0.944, facts 0.741, abstain 6/6, 4,377 prompt + 221 completion tokens per LLM question over 2.14 calls, p50 5.08 s, p95 11.62 s, 18 repairs, 0 guard rejections. RS_0 0.73, RS_1 0.47, **RS_10 -1.93**. Modes used: llm 54, **llm+fallback 4** (rt01, rt04, rt05, cf04: the template answered when the model failed), fallback 1, error 1. rt01/rt04/pp03 have no gold rows (exec_acc=None counts as correct today).
- **Contamination (verified)**: `EXAMPLES_V2` in `rescuegrid/qa/fewshot.py` contains **six benchmark questions verbatim** (rt01, px04, cf01, tm07, un01, st01) and near-duplicates of tm01/tm02/na03 (token Jaccard 0.75-0.80); the V0 bank contains rt01 verbatim and tm01/tm02 near-duplicates. `schema_check.py`'s docstring says it "catches the failure classes the benchmark exposed". The 60 questions are therefore a **dev set** that has already been tuned against.
- **The all-flags run re-read** (`all-llm-8d1c86c.json` vs `baseline-llm-6d30e04.json`): 0.800 vs 0.733 overall, but fixed = {st06, st07, tm05, tm07, px04, rt05, ag01, cf01} of which **tm07, px04, cf01 are leaked**, broke = {pv05, px02, rt02, ag03}; on the **51 uncontaminated questions it is 40/51 vs 39/51 (+1 question)**; repairs 18 -> 29, calls 2.14 -> 2.42, p50 5.08 -> 5.79 s, guard rejections 0 -> 2, three `finish=length` truncations; prompt tokens did fall 4,377 -> 2,477. `l1-entity`: 0.717, repairs 24; `l2-entity-schema`: 0.717, repairs 33, error rate 0.067, p95 13.55 s. Static schema checking as implemented **adds rounds without rescuing questions**.
- **Latency outliers are bugs**: px02 (21.7 s, error) hit `finish=length` at 320 tokens three times with a degenerate projection; `extract_cypher`'s fence regex requires a closing fence, so the truncated output kept its leading ```` ```cypher ```` and was rejected by the guard twice, and the truncated query was re-fed as the "previous query". rt01 (17.4 s) spent 3 x ~250 completion tokens before falling back **although its question is verbatim in the V0 bank** (leakage does not even guarantee a pass). The OpenAI client has `max_retries=1` and a 120 s timeout, so hidden retries are outside the ledger.
- **Grounding metric is not what it claims**: `run_bench.py` appends conflict/provenance rows the engine did not use (tm02 is flagged ungrounded by the bench but not by the engine); zero-row wrong answers score grounded=True (st06, st07, pv03, px04); the **existing deterministic renderers fail the check** (tm07 in auto mode prints the window boundary; the route renderer cites road times absent from its evidence rows).
- **Cost arithmetic (design rationale, not a contribution)**: decode ~45 tok/s vs prefill ~5.5k tok/s, so one completion token costs the wall time of ~120 prompt tokens; output tokens and call count are the levers, prompt compression is worth <= 0.5 s.

## 1. Decision

**LLM + KG via text-to-Cypher stays the core; the benchmark becomes the research object; the answer layer is engineering adopted only under a pre-specified test.**

| Tier | Trigger | Model work | Answer | Status |
|---|---|---|---|---|
| 1 (exists) | regex intent router, 7 intents | none | parameterised Cypher -> deterministic renderer, reconciled with the grounding check | demo path for scripted questions |
| 2 (post-demo) | `guided_choice` intent with **intent-token logprob gate** + deterministic slot validation + regex/BM25 agreement | ~250 prompt + ~30 completion tokens; miss path measured | template library 7 -> ~13, designed from the EOC taxonomy on the dev split | adopted only if coverage-vs-false-route and tail p50 with router pass |
| 3 (exists, fixed) | everything else | pinned prompt now; compact schema + decontaminated dynamic few-shots after R2 -> Cypher; parser fixes (D1); repair budget by ablation (R3) | LLM json_schema compose now; row-only renderer for two shapes (D2); slot-bound answer template post-demo (R5) | per-call timeout with an honest `abstain_reason=timeout` |

Every path still runs the read-only guard, a read transaction, `_finish()` and `fact_check`. This follows Neo4j's guidance to use parameterised Cypher for common questions and Text2Cypher as the fallback ([Neo4j Text2Cypher guide](https://neo4j.com/blog/genai/text2cypher-guide/)); TeCoD ([arXiv 2604.28028](https://arxiv.org/abs/2604.28028)) is the closest prior art for template routing and is cited as such, **without** quoting its BIRD gains as an expectation here.

## 2. Why not the alternatives

| Alternative | Verdict | Reason (with numbers) |
|---|---|---|
| LLM-only, whole graph in prompt | reference row | 7.6-8.9k tokens now, 15-60k at 100-500 events vs 32,768 max; probe invented an entity; no rows -> no provenance/highlight/fact-check |
| Vector RAG over event text | no | cannot express time windows, sums, routes, conflicts; fulltext clause already allowlisted |
| GraphRAG / LightRAG / HippoRAG / ToG | no | build a graph from text; ours exists and is queried exactly; graphs help multi-hop over corpora, not fact lookups ([2502.11371](https://arxiv.org/html/2502.11371), [GraphRAG-Bench](https://arxiv.org/abs/2506.05690)) |
| Agentic ReAct / MCP loop | no | tool-parser relaunch; 3-4x calls at ~3 s; Neo4j's MCP agent 0.71 with Claude 3.7 Sonnet at 3.6 calls/q ([Neo4j](https://neo4j.com/blog/developer/evaluating-graph-retrieval-in-mcp-agentic-systems/)); Nemotron BFCL v4 53.2 ([model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8)) |
| DIN-SQL / MAC-SQL / schema linking | no | schema fits (815 tokens); linking costs 2.7-4.8 EX when it fits ([2408.07702](https://arxiv.org/html/2408.07702)) |
| **entity_link + schema_check as the fix** | ablation only | measured 0.717 / repairs 24 and 0.717 / repairs 33 / error 0.067 at `8d1c86c`; written from dev failures |
| **fewshot_v2 + dyn_fewshot on the 8d1c86c evidence** | decontaminate first | 6 verbatim + 3 near-dup leaks; +1 unseen question; 4 broke |
| LoRA on the 30B; hybrid students | no | ~60 GB for 16-bit LoRA ([Unsloth](https://unsloth.ai/docs/models/nemotron-3)) vs 9-11 GB; FP8 base; missing sm_121a kernels |
| Speculative decoding now | no | no MTP head; ngram on hybrid recurrent models silently corrupts ([vLLM #56531](https://github.com/vllm-project/vllm/pull/56531)); already reverted |
| LLMLingua-2 compression | negative row | ceiling ~0.35 s; deletes schema identifiers ([2403.12968](https://arxiv.org/abs/2403.12968)) |
| Schema-compiled grammar as thesis | ablation | 0 guard rejections in baseline; 3/3 probes grammatical-but-wrong; empties 0 -> 24 -> 36 ([Ozsoy 2026](https://arxiv.org/html/2605.10318), verified in the paper body) |
| **8 s deadline returning 'no matching records'** | replaced | px02 is a parser bug; a timeout message that claims no records is false and scores as an abstention |
| **Tier-2 router before the demo, self-reported confidence gate** | post-demo, logprob gate | uncalibrated; miss path 72 completion tokens / 3.74 s under load; regex router already gives temporal 0.43 in auto |
| **RS_10 as primary** | secondary | baseline -1.93; all-abstain about +0.1; answer-30-refuse-24 about +0.6 |
| Neo4j Gemma-3-4B specialist | no | does not fit in 9-11 GB without stopping vision; no published accuracy |
| Server relaunch before demo | post-demo | <= 0.7 s upside only if the static prefix exceeds one Mamba block ([vLLM #46384](https://github.com/vllm-project/vllm/pull/46384)) |

## 3. Baseline (`baseline-v1`, frozen in D0)

Code HEAD `8d1c86c` + committed ladder files + the ledger changes below; `QA_FEATURES` unset; `LLM_THINKING=false`; T=0; call A `max_tokens=320`; call B json_schema `COMPOSE_MAX_TOKENS=320`; `max_repairs=2` + zero-row retry; one fact-check regeneration; client `max_retries=1`/120 s (recorded, changed in D1); graph from `bench/snapshots/baseline-v0/graph.json` (sha256 `7f101435...`) with `RESCUEGRID_CLOCK=replay`; `bench/questions.json` (sha256 `fea4829d...`) with gold rows added for rt01/rt04/pp03; server as in section 0; idle box.

```
for k in 1 2 3; do for m in llm auto llm-pure; do
  NEO4J_URI=bolt://127.0.0.1:7687 .venv/bin/python bench/run_bench.py --label baseline-v1-k$k --mode $m; done; done
scripts/snapshot_state.sh baseline-v1 "baseline-v1: K=3 llm+auto+llm-pure, fingerprinted"
scripts/restore_state.sh baseline-v1   # branch from the tag + private graph reloaded
```

Ledger additions to `run_bench.py` before running: (a) a **role** per call (`cypher`, `repair_k`, `zero_row_retry`, `compose`, `compose_retry`, `router`) via a context variable, so `repairs` stops meaning "non-json calls minus 1"; (b) a **fingerprint** block: git sha + dirty-tree flag, sha256 of `SCHEMA_TEXT`, `ANSWER_SYSTEM` and the **rendered few-shot bank**, sha256 of `questions.json` and the snapshot, `vllm-llm.json`, vLLM version, `QA_FEATURES`, K, temperature, free/available memory, vLLM running-request gauge; (c) **per-run `seen` ids** (verbatim or token-Jaccard >= 0.7 to any bank question) and `Correct_unseen`; (d) **K repeats** with Wilson CI, seed-clustered SE, `pass^3`, paired bootstrap and exact McNemar vs a named file ([Miller 2024](https://arxiv.org/html/2411.00640), [tau-bench pass^k](https://arxiv.org/abs/2406.12045)); (e) `--mode llm-pure` (`allow_fallback=False`) and per-`mode_used` counts. Reproducibility = majority-of-K agreement >= 90% of questions and tokens within 5% (not "within the CI", which anything satisfies at +/-11 pp).

## 4. Metrics

**Correct_unseen (primary)** = for answerable questions EX and Grounded and Facts and mode != error (HighlightAcc joins once gold highlight ids exist); for unanswerable questions an explicit abstention; restricted to ids not flagged `seen` for that run's bank. **Acceptance rule** for adopting any change: lower bound of the 90% paired-bootstrap interval on the mean-of-K difference > -3 pp, no category loses more than one question, repairs and p95 not worse, AbstentionRecall not lower, **FalseRefusal <= 5%**; n=60 cannot certify gains below ~10 pp, so gains are claimed only on the held-out set. **EX_strict** = multiset row equality against **any accepted gold shape** (count or list; ordered iff gold has ORDER BY; CypherBench semantics, [scorer](https://github.com/megagonlabs/cypherbench/blob/main/cypherbench/metrics/execution_accuracy.py)); **EX_key** lenient secondary; **EvJ** entity-id Jaccard; **EX_multi** = min over 4-6 replayed snapshots ([Zhong et al. 2020](https://aclanthology.org/2020.emnlp-main.29/)). **Grounded** over answered-with-rows questions only, same `fact_check` inputs in engine and bench, with **GroundedCoverage** alongside; "by construction" is a unit-tested property limited to sources/times/confidences/percentages. **Facts**, **HighlightAcc** (post-demo). **AbstentionRecall / FalseRefusal** from explicit `answerable` / `abstain_reason` fields; `mode=error` never counts. **RS_c** ([TrustSQL](https://arxiv.org/html/2403.15879v6)) at c in {0, 1, 10}, RS_1 on the Pareto chart, RS_10 reported not optimised. **pass^3**, **ConsistencyRate**. **Tokens by role**, truncation and guard counts, cached_tokens when exposed. **Latency** p50/p95, LLM-seconds, timeouts, load and memory. **TokensPerCorrect / SecondsPerCorrect** ([Cost-of-Pass](https://arxiv.org/abs/2504.13359)). **Router** coverage-vs-false-route curve ([selective classification](https://arxiv.org/abs/1705.08500)). **Seen flag + bank hash**. **Error taxonomy** with a second annotator on 20 items.

## 5. Roadmap (D = demo window, ~8 h code + ~2 h box; minimum cut D0-lite + D1 + D3 about 5 h; R = research week)

| # | Step | Hypothesis | Expected | Effort | Measure |
|---|---|---|---|---|---|
| D0 | Freeze `baseline-v1` at `8d1c86c`: commit ladder files, role ledger, fingerprint, seen-per-run, K=3, `llm-pure`, gold rows for rt01/rt04/pp03, snapshot only this tag's results, restore creates a branch | numbers are single-run, dirty-tree, template-substituted and `seen` is a bank property | Correct ~0.70-0.75 (unseen ~0.77), CI +/-11 pp; llm-pure lower on routes | 2.5 h + 65 min box | >= 90% majority-of-K agreement on restore |
| D1 | Parser/loop fixes: strip unterminated fence; `finish=length` as its own repair reason, never re-feed a truncated query; `max_retries=0` + per-call timeout; honest `abstain_reason=timeout`; one proxy-cancellation check | px02 21.7 s and rt01 17.4 s are loop artefacts; hidden retries are off-ledger | p95 11.6 -> ~9 s, truncations become one short repair, accuracy unchanged | 1.5 h + 15 min box | finish=length, guard rejections, p95, paired non-inferiority |
| D2 (opt.) | Row-only renderer for single-entity status and event-list shapes; fix tm07/rt01/rt04 renderer grounding; unit test every rendered answer passes `fact_check` on its own rows; LLM compose (60-word cap) elsewhere | call B = 842+84 tokens, 2.13 s on 49/59; renderers are not yet grounded | -2 s, -900 tokens on ~40% of LLM questions; Grounded not lower | 3 h + 25 min box | Grounded/coverage, Facts, tokens by role, p50 |
| D3 | `demo-v1`: mode=auto, pinned prompt (flags only if a decontaminated K=3 cell passes the rule in time); scripted questions Tier-1 only, rehearsed x3; no relaunch, no T>0 | co-tenant load halves decode (16-34 tok/s) | scripted < 2 s, pass^3 = 1.0; unscripted bounded by an honest timeout | 1 h | `--only <ids>` at K=3 |
| R1 | Held-out set (~100 q from the taxonomy by entity-swap, ~20 unanswerables, paraphrase/radio-speak tiers with seed ids, gold highlight ids, accepted gold shapes); decontaminate V2 bank; EX_strict/EvJ scorer; explicit `answerable`/`abstain_reason`; multi-snapshot EX; taxonomy with second annotator; re-score frozen files first | the 60 are a dev set; single snapshot hides over-filtering (2.5% avg / 8.1% worst, Zhong 2020); short NL drops accuracy ([Jackal](https://arxiv.org/abs/2509.23579)); abstention saturated | reviewable benchmark, CI +/-8 pp, seen = 0 | 1.5 d + 4 h | re-scored baseline-v1/demo-v1 |
| R2 | Flag ladder (10 cells, K=3) on the held-out set with the decontaminated bank; 8d1c86c single runs as first data points | compact+dyn cuts prompt at no loss; entity_link/schema_check add rounds without rescues | prompt 4.4k -> ~2.3k; a documented negative result | 1 h + 3.5 h box | acceptance rule per cell |
| R3 | Repair budget ablation (A2) with the D1 truncation repair | round 2 rarely rescues (tm05); adaptive budgets save 21-25% wall for 0.2-0.5% ([2609.02324](https://arxiv.org/abs/2609.02324)) | p95 < 8 s | 2 h + 2.5 h box | p95, repairs, FalseRefusal |
| R4 | Projection macro + short few-shots | RETURN..LIMIT is ~50% of few-shot Cypher; 1 output token = ~120 prompt tokens | call-A completion 116 -> ~65; truncations -> 0 | 0.5 d | tokens by role, projection_omission |
| R5 | Slot-bound answer template (template NLG, [Reiter & Dale](https://doi.org/10.1017/CBO9780511519857)) with closed connective vocabulary and a validator rejecting any out-of-slot entity/status/number/time token; blind fluency rating | grounding for checked value classes enforced at the interface; contribution is the measured trade-off | compose removed on most LLM questions; fluency loss measured | 1 d | A3 |
| R6 | Tier-2 router with logprob gate + slot validation + regex/BM25 agreement; templates from the taxonomy; tail p50 with router measured | recurrent EOC shapes; TeCoD as prior art, no transferred gain | coverage-vs-false-route curve | 1 d | A4 |
| R7 | Best-of-4, execution-grounded selection | 77.6 -> 85.4% with confidence selection on Gemma-2-9B ([Ozsoy 2026](https://arxiv.org/html/2605.10318), verified); gains from 3 samples ([2503.24364](https://arxiv.org/html/2503.24364)) | +5-10 pp tail EX at ~3.5x tokens | 0.5-1 d | A5 |
| R8 | Schema-compiled typed EBNF via xgrammar, coverage test over gold | validity up, EX flat, empties up; beam > sample+vote under constraints ([2608.25761](https://arxiv.org/abs/2608.25761)) | rejections -> 0; EX +/-5 pp | 1.5-2 d | A6 |
| R9 | Relaunch bundle with the hit condition stated (static prefix >= one Mamba block after KV dtype), padded vs unpadded | caching is off; SSM state checkpointed at block boundaries | -0.4 to -0.7 s only if hits | 2 h + restart | hits > 0, cached_tokens, pass^3 |
| R10 (cond.) | Student LoRA on Qwen2.5-Coder-0.5B/1.5B with pattern-first synthetic data, only with the vision service stopped | small fine-tuned models approach frontier Text2Cypher ([CYQUARK](https://arxiv.org/abs/2606.14325), averages unverified) | call-A prompt ~300 tokens | 3-5 d | A10 |

## 6. Ablation plan (pre-registered; K=3; paired; Correct_unseen primary, EX_strict secondary, RS_c at {0,1,10})

A0 reference rows: llm / **llm-pure** / auto / router-only / whole-graph LLM-only with per-mode counts. A1 prompt: pinned vs compact vs dyn-k3 vs compact+dyn vs compact+decontaminated-V2+dyn, bank hash stored. A2 schema_check x max_repairs {0,1,2} x zero-row retry. A3 compose: LLM / LLM+cap / row-only renderer / slot template + fluency. A4 Tier-2 off/on x logprob thresholds as coverage-vs-risk. A5 greedy vs best-of-4 x selection signal. A6 grammar off/typed x few-shots static/kNN with empties. A7 thinking off/on (AbstentionRecall, FalseRefusal; [AbstentionBench](https://arxiv.org/abs/2506.09038)). A8 prefix caching off/on x padded/unpadded. A9 negative rows: LLMLingua-2; ngram spec-decode greedy-equality. A10 student x data source x data size. Box time ~7 min per llm pass x K=3, so a 10-cell grid is ~3.5 h overnight.

## 7. Recoverability protocol

Every experiment = one env-flag configuration + `bench/results/<label>-<sha>-k<i>.json` (never overwritten, fingerprinted with dirty flag and prompt/bank hashes) + `scripts/snapshot_state.sh <tag>` (git tag + graph dump + **only that tag's** results + the few-shot bank, template library, router prompt and `vllm-llm.json`). `scripts/restore_state.sh <tag>` creates a branch from the tag, stashes uncommitted work and reloads the private graph; both refuse the shared 7688 instance. "Come back to now" = `scripts/restore_state.sh baseline-v1` (or `demo-v1`), verified by >= 90% majority-of-K agreement.

## 8. Resume framing

"RescueGrid-QA: a contamination-controlled, provenance- and time-first text-to-Cypher benchmark and evaluation protocol for an offline EOC assistant on a 30B-A3B hybrid Mamba/MoE (FP8, vLLM 0.26) on an NVIDIA GB10." Lead with the benchmark and the honest findings (a +6.7 pp bundle was +1 unseen question; static schema checking added 61% repair rounds without gains; the worst outliers were parser bugs), then the protocol (held-out tiers, multi-snapshot EX, K-repeat paired statistics quantifying FP8/serving nondeterminism ([Thinking Machines](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)), coverage-vs-risk curves), then the engineering (tiered answer layer with row-only rendering, framed as template NLG with a measured fluency/grounding trade-off) and the pre-registered ablations including negative results. The "cost model" is a design rationale, not a contribution.

## 9. Risks

Contamination/test-tuning (seen flag, unseen-only headline, held-out set never used for design). Demo-window overload (minimum cut D0-lite + D1 + D3; Tier-1 path untouched). Stale single-run baseline (D0 first). Co-tenant load (Tier-1 scripted, honest timeout, load recorded). Confident mis-routes (logprob gate, slot validation, no auto-promotion). Grounding over-claim (scored over answered-with-rows, identical inputs, unit-tested renderers, limited to checked value classes). Refusal-rewarding metric (Correct primary, RS_1 on Pareto, FalseRefusal floor). Weak non-inferiority at n=60 (margin + guards; claims only on held-out). Saturated abstention metric (explicit fields, ~20 unanswerables, errors never count). Unverified proxy cancellation (one test). Grammar empties, best-of-N cost, prefix-cache miss (ablations with revert gates). Memory 9-11 GB (recorded; specialist/student conditional on stopping vision). Schema drift (pinned snapshot; 7688 refused). Self-judged metrics (deterministic headline). Over-claiming (explicit prior art).

## 10. Changes after review

- **Contamination (fatal)**: verified 6 verbatim + 3 near-duplicate leaks in `EXAMPLES_V2` (and rt01/tm01/tm02 in V0); added a per-run `seen` flag from the rendered bank hash, `Correct_unseen` as the headline, decontamination of the V2 bank (R1), the 60 questions reclassified as a dev set, and a held-out ~100-question test set authored from the taxonomy, never from failing items.
- **Demo timeline (fatal)**: cut S0-S5 (16-18 h) to D0-D3 (~8 h, minimum ~5 h); Tier-2 router, flag sweep, structured schema errors and all novelty claims moved to the research week; no new gold except rows for rt01/rt04/pp03.
- **S1 hypothesis (major)**: replaced with the measured `8d1c86c` ladder (all-llm +1 unseen question, repairs +61%; l1/l2 0.717 with repairs 24/33); adoption now requires a -3 pp paired-bootstrap non-inferiority margin plus category/repairs/p95/FalseRefusal guards; the vacuous "not worse at p>0.05" rule is gone.
- **Grounded by construction (major)**: the claim is now a unit-tested property limited to the checked value classes; Grounded is scored over answered-with-rows only with GroundedCoverage alongside; engine and bench feed identical rows to `fact_check`; renderers print only row cells; the slot template gets a closed vocabulary and an out-of-slot token validator.
- **S2 deadline (major)**: replaced by the two parser/loop fixes (unterminated fence; `finish=length` handling), `max_retries=0` with a per-call timeout, an honest `abstain_reason=timeout` message and a proxy-cancellation check; the repair-budget question becomes ablation A2/R3.
- **RS_10 (major)**: demoted to a reported secondary at c in {0,1,10}; Correct_unseen is primary; RS_1 on the Pareto chart; FalseRefusal <= 5% floor.
- **Tier-2 router (major)**: post-demo; gated on the intent-token logprob plus deterministic slot validation and regex/BM25 agreement; miss-path latency measured; templates designed from the taxonomy on the dev split.
- **A0 double-counting (major)**: added `--mode llm-pure` (`allow_fallback=False`), gold rows for rt01/rt04/pp03, per-`mode_used` counts.
- **Resume framing (major)**: benchmark and honest findings first; slot templates cited as template NLG; "cost model" demoted to rationale.
- **Missing gold fields (major)**: HighlightAcc and ConsistencyRate deferred until the held-out set carries gold highlight ids and seed ids; role ledger lands before any counter changes.
- **Minor items adopted**: memory recorded in the fingerprint (9/11 GB today) and student/specialist models conditional on stopping vision; FalseRefusal gated and errors never count as abstentions; R9 states the Mamba-block hit condition; reproducibility redefined as majority-of-K agreement; snapshots carry only their own results and restore creates a branch; EX_strict accepts multiple gold shapes; Ozsoy's 77.6 -> 85.4 and 0 -> 24 -> 36 (not 1 -> 24) verified in the paper body; TeCoD cited without transferring its gain.

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
- Ozsoy, Extending Confidence-Based Text2Cypher with Grammar and Schema Aware Filtering: https://arxiv.org/html/2605.10318
- Adaptive test-time inference for Text2Cypher: https://arxiv.org/abs/2609.02324
- Query-and-Conquer execution-guided selection: https://arxiv.org/html/2503.24364
- Beam search vs self-consistency for grammar-constrained text-to-SQL (Chermsirivatana & MacCormick): https://arxiv.org/abs/2608.25761
- CYGNET/RAMPART validation gate: https://arxiv.org/abs/2606.04645
- CYQUARK grounded synthetic Text2Cypher data: https://arxiv.org/abs/2606.14325
- Schema-constrained grammar-guided GQL (Huawei, CEUR 2024): https://ceur-ws.org/Vol-4085/paper12.pdf
- PICARD: https://aclanthology.org/2021.emnlp-main.779/
- CypherBench: https://arxiv.org/html/2412.18702v1 ; scorer: https://github.com/megagonlabs/cypherbench/blob/main/cypherbench/metrics/execution_accuracy.py
- Test-suite accuracy (Zhong et al. 2020): https://aclanthology.org/2020.emnlp-main.29/
- TrustSQL reliability score: https://arxiv.org/html/2403.15879v6
- Selective classification (Geifman & El-Yaniv 2017): https://arxiv.org/abs/1705.08500
- Reiter & Dale, Building Natural Language Generation Systems (template-based NLG): https://doi.org/10.1017/CBO9780511519857
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
- AI Agents That Matter: https://arxiv.org/html/2407.01502 ; Cost-of-Pass: https://arxiv.org/abs/2504.13359
- tau-bench pass^k: https://arxiv.org/abs/2406.12045 ; Wilson interval: https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval
- AWS parameterized query templates: https://aws.amazon.com/blogs/architecture/reducing-text2sql-latency-with-parameterized-query-templates/
- Local: /opt/hp/zrt/run/vllm-llm.json, /opt/hp/zrt/run/vllm-llm.log, /home/hp2/kenil/rescuegrid-reasoning/bench/results/{baseline-llm-6d30e04,all-llm-8d1c86c,all-auto-8d1c86c,l1-entity-8d1c86c,l2-entity-schema-8d1c86c}.json, /home/hp2/kenil/rescuegrid-reasoning/rescuegrid/qa/{fewshot,schema_check,text2cypher,features}.py, /home/hp2/kenil/rescuegrid-reasoning/bench/{run_bench.py,ladder.sh}, /home/hp2/kenil/rescuegrid-reasoning/scripts/{snapshot_state,restore_state}.sh