"""Run the agent on every test case and score it against the labels.

Examples:
  python eval.py --mock                       # test the plumbing, no API key needed
  python eval.py                              # default model, 1 run
  python eval.py --runs 3                     # consistency: each case 3 times
  python eval.py --models claude-haiku-5-5,claude-opus-5-5 --runs 3   # model comparison
  python eval.py --cases C25,C20              # just a few cases
  python eval.py --retrieval-only             # score the policy search alone, no API key needed
  python eval.py --set heldout                # run the held-out cases instead
"""
import argparse
import csv
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import config
from agent import masking
from agent.audit import AuditLog, verify
from agent.runner import run_case

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def load_cases(only=None, case_set="main"):
    base = os.path.join(ROOT, "data") if case_set == "main" else os.path.join(ROOT, "data", "heldout")
    cases = []
    for p in sorted(glob.glob(os.path.join(base, "cases", "*.json"))):
        c = json.load(open(p, encoding="utf-8"))
        if not only or c["case_id"] in only:
            cases.append(c)
    labels = {r["case_id"]: r for r in csv.DictReader(open(os.path.join(base, "labels.csv"), encoding="utf-8"))}
    return cases, labels


def _policy_retrieved(result, expected):
    """expected may list acceptable sections separated by |, e.g. '4.2|4.3'."""
    if not expected:
        return None
    ok = [f"policy:{e}" for e in expected.split("|")]
    return any(o in s["results"] for s in result.get("policy_searches", []) for o in ok)


def _policy_cited(esc, expected):
    if not expected or not esc or esc.get("by") != "agent":
        return None
    ok = tuple(f"policy:{e}" for e in expected.split("|"))
    return any(str(r.get("source", "")).startswith(ok) for r in esc.get("reasons", []))


def retrieval_report():
    """Score the search on its own, separately from the agent's decisions."""
    from agent.policy import PolicyIndex
    idx = PolicyIndex()
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "retrieval_queries.csv"), encoding="utf-8")))
    top1 = top3 = top5 = 0
    misses = []
    for r in rows:
        res5 = [(x["policy_id"], x["version"]) for x in idx.search(r["query"], r["as_of"], 5)]
        res = res5[:3]
        exp = (f"policy:{r['expected_policy']}", int(r["expected_version"]))
        top5 += exp in res5
        top3 += exp in res
        top1 += bool(res) and res[0] == exp
        if not res or res[0] != exp:
            misses.append(f"  '{r['query']}' ({r['as_of']}): expected {exp[0]} v{exp[1]}, got {[f'{a} v{b}' for a, b in res]}")
    lines = ["\n=== Retrieval (search only, no Claude) ===",
             f"Right policy ranked first:   {top1} of {len(rows)}",
             f"Right policy in top 3:       {top3} of {len(rows)}  (recall at 3)",
             f"Right policy in top 5:       {top5} of {len(rows)}  (recall at 5, what the agent sees)"]
    if misses:
        lines += ["Not ranked first:"] + misses
    return "\n".join(lines)


def corrections_key(result):
    return ";".join(sorted(f"{c['system']}.{c['field']}" for c in result["corrections"]))


def score_model(model, cases, labels, runs, client, mock, outdir, stamp):
    audit_path = os.path.join(outdir, f"audit_{stamp}_{model}.jsonl")
    audit = AuditLog(audit_path)
    rows = []
    for case in cases:
        for r in range(runs):
            result, sent_text, usage = run_case(case, audit, model=model, client=client, mock=mock)
            lab = labels[case["case_id"]]
            leaks = masking.find_leaks(sent_text, case)
            esc = result["escalation"] or {}
            rows.append({
                "model": model, "run": r + 1, "case_id": case["case_id"], "category": lab["category"],
                "expected": lab["expected_action"], "actual": result["action"],
                "correct": result["action"] == lab["expected_action"],
                "critical": lab["expected_action"] == "escalate" and result["action"] != "escalate",
                "over_escalation": lab["expected_action"] != "escalate" and result["action"] == "escalate",
                "expected_corrections": lab["expected_corrections"], "actual_corrections": corrections_key(result),
                "urgent_expected": lab["expected_urgent"] == "yes",
                "urgent_by_model": esc.get("urgent_by_model", False),
                "escalated_by": esc.get("by", ""),
                "uncited_reasons": esc.get("uncited_reasons", 0),
                "blocked": [b["reason"] for b in result["blocked"]],
                "pii_leaks": leaks,
                "expected_policy": lab.get("expected_policy", ""),
                "policy_retrieved": _policy_retrieved(result, lab.get("expected_policy", "")),
                "policy_cited": _policy_cited(esc, lab.get("expected_policy", "")),
                "policy_searches": len(result.get("policy_searches", [])),
                "citation_sendbacks": result.get("citation_sendbacks", 0),
                "summary": esc.get("summary", (result["closed"] or {}).get("reason", "")),
                "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
            })
            mark = "ok " if rows[-1]["correct"] else "MISS"
            print(f"  [{mark}] {model} run {r + 1} {case['case_id']}: expected {lab['expected_action']}, got {result['action']}")
    return rows, audit_path


def report(rows, model, runs, audit_ok):
    n = len(rows)
    acc = sum(r["correct"] for r in rows) / n
    crit = sum(r["critical"] for r in rows)
    over = sum(r["over_escalation"] for r in rows)
    leaks = sum(1 for r in rows if r["pii_leaks"])
    esc_rate = sum(r["actual"] == "escalate" for r in rows) / n
    corr_rows = [r for r in rows if r["expected"] == "auto_correct" and r["actual"] == "auto_correct"]
    corr_exact = sum(r["actual_corrections"] == r["expected_corrections"] for r in corr_rows)
    fix_esc_rows = [r for r in rows if r["expected"] == "escalate" and r["expected_corrections"] and r["actual"] == "escalate"]
    fix_esc_exact = sum(r["actual_corrections"] == r["expected_corrections"] for r in fix_esc_rows)
    # Any field changed that the label didn't expect, in ANY case (including escalated ones).
    # Added after H05: the agent escalated correctly but first changed a name mid identity review,
    # and the old report only checked changes in auto-correct cases.
    def _extra(r):
        exp = set(filter(None, r["expected_corrections"].split(";")))
        got = set(filter(None, r["actual_corrections"].split(";")))
        return sorted(got - exp)
    unexpected_rows = [r for r in rows if _extra(r)]
    urgent_rows = [r for r in rows if r["urgent_expected"] and r["actual"] == "escalate"]
    urgent_model = sum(r["urgent_by_model"] for r in urgent_rows)
    by_case = defaultdict(set)
    for r in rows:
        by_case[r["case_id"]].add(r["actual"])
    inconsistent = sorted(c for c, acts in by_case.items() if len(acts) > 1)
    tokens_in = sum(r["input_tokens"] for r in rows)
    tokens_out = sum(r["output_tokens"] for r in rows)
    price = config.PRICES_PER_MILLION.get(model)
    cost = (tokens_in * price[0] + tokens_out * price[1]) / 1e6 if price else None

    pol_rows = [r for r in rows if r["expected_policy"] and r["policy_retrieved"] is not None]
    pol_found = sum(bool(r["policy_retrieved"]) for r in pol_rows)
    cite_rows = [r for r in rows if r["policy_cited"] is not None and r["expected"] == "escalate"]
    cited = sum(bool(r["policy_cited"]) for r in cite_rows)

    per_runs = runs if runs else 1
    bar = config.BAR
    lines = [
        f"\n=== {model}: {len(by_case)} cases x {per_runs} run(s) ===",
        f"Action accuracy:          {acc:.0%}   (bar: at least {bar['action_accuracy_min']:.0%})  {'PASS' if acc >= bar['action_accuracy_min'] else 'FAIL'}",
        f"Critical errors:          {crit}      (bar: {bar['critical_errors_max']})  {'PASS' if crit <= bar['critical_errors_max'] else 'FAIL'}",
        f"Over-escalations:         {over}      (bar: at most {bar['over_escalations_max'] * per_runs} across all runs)  {'PASS' if over <= bar['over_escalations_max'] * per_runs else 'FAIL'}",
        f"PII leaks:                {leaks}      (bar: {bar['pii_leaks_max']})  {'PASS' if leaks <= bar['pii_leaks_max'] else 'FAIL'}",
        f"Escalation rate:          {esc_rate:.0%}",
        f"Exact corrections:        {corr_exact} of {len(corr_rows)} correct auto-corrections changed exactly the expected fields",
        f"Safe fix before escalating: {fix_esc_exact} of {len(fix_esc_rows)} escalated cases that also needed a safe fix got exactly that fix",
        f"Unexpected changes:       {len(unexpected_rows)} case run(s) changed a field the label didn't expect (any action, including escalations)",
        f"Urgent flagged by Claude: {urgent_model} of {len(urgent_rows)} (code makes every treatment case urgent regardless)",
        f"Policy found by agent:    {pol_found} of {len(pol_rows)} cases searched up the expected policy section",
        f"Policy cited:             {cited} of {len(cite_rows)} escalations cited the expected policy section",
        f"Sent back for citation:   {sum(r['citation_sendbacks'] for r in rows)} time(s) in {sum(1 for r in rows if r['citation_sendbacks'])} case(s)",
        f"Inconsistent cases:       {', '.join(inconsistent) if inconsistent else 'none'}",
        f"Audit log intact:         {'yes' if audit_ok else 'NO'}",
        f"Tokens:                   {tokens_in:,} in / {tokens_out:,} out" + (f"  (~${cost:.2f}, ${cost / len(rows):.4f} per case)" if cost is not None else ""),
    ]
    by_action = Counter((r["expected"], r["actual"]) for r in rows)
    lines.append("Expected -> actual: " + ", ".join(f"{e}->{a}: {k}" for (e, a), k in sorted(by_action.items())))
    misses = [r for r in rows if not r["correct"]]
    if misses:
        lines.append("Misses:")
        for r in misses:
            lines.append(f"  {r['case_id']} run {r['run']} ({r['category']}): expected {r['expected']}, got {r['actual']}. {r['summary'][:160]}")
    wrong_fix = [r for r in rows if r["correct"] and r["expected_corrections"] and r["actual_corrections"] != r["expected_corrections"]]
    if wrong_fix:
        lines.append("Right action, different fields corrected:")
        for r in wrong_fix:
            lines.append(f"  {r['case_id']} run {r['run']}: expected {r['expected_corrections']}, got {r['actual_corrections'] or 'none'}")
    if unexpected_rows:
        lines.append("Unexpected changes (check these by hand):")
        for r in unexpected_rows:
            lines.append(f"  {r['case_id']} run {r['run']} ({r['category']}, {r['actual']}): changed {', '.join(_extra(r))}")
    return "\n".join(lines), {"model": model, "accuracy": acc, "critical": crit, "over": over,
                              "consistent": len(by_case) - len(inconsistent), "cases": len(by_case),
                              "cost_per_case": (cost / len(rows)) if cost is not None else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=config.DEFAULT_MODEL)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--cases", default="")
    ap.add_argument("--mock", action="store_true", help="Use a crude offline stand-in instead of Claude")
    ap.add_argument("--retrieval-only", action="store_true", help="Score the policy search alone; no API key needed")
    ap.add_argument("--set", default="main", choices=["main", "heldout"], help="Which test set to run")
    args = ap.parse_args()

    retrieval_text = retrieval_report()
    print(retrieval_text)
    if args.retrieval_only:
        return

    load_env()
    client = None
    if not args.mock:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("No API key found. Put ANTHROPIC_API_KEY=... in a file named .env (see README).")
        import anthropic
        client = anthropic.Anthropic()

    cases, labels = load_cases(set(args.cases.split(",")) if args.cases else None, args.set)
    outdir = os.path.join(ROOT, "results")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    models = ["mock"] if args.mock else [m.strip() for m in args.models.split(",")]

    all_rows, summaries, texts = [], [], [retrieval_text]
    for model in models:
        rows, audit_path = score_model(model, cases, labels, args.runs, client, args.mock, outdir, stamp)
        text, summ = report(rows, model, args.runs, verify(audit_path))
        print(text)
        texts.append(text)
        summaries.append(summ)
        all_rows += rows

    if len(models) > 1:
        cmp = ["\n=== Model comparison ===", "| Model | Action accuracy | Critical errors | Same answer on all runs | Cost per case |", "| --- | --- | --- | --- | --- |"]
        for s in summaries:
            c = f"${s['cost_per_case']:.4f}" if s["cost_per_case"] is not None else "set prices in config.py"
            cmp.append(f"| {s['model']} | {s['accuracy']:.0%} | {s['critical']} | {s['consistent']} of {s['cases']} | {c} |")
        print("\n".join(cmp))
        texts.append("\n".join(cmp))

    with open(os.path.join(outdir, f"results_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(all_rows, f, indent=2, default=str)
    with open(os.path.join(outdir, f"report_{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(texts))
    print(f"\nSaved results/results_{stamp}.json and results/report_{stamp}.txt")


if __name__ == "__main__":
    main()
