# Sample output

Real output from the final run of all 36 main cases (Claude Sonnet, November 2026), so you can see results without running anything.

- `sample_report.txt`: the full report, scored against the bar set before the first run, including the retrieval check.
- `sample_decisions.json`: five individual decisions: no action (C06), an auto-correct (C01), an escalation with a safe fix first (C25), an escalation where a case note tries to override the rules (C20), and an escalation with a prepared fix for a caseworker to approve (C21).
- `approval_demo.txt`: a real prepared fix from the agent (C22), refused when the agent tries to approve it, then approved by a caseworker.

Every case is synthetic. New runs write to `results/`, which is kept out of the repo.
