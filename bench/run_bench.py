#!/usr/bin/env python3
"""Benchmark the Q&A layer against bench/questions.json on the PRIVATE graph.

  NEO4J_URI=bolt://127.0.0.1:7687 .venv/bin/python bench/run_bench.py --label baseline --mode llm
  .venv/bin/python bench/run_bench.py --compare bench/results/baseline-*.json bench/results/exp1-*.json

Per question it records: cypher, rows, answer, every LLM call's prompt/completion tokens and wall time, warnings; and scores:
  exec_acc   - the system's rows match the gold query's rows on the question's key columns (set comparison)
  grounded   - fact-check found no source/time/confidence in the answer that is absent from the rows
  facts      - every must_mention string appears (case-insensitive) and no must_not_mention string appears
  abstain    - for unanswerable questions: the answer honestly says there is no record / it cannot answer (no invented facts)
Aggregates: accuracy rates, prompt/completion tokens per question, latency p50/p95, LLM calls per question, guard rejections, repairs.
Results go to bench/results/<label>-<git sha>.json (never overwritten)."""
import argparse, json, os, re, statistics, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:7687")
if "7688" in os.environ["NEO4J_URI"]:
    sys.exit("refusing to benchmark against the SHARED graph; set NEO4J_URI=bolt://127.0.0.1:7687")

from rescuegrid.config import Settings  # noqa: E402
from rescuegrid.graph import GraphStore, to_native  # noqa: E402
from rescuegrid.qa import QAEngine  # noqa: E402
from rescuegrid.qa.engine import fact_check  # noqa: E402
import rescuegrid.qa.llm_openai_compat as compat  # noqa: E402

CALLS: list[dict] = []


def instrument():
    """Wrap the OpenAI client so every call's tokens and latency are recorded."""
    orig_init = compat.OpenAICompatLLM.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        old = self.client.chat.completions.create

        def create(**kw):
            t = time.time()
            r = old(**kw)
            CALLS.append({"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens, "seconds": round(time.time() - t, 3),
                          "max_tokens": kw.get("max_tokens"), "json": bool(kw.get("response_format")), "finish": r.choices[0].finish_reason})
            return r
        self.client.chat.completions.create = create
    compat.OpenAICompatLLM.__init__ = init


def norm(v):
    v = to_native(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, list):
        return tuple(sorted(norm(x) for x in v)) if all(not isinstance(x, dict) for x in v) else tuple(sorted(json.dumps(norm(x), sort_keys=True) for x in v))
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    return v


def project(rows, keys):
    out = set()
    for r in rows:
        r = norm(r)
        vals = []
        for k in keys:
            v = r.get(k)
            vals.append(json.dumps(v, sort_keys=True, default=str))
        out.add(tuple(vals))
    return out


def _cells(rows):
    out = []
    def walk(v):
        if isinstance(v, dict):
            for x in v.values(): walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v: walk(x)
        else:
            out.append(v)
    walk([norm(r) for r in rows])
    return out


def score_exec(spec, sys_rows, gold_rows):
    cmp = spec.get("compare", {"key": ["id"]})
    if "value" in cmp:                       # a single number/string must appear somewhere in the system rows
        want = cmp["value"]
        return any((isinstance(c, (int, float)) and abs(float(c) - float(want)) < 1e-6) or str(c) == str(want) for c in _cells(sys_rows))
    if "contains_any" in cmp:                # at least one of these ids appears anywhere in the system rows
        cells = {str(c) for c in _cells(sys_rows)}
        return any(x in cells for x in cmp["contains_any"])
    if "rowcount" in cmp:
        return len(sys_rows) == len(gold_rows) and len(gold_rows) > 0
    if "key_any" in cmp:                     # system 'id' column must equal ANY of these gold columns
        sys_ids = project(sys_rows, ["id"])
        return any(sys_ids == project(gold_rows, [c]) for c in cmp["key_any"])
    keys = cmp.get("key", ["id"])
    if cmp.get("subset"):                    # system may return a superset of ids (e.g. extra explanatory rows)
        return project(gold_rows, keys) <= project(sys_rows, keys) and bool(gold_rows)
    return project(gold_rows, keys) == project(sys_rows, keys)


ABSTAIN_RX = re.compile(r"no (?:matching )?record|not (?:track|in the graph)|cannot (?:answer|evaluate|build|determine)|could not build|does not track|"
                        r"no (?:entity|unit|facility|building|such) (?:called|named|with|record)|read-only|never dispatches|no changes were made|"
                        r"no (?:data|information|records?) (?:on|about|for|available)|not available in the graph|graph (?:has|contains) no|do(?:es)? not (?:have|contain|include|provide) (?:any )?(?:data|record|information|forecast)|unknown to the graph|is not (?:tracked|recorded|provided|available|included)|not provided|no (?:forecast|weather|helicopter)", re.I)


def run(args):
    questions = json.load(open(ROOT / "bench" / args.questions))
    if args.only:
        questions = [q for q in questions if q["id"] in args.only or q.get("category") in args.only]
    instrument()
    g = GraphStore(Settings())
    engine = QAEngine(g)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "nogit"
    results = []
    for i, q in enumerate(questions, 1):
        gold_rows = []
        if q.get("gold_cypher"):
            try:
                gold_rows = [to_native(r) for r in g.read_dicts(q["gold_cypher"], now=engine.now())]
            except Exception as e:
                print(f"  !! gold cypher failed for {q['id']}: {e}")
        CALLS.clear()
        t0 = time.time()
        try:
            r = engine.answer(q["question"], mode=args.mode)
            err = None
        except Exception as e:
            r, err = None, f"{type(e).__name__}: {e}"
        wall = time.time() - t0
        rec = {"id": q["id"], "category": q.get("category"), "question": q["question"], "answerable": q.get("answerable", True), "mode_used": r.mode if r else "exception",
               "intent": r.intent if r else None, "cypher": r.cypher if r else None, "answer": r.answer if r else err, "confidence": r.confidence if r else 0.0,
               "highlight": r.highlight.model_dump() if r else None, "warnings": r.warnings if r else [err], "rows": (r.evidence[:50] if r else []),
               "gold_rows": gold_rows[:50], "calls": list(CALLS), "prompt_tokens": sum(c["prompt_tokens"] for c in CALLS),
               "completion_tokens": sum(c["completion_tokens"] for c in CALLS), "llm_seconds": round(sum(c["seconds"] for c in CALLS), 3), "wall_seconds": round(wall, 3)}
        ans = (r.answer if r else "") or ""
        if q.get("answerable", True):
            rec["exec_acc"] = score_exec(q, r.evidence if r else [], gold_rows) if q.get("gold_cypher") else None
            ground_rows = (r.evidence + [c.model_dump() for c in r.conflicts] + [p.model_dump() for p in r.provenance]) if r else []
            rec["grounded"] = (not fact_check(ans, ground_rows)) if r and ground_rows else (r is not None and r.mode != "error" and not r.evidence and rec["exec_acc"] is not True)
            low = ans.lower()
            mm = [m for m in q.get("must_mention", []) if not any(alt.lower() in low for alt in (m if isinstance(m, list) else [m]))]
            mn = [m for m in q.get("must_not_mention", []) if m.lower() in low]
            rec["facts"] = not mm and not mn; rec["missing"] = mm; rec["forbidden_hit"] = mn
            rec["correct"] = bool(rec["exec_acc"] in (True, None)) and rec["facts"] and (rec["grounded"] if r and r.evidence else True) and (r.mode != "error")
        else:
            rec["abstain"] = bool(ABSTAIN_RX.search(ans)) and not [m for m in q.get("must_not_mention", []) if m.lower() in ans.lower()]
            rec["correct"] = rec["abstain"]
        rec["guard_rejections"] = sum(1 for w in (r.warnings if r else []) if "read-only guard" in w)
        rec["repairs"] = max(0, len([c for c in CALLS if not c["json"]]) - 1)
        results.append(rec)
        flag = "OK " if rec["correct"] else "BAD"
        print(f"[{i:2d}/{len(questions)}] {flag} {rec['wall_seconds']:5.1f}s tok={rec['prompt_tokens']}+{rec['completion_tokens']} [{rec['mode_used']}] {q['id']}: {ans[:90]}", flush=True)
    summary = summarize(results)
    out = {"label": args.label, "git": sha, "mode": args.mode, "questions_file": args.questions, "graph": os.environ["NEO4J_URI"],
           "timestamp": datetime.now(timezone.utc).isoformat(), "summary": summary, "results": results}
    path = ROOT / "bench" / "results" / f"{args.label}-{sha}.json"
    if path.exists():
        path = ROOT / "bench" / "results" / f"{args.label}-{sha}-{int(time.time())}.json"
    json.dump(out, open(path, "w"), indent=1, default=str)
    print("\n" + table([out]))
    print(f"\nsaved {path}")


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0.0


def summarize(results):
    ans = [r for r in results if r["answerable"]]; un = [r for r in results if not r["answerable"]]
    llm = [r for r in results if r["calls"]]
    execd = [r for r in ans if r.get("exec_acc") is not None]
    s = {"n": len(results), "n_answerable": len(ans), "n_unanswerable": len(un),
         "correct_rate": round(sum(r["correct"] for r in results) / max(1, len(results)), 3),
         "exec_acc": round(sum(bool(r["exec_acc"]) for r in execd) / max(1, len(execd)), 3),
         "grounded_rate": round(sum(bool(r.get("grounded")) for r in ans) / max(1, len(ans)), 3),
         "facts_rate": round(sum(bool(r.get("facts")) for r in ans) / max(1, len(ans)), 3),
         "abstain_rate": round(sum(bool(r.get("abstain")) for r in un) / max(1, len(un)), 3) if un else None,
         "error_rate": round(sum(r["mode_used"] in ("error", "exception") for r in results) / max(1, len(results)), 3),
         "llm_questions": len(llm),
         "prompt_tokens_per_q": round(statistics.mean([r["prompt_tokens"] for r in results]), 1),
         "completion_tokens_per_q": round(statistics.mean([r["completion_tokens"] for r in results]), 1),
         "prompt_tokens_per_llm_q": round(statistics.mean([r["prompt_tokens"] for r in llm]), 1) if llm else 0,
         "completion_tokens_per_llm_q": round(statistics.mean([r["completion_tokens"] for r in llm]), 1) if llm else 0,
         "calls_per_llm_q": round(statistics.mean([len(r["calls"]) for r in llm]), 2) if llm else 0,
         "latency_p50_s": round(pct([r["wall_seconds"] for r in results], 0.5), 2), "latency_p95_s": round(pct([r["wall_seconds"] for r in results], 0.95), 2),
         "latency_llm_p50_s": round(pct([r["wall_seconds"] for r in llm], 0.5), 2) if llm else 0,
         "guard_rejections": sum(r["guard_rejections"] for r in results), "repairs": sum(r["repairs"] for r in results),
         "by_category": {}}
    for cat in sorted({r["category"] for r in results}):
        rs = [r for r in results if r["category"] == cat]
        s["by_category"][cat] = {"n": len(rs), "correct": round(sum(r["correct"] for r in rs) / len(rs), 2), "tokens": round(statistics.mean([r["prompt_tokens"] + r["completion_tokens"] for r in rs])), "p50_s": round(pct([r["wall_seconds"] for r in rs], 0.5), 2)}
    return s


COLS = [("correct_rate", "correct"), ("exec_acc", "exec_acc"), ("grounded_rate", "grounded"), ("abstain_rate", "abstain"), ("error_rate", "errors"),
        ("prompt_tokens_per_q", "prompt/q"), ("completion_tokens_per_q", "compl/q"), ("calls_per_llm_q", "calls/llmq"), ("latency_p50_s", "p50 s"), ("latency_p95_s", "p95 s"), ("repairs", "repairs")]


def table(runs):
    head = f"{'run':<28}" + "".join(f"{c[1]:>11}" for c in COLS)
    lines = [head, "-" * len(head)]
    for r in runs:
        s = r["summary"]
        lines.append(f"{(r['label'] + '@' + r['git'])[:28]:<28}" + "".join(f"{str(s.get(c[0])):>11}" for c in COLS))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--mode", choices=["auto", "llm", "fallback"], default="llm")
    ap.add_argument("--questions", default="questions.json")
    ap.add_argument("--only", nargs="*", help="question ids or categories")
    ap.add_argument("--compare", nargs="*", help="result files to tabulate instead of running")
    a = ap.parse_args()
    if a.compare:
        runs = [json.load(open(p)) for p in a.compare]
        print(table(runs))
        cats = sorted({c for r in runs for c in r["summary"]["by_category"]})
        print("\nper-category correct rate:")
        print(f"{'category':<16}" + "".join(f"{(r['label'])[:12]:>13}" for r in runs))
        for c in cats:
            print(f"{c:<16}" + "".join(f"{str(r['summary']['by_category'].get(c, {}).get('correct', '-')):>13}" for r in runs))
    else:
        run(a)
