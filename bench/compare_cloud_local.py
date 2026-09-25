#!/usr/bin/env python3
"""Aggregate bench/results/cloud_vs_local/*.json into docs/CLOUD_VS_LOCAL.md and summary.json.
Groups runs by (site, link profile, mode); reports mean +/- SE over repeats, pass^K, tokens, latency split into
inference+real network vs emulated link, per-question agreement between local and cloud, and estimated cloud cost."""
import glob, json, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
d = (Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "bench" / "results" / "cloud_vs_local").resolve()
PRICE_IN, PRICE_OUT = 0.06, 0.24   # USD per 1M tokens, Nebius Token Factory list price for Nemotron-3-Nano-30B-A3B (Sep 2026; check before quoting)

runs = []
for f in sorted(glob.glob(str(d / "*.json"))):
    j = json.load(open(f))
    if "results" in j and "fingerprint" in j:
        runs.append(j)
groups = defaultdict(list)
for r in runs:
    fp = r["fingerprint"]
    groups[(fp.get("site", "?"), (fp.get("net") or {}).get("profile", "none"), r["mode"])].append(r)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0.0


def agg(rs):
    rates = [r["summary"]["correct_rate"] for r in rs]
    unseen = [r["summary"]["correct_unseen"] for r in rs]
    allq = [q for r in rs for q in r["results"]]
    llmq = [q for q in allq if q["calls"]]
    ids = sorted({q["id"] for q in allq})
    per = {i: [q["correct"] for q in allq if q["id"] == i] for i in ids}
    return {
        "runs": len(rs), "n": len(rs[0]["results"]),
        "correct_mean": round(statistics.mean(rates), 3), "correct_se": round(statistics.stdev(rates) / len(rates) ** 0.5, 3) if len(rates) > 1 else 0.0,
        "correct_unseen": round(statistics.mean(unseen), 3), "pass_k": round(sum(all(v) for v in per.values()) / len(ids), 3),
        "exec_acc": round(statistics.mean(r["summary"]["exec_acc"] for r in rs), 3), "grounded": round(statistics.mean(r["summary"]["grounded_rate"] for r in rs), 3),
        "abstain": round(statistics.mean(r["summary"]["abstain_rate"] or 0 for r in rs), 3), "errors": round(statistics.mean(r["summary"]["error_rate"] for r in rs), 3),
        "prompt_tok_q": round(statistics.mean(q["prompt_tokens"] for q in allq)), "compl_tok_q": round(statistics.mean(q["completion_tokens"] for q in allq)),
        "p50_s": round(pct([q["wall_seconds"] for q in allq], .5), 2), "p95_s": round(pct([q["wall_seconds"] for q in allq], .95), 2),
        "llm_p50_s": round(pct([q["wall_seconds"] for q in llmq], .5), 2) if llmq else 0,
        "emulated_link_s_q": round(statistics.mean(sum(c.get("emulated_link_seconds", 0) for c in q["calls"]) for q in allq), 2),
        "llm_share": round(len(llmq) / len(allq), 2),
        "tokens_total_per_run": round(sum(q["prompt_tokens"] + q["completion_tokens"] for q in allq) / len(rs)),
        "cost_usd_per_1k_q": round((statistics.mean(q["prompt_tokens"] for q in allq) * PRICE_IN + statistics.mean(q["completion_tokens"] for q in allq) * PRICE_OUT) / 1000, 4),
        "endpoint": rs[0]["fingerprint"].get("llm_endpoint"), "models": rs[0]["fingerprint"].get("llm_models_served"), "net": rs[0]["fingerprint"].get("net"),
        "per_question": {i: sum(v) / len(v) for i, v in per.items()},
    }


summary = {f"{s}|{p}|{m}": agg(rs) for (s, p, m), rs in sorted(groups.items())}
json.dump({k: {kk: vv for kk, vv in v.items() if kk != "per_question"} for k, v in summary.items()}, open(d / "summary.json", "w"), indent=1)

lines = ["# Cloud vs local: RescueGrid Q&A", "",
         "Identical pipeline (same prompts, templates, checks, questions); only the model endpoint changes. Local = Nemotron-3-Nano-30B-A3B-FP8",
         "on the ZGX Nano (vLLM 0.26 via HP ZRT). Cloud = the same model family on Nebius Token Factory (precision as served by Nebius).",
         "Both sides ran simultaneously against the same frozen graph. 'throttled' = WAN emulated in the client transport at",
         "256 kbps + 2.6 s latency per request (the numbers the twin displays); it applies to the cloud side only, because local",
         "inference never leaves the box. Emulated link time is reported separately from inference + real network time.", "",
         "| Site | Link | Mode | Runs | Correct (mean +/- SE) | Unseen | pass^K | Exec | Grounded | Abstain | Errors | Prompt tok/q | Compl tok/q | p50 s | p95 s | LLM-q p50 s | Emulated link s/q | $ per 1k q |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for k, v in summary.items():
    s, p, m = k.split("|")
    cost = f"{v['cost_usd_per_1k_q']:.3f}" if s == "cloud" else "0 (on-device)"
    lines.append(f"| {s} | {p} | {m} | {v['runs']} | {v['correct_mean']:.3f} +/- {v['correct_se']:.3f} | {v['correct_unseen']:.3f} | {v['pass_k']:.3f} | {v['exec_acc']:.3f} | {v['grounded']:.3f} | {v['abstain']:.3f} | {v['errors']:.3f} | {v['prompt_tok_q']} | {v['compl_tok_q']} | {v['p50_s']} | {v['p95_s']} | {v['llm_p50_s']} | {v['emulated_link_s_q']} | {cost} |")
lines += ["", "## Per-question agreement (majority over repeats)", ""]
for mode in sorted({k.split("|")[2] for k in summary}):
    for prof in ("none", "throttled"):
        lk, ck = f"local|{prof}|{mode}", f"cloud|{prof}|{mode}"
        if lk in summary and ck in summary:
            L, C = summary[lk]["per_question"], summary[ck]["per_question"]
            both = sum(1 for i in L if L[i] >= .5 and C.get(i, 0) >= .5); lo = sum(1 for i in L if L[i] >= .5 and C.get(i, 0) < .5)
            co = sum(1 for i in L if L[i] < .5 and C.get(i, 0) >= .5); nei = sum(1 for i in L if L[i] < .5 and C.get(i, 0) < .5)
            lines.append(f"- mode={mode}, link={prof}: both correct {both}, local only {lo}, cloud only {co}, neither {nei}"
                         + (f"; local-only ids {sorted(i for i in L if L[i] >= .5 and C.get(i, 0) < .5)[:12]}" if lo else "")
                         + (f"; cloud-only ids {sorted(i for i in L if L[i] < .5 and C.get(i, 0) >= .5)[:12]}" if co else ""))
lines += ["", f"Source files: {d.relative_to(ROOT)}/*.json (one per run, with git sha, endpoint, link profile, flags and memory).", ""]
out = ROOT / "docs" / "CLOUD_VS_LOCAL.md"
out.write_text("\n".join(lines))
print("\n".join(lines[8:8 + len(summary)]))
print(f"\nwrote {out} and {d / 'summary.json'}")
