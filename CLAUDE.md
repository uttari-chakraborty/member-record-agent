# Notes for Claude Code

This is a prototype Claude agent that investigates out-of-sync member records in a fictional public health program. See README.md for the product brief, design and results.

## Hard rules
- Never open, print, copy or search `.env`. It holds the API key. If a task seems to need the key, stop and ask.
- All data is synthetic. Never add real names, addresses, SSNs, member IDs, employers or client details.
- Don't change the agent's instructions (`SYSTEM_PROMPT` in `agent/runner.py`) to make a held-out case pass (`data/heldout/`). Those cases measure generalization.
- Safety limits live in code (`agent/tools.py`), not only in the prompt. Keep new limits there too.
- Don't run `git push`. Don't commit without asking first.
- Don't run `python eval.py` without `--mock` or `--retrieval-only` unless asked: full runs cost API credit.

## How to check a change
1. `python -m unittest discover tests` (no key needed; all tests must pass)
2. `python eval.py --mock` (plumbing check, no key needed)
3. Add a test in `tests/test_guardrails.py` for any new behavior.
