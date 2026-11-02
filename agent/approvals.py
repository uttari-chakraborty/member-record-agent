"""Caseworker approval of the agent's proposed changes.

The agent can only propose (see CaseSession._check_proposals in agent/tools.py).
Nothing in the agent's tool path imports this module: a proposal changes a
record only when a named caseworker approves it here.

A proposal is one group: one field, every system that needs it. A group is
approved or rejected as a whole, never split. Approval applies exactly the
group's changes and nothing else; rejection needs a reason and changes nothing.
Both are written to the audit log with the caseworker as the actor.
"""
from datetime import datetime, timezone

from agent.tools import proposable

NOT_A_CASEWORKER = {"", "agent", "system"}


class ApprovalError(ValueError):
    """The decision was refused; nothing was changed or logged as a decision."""


def _group(pending, group_id):
    for g in pending.get("proposals", []):
        if g["id"] == group_id:
            return g
    raise ApprovalError(f"no proposal {group_id} in case {pending.get('case_id')}")


def _check_decision(pending, group_id, approver):
    approver = (approver or "").strip()
    if approver.lower() in NOT_A_CASEWORKER:
        raise ApprovalError("a named caseworker must decide; the agent and the system cannot")
    group = _group(pending, group_id)
    if group["status"] != "pending_approval":
        raise ApprovalError(f"{group_id} is already {group['status']}")
    return group, approver


def approve(pending, group_id, approver, audit):
    """Apply exactly one proposal group. All checks run before anything is written."""
    group, approver = _check_decision(pending, group_id, approver)
    field = group["field"]
    if not proposable(field):
        raise ApprovalError(f"{field} can no longer be changed through a proposal")
    records = pending["records"]
    stale = [c["system"] for c in group["changes"] if records[c["system"]].get(field) != c["old_value"]]
    if stale:
        raise ApprovalError(f"record changed since the proposal was made ({', '.join(stale)}); nothing applied")
    for c in group["changes"]:
        records[c["system"]][field] = group["new_value"]
    group.update(status="approved", decided_by=approver, decided_at=datetime.now(timezone.utc).isoformat())
    audit.write(pending["case_id"], approver, "approved_change", {
        "group": group_id, "field": field,
        "changes": [{"system": c["system"], "old_value": c["old_value"], "new_value": group["new_value"]}
                    for c in group["changes"]],
        "confirm_before_approving": group["confirm_before_approving"],
    })
    return group


def reject(pending, group_id, approver, reason, audit):
    """Reject one proposal group with a reason. Records are not touched."""
    group, approver = _check_decision(pending, group_id, approver)
    reason = (reason or "").strip()
    if not reason:
        raise ApprovalError("a rejection needs a reason")
    group.update(status="rejected", decided_by=approver, decided_at=datetime.now(timezone.utc).isoformat(),
                 rejection_reason=reason)
    audit.write(pending["case_id"], approver, "rejected_change",
                {"group": group_id, "field": group["field"],
                 "systems": [c["system"] for c in group["changes"]], "reason": reason})
    return group
