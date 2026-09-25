# Cloud vs local: RescueGrid Q&A

Identical pipeline (same prompts, templates, checks, questions); only the model endpoint changes. Local = Nemotron-3-Nano-30B-A3B-FP8
on the ZGX Nano (vLLM 0.26 via HP ZRT). Cloud = the same model family on Nebius Token Factory (precision as served by Nebius).
Both sides ran simultaneously against the same frozen graph. 'throttled' = WAN emulated in the client transport at
256 kbps + 2.6 s latency per request (the numbers the twin displays); it applies to the cloud side only, because local
inference never leaves the box. Emulated link time is reported separately from inference + real network time.

| Site | Link | Mode | Runs | Correct (mean +/- SE) | Unseen | pass^K | Exec | Grounded | Abstain | Errors | Prompt tok/q | Compl tok/q | p50 s | p95 s | LLM-q p50 s | Emulated link s/q | $ per 1k q |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cloud | none | auto | 3 | 0.467 +/- 0.015 | 0.467 | 0.380 | 0.483 | 0.867 | 0.633 | 0.120 | 1859 | 166 | 1.64 | 3.11 | 1.79 | 0.0 | 0.151 |
| cloud | none | llm | 3 | 0.463 +/- 0.003 | 0.463 | 0.380 | 0.541 | 0.863 | 0.617 | 0.117 | 2004 | 176 | 1.98 | 3.68 | 2.04 | 0.0 | 0.162 |
| cloud | throttled | auto | 3 | 0.467 +/- 0.003 | 0.467 | 0.400 | 0.483 | 0.859 | 0.683 | 0.127 | 1868 | 164 | 7.37 | 11.31 | 7.55 | 4.6 | 0.151 |
| cloud | throttled | llm | 3 | 0.483 +/- 0.009 | 0.483 | 0.380 | 0.520 | 0.846 | 0.700 | 0.143 | 2033 | 180 | 7.51 | 11.55 | 7.56 | 5.04 | 0.165 |
| local | none | auto | 6 | 0.468 +/- 0.002 | 0.468 | 0.460 | 0.487 | 0.875 | 0.650 | 0.118 | 1919 | 171 | 3.94 | 8.08 | 4.31 | 0.0 | 0 (on-device) |
| local | none | llm | 6 | 0.452 +/- 0.002 | 0.452 | 0.450 | 0.512 | 0.877 | 0.658 | 0.120 | 2113 | 190 | 4.26 | 8.49 | 4.38 | 0.0 | 0 (on-device) |

## Per-question agreement (majority over repeats)

- mode=auto, link=none: both correct 45, local only 2, cloud only 5, neither 48; local-only ids ['hna03', 'hun03']; cloud-only ids ['hna19', 'hpv02', 'hpv09', 'hst04', 'hst07']
- mode=llm, link=none: both correct 42, local only 3, cloud only 4, neither 51; local-only ids ['hna03', 'hpx07', 'hun03']; cloud-only ids ['hpv02', 'hpv09', 'hst02', 'hst04']

Source files: bench/results/cloud_vs_local/*.json (one per run, with git sha, endpoint, link profile, flags and memory).
