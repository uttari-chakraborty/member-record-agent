"""A caseworker approves or rejects one of the agent's proposed changes.

Examples:
  python approve.py results/pending_<stamp>_<model>.json                       # list what is waiting
  python approve.py results/pending_<stamp>_<model>.json C21 G1 --approve --by cw-demo-01
  python approve.py results/pending_<stamp>_<model>.json C21 G1 --reject --by cw-demo-01 --reason "Employer says coverage runs to 2026-03-31"

The pending file comes from eval.py. Decisions update that file and are appended
to the run's audit log with the caseworker as the actor. Nothing in data/ changes.
"""
import argparse
import json
import sys

from agent.approvals import ApprovalError, approve, reject
from agent.audit import AuditLog


def _show(res):
    for g in res["proposals"]:
        print(f"{res['case_id']} run {res.get('run', 1)} {g['id']} [{g['status']}] {g['field']} -> {g['new_value']!r}")
        for c in g["changes"]:
            print(f"    {c['system']}: {c['old_value']!r}")
        for e in g["evidence"]:
            print(f"    evidence ({e.get('source')}): {e.get('claim')}")
        print(f"    confirm before approving: {g['confirm_before_approving']}")
        if g["status"] != "pending_approval":
            extra = f", reason: {g['rejection_reason']}" if g["rejection_reason"] else ""
            print(f"    decided by {g['decided_by']} at {g['decided_at']}{extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pending_file")
    ap.add_argument("case_id", nargs="?")
    ap.add_argument("group_id", nargs="?")
    ap.add_argument("--run", type=int, default=None, help="Which run, if the case was run more than once")
    decision = ap.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    ap.add_argument("--by", default="", help="Caseworker ID (synthetic in the prototype)")
    ap.add_argument("--reason", default="", help="Required with --reject")
    args = ap.parse_args()

    with open(args.pending_file, encoding="utf-8") as f:
        data = json.load(f)
    resolutions = data["resolutions"]

    if not args.case_id:
        for res in resolutions:
            _show(res)
        return
    matches = [r for r in resolutions if r["case_id"] == args.case_id
               and (args.run is None or r.get("run") == args.run)]
    if len(matches) != 1:
        sys.exit(f"Found {len(matches)} pending resolutions for {args.case_id}; use --run to pick one.")
    res = matches[0]
    if not args.group_id or not (args.approve or args.reject):
        _show(res)
        return

    audit = AuditLog(res["audit_path"])
    try:
        if args.approve:
            approve(res, args.group_id, args.by, audit)
        else:
            reject(res, args.group_id, args.by, args.reason, audit)
    except ApprovalError as e:
        sys.exit(f"Refused: {e}")
    with open(args.pending_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    _show(res)


if __name__ == "__main__":
    main()
