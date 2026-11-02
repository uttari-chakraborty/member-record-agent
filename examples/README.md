# Sample output

Real output from the final run of all 36 main cases (Claude Sonnet, November 2026), so you can see results without running anything.

- `sample_report.txt`: the full report, scored against the bar set before the first run, including the retrieval check.
- `sample_decisions.json`: four individual decisions, one of each kind: no action (C06), an auto-correct (C01), an escalation with a safe fix first (C25), and an escalation where a case note tries to override the rules (C20).

Every case is synthetic. New runs write to `results/`, which is kept out of the repo.
