"""The agent's tools. Role limits live HERE, in code, not in the prompt.

The safest way to stop an action is to not give the agent a tool for it:
there is no tool to end coverage, change eligibility, bill a member or merge
records. The one tool that changes data, propose_correction, only accepts
low-risk fields and only values that already exist in a record or a note.
"""
import copy
import json
import config
from agent import masking, rules
from agent.policy import PolicyIndex

POLICY_INDEX = PolicyIndex()
TOP_K = 5  # raised from 3 after Claude's real searches missed the right policy in 4 cases

TOOL_DEFINITIONS = [
    {
        "name": "search_policy",
        "description": (
            "Search the program's policy manual. Returns the top 5 sections in effect on as_of_date. "
            "Use the date the event happened (for example the date of the note that reports it), because policies change. "
            "Write the query with the details that make this case different: the field, the kind of event, and who reported it "
            "and through which channel (for example 'address change reported through the insurer portal', not just "
            "'address change'). Run one search per issue in the case."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you need the rule for, in plain words."},
                "as_of_date": {"type": "string", "description": "YYYY-MM-DD: the date the event happened."},
            },
            "required": ["query", "as_of_date"],
        },
    },
    {
        "name": "get_record",
        "description": "Get one system's record for this member. Identifiers are masked; restricted fields are not available to your role.",
        "input_schema": {
            "type": "object",
            "properties": {"system": {"type": "string", "enum": config.SYSTEMS}},
            "required": ["system"],
        },
    },
    {
        "name": "propose_correction",
        "description": (
            "Correct one low-risk field in one system: address (in-state only), first_name, middle_name, "
            "last_name, phone or email. The new value must already appear in another system's record or in a "
            "case note. Any other field is rejected and the case goes to a caseworker."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "system": {"type": "string", "enum": config.SYSTEMS},
                "field": {"type": "string"},
                "new_value": {"description": "String, or an address object with line1, city, state, zip."},
                "evidence": {"type": "string", "description": "Which record or note supports this, e.g. 'agency' or 'note:2'."},
            },
            "required": ["system", "field", "new_value", "evidence"],
        },
    },
    {
        "name": "escalate_to_caseworker",
        "description": "Send the case to a caseworker with a summary. Ends the case.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "2-4 sentences: what happened and what needs deciding."},
                "reasons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"},
                            "source": {"type": "string", "description": "agency, insurer, payment_vendor, identity_checks, note:N, or policy:X.Y"},
                        },
                        "required": ["claim", "source"],
                    },
                },
                "recommended_next_step": {"type": "string"},
                "urgent": {"type": "boolean", "description": "True if the member is in ongoing treatment and the issue could affect care or billing."},
            },
            "required": ["summary", "reasons", "recommended_next_step", "urgent"],
        },
    },
    {
        "name": "close_case",
        "description": "Close the case without escalating: either the records already agree (no_action) or your corrections resolved every mismatch (corrected).",
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["no_action", "corrected"]},
                "reason": {"type": "string"},
            },
            "required": ["outcome", "reason"],
        },
    },
]

VALID_SOURCES = set(config.SYSTEMS) | {"identity_checks"}


class CaseSession:
    """Holds one case's state while the agent works on it."""

    def __init__(self, case, audit):
        self.case = case
        self.audit = audit
        self.working = copy.deepcopy(case["systems"])
        self.mismatches = rules.find_mismatches(case)
        self.names_flagged = any(m["field"] in ("first_name", "middle_name", "last_name") for m in self.mismatches)
        self.corrections = []
        self.blocked = []
        self.escalation = None
        self.closed = None
        self.done = False
        self.policy_searches = []
        self.citation_sendbacks = 0

    # --- what the agent sees first ---------------------------------------
    def overview(self):
        return {
            "program": config.PROGRAM_NAME,
            "program_state": config.PROGRAM_STATE,
            "flagged_mismatches": self.mismatches,
            "identity_checks": rules.identity_checks(self.case),
            "ongoing_treatment": self.case.get("ongoing_treatment", False),
            "source_of_truth": config.SOURCE_OF_TRUTH,
            "fields_not_available_to_your_role": sorted(config.RESTRICTED_FIELDS),
            "case_notes": masking.masked_notes(self.case),
            **({"privacy_exception": self.case["dob_needed_for_decision"]} if self.case.get("dob_needed_for_decision") else {}),
        }

    # --- tool handlers ---------------------------------------------------
    def handle(self, name, args):
        handler = {
            "search_policy": self._search_policy,
            "get_record": self._get_record,
            "propose_correction": self._propose_correction,
            "escalate_to_caseworker": self._escalate,
            "close_case": self._close,
        }.get(name)
        if handler is None:
            return self._block("unknown_tool", {"tool": name, "args": args})
        return handler(args)

    def _search_policy(self, args):
        query, as_of = str(args.get("query", "")), str(args.get("as_of_date", ""))[:10]
        if len(as_of) != 10:
            return {"error": "as_of_date must be YYYY-MM-DD"}
        results = POLICY_INDEX.search(query, as_of, k=TOP_K)
        self.policy_searches.append({"query": query, "as_of": as_of, "results": [r["policy_id"] for r in results]})
        self.audit.write(self.case["case_id"], "agent", "searched_policy",
                         {"query": query, "as_of": as_of, "results": [f"{r['policy_id']} v{r['version']}" for r in results]})
        return {"results": results}

    def _get_record(self, args):
        system = args.get("system")
        if system not in config.SYSTEMS:
            return {"error": f"unknown system {system}"}
        rec = masking.mask_record(self.working[system], include_names=self.names_flagged)
        self.audit.write(self.case["case_id"], "agent", "read_record", {"system": system})
        return rec

    def _block(self, reason, detail):
        self.blocked.append({"reason": reason, **detail})
        self.audit.write(self.case["case_id"], "agent", "blocked", {"reason": reason, **detail})
        return {"result": "rejected", "reason": reason,
                "note": "This change is outside your role. The case will go to a caseworker."}

    def _value_has_evidence(self, field, value, target_system):
        for s in config.SYSTEMS:
            if s != target_system and self.case["systems"][s].get(field) == value:
                return True
        notes = " ".join(n["text"].lower() for n in self.case.get("notes", []))
        if isinstance(value, dict):
            line1 = str(value.get("line1", "")).lower()
            return bool(line1) and line1 in notes
        return isinstance(value, str) and value.strip().lower() in notes

    def _propose_correction(self, args):
        system, field, value = args.get("system"), args.get("field"), args.get("new_value")
        if isinstance(value, str) and field == "address":
            try:
                value = json.loads(value)
            except ValueError:
                pass
        detail = {"system": system, "field": field, "new_value": value, "evidence": args.get("evidence")}
        if system not in config.SYSTEMS:
            return self._block("unknown_system", detail)
        if field not in config.AUTO_CORRECT_FIELDS:
            return self._block("field_not_allowed", detail)
        if field == "address":
            if not isinstance(value, dict) or value.get("state") != config.PROGRAM_STATE:
                return self._block("address_out_of_state_or_invalid", detail)
        if field in ("first_name", "middle_name", "last_name"):
            ids = rules.identity_checks(self.case)
            if any(v != "all systems match" for k, v in ids.items() if k.endswith("_match")):
                return self._block("identity_not_confirmed", detail)
        if not self._value_has_evidence(field, value, system):
            return self._block("value_not_in_any_record_or_note", detail)
        self.working[system][field] = value
        self.corrections.append({"system": system, "field": field, "new_value": value})
        self.audit.write(self.case["case_id"], "agent", "corrected", detail)
        return {"result": "applied"}

    def _escalate(self, args):
        reasons = args.get("reasons") or []
        # Code check: every escalation must cite the policy it relies on. Sent back once per
        # attempt; if the agent never adds one, finalize() still escalates the case (fail-safe).
        if not any(str(r.get("source", "")).startswith("policy:") for r in reasons):
            self.citation_sendbacks += 1
            self.audit.write(self.case["case_id"], "system", "sent_back", {"reason": "missing_policy_citation"})
            return {"result": "not_accepted", "reason": "missing_policy_citation",
                    "note": "Add the policy section this decision relies on (source policy:X.Y), using search_policy if needed, then call escalate_to_caseworker again."}
        bad = [r for r in reasons if r.get("source") not in VALID_SOURCES
               and not str(r.get("source", "")).startswith(("note:", "policy:"))]
        self.escalation = {
            "summary": args.get("summary", ""),
            "reasons": reasons,
            "recommended_next_step": args.get("recommended_next_step", ""),
            "urgent_by_model": bool(args.get("urgent")),
            # Enforced in code: ongoing treatment always means urgent.
            "urgent": bool(args.get("urgent")) or bool(self.case.get("ongoing_treatment")),
            "uncited_reasons": len(bad),
            "by": "agent",
        }
        self.audit.write(self.case["case_id"], "agent", "escalated", self.escalation)
        self.done = True
        return {"result": "escalated"}

    def _close(self, args):
        self.closed = {"outcome": args.get("outcome"), "reason": args.get("reason", "")}
        self.audit.write(self.case["case_id"], "agent", "closed", self.closed)
        self.done = True
        return {"result": "closed"}

    # --- the final answer ------------------------------------------------
    def finalize(self, ran_out_of_turns=False):
        """Code decides the final action, with fail-safes that favour a human."""
        if self.escalation is None and (self.blocked or ran_out_of_turns):
            reason = "blocked action" if self.blocked else "agent did not finish"
            self.escalation = {
                "summary": f"Escalated by the system: {reason}.", "reasons": [],
                "recommended_next_step": "Review the case.", "urgent_by_model": False,
                "urgent": bool(self.case.get("ongoing_treatment")), "uncited_reasons": 0, "by": "system",
            }
            self.audit.write(self.case["case_id"], "system", "escalated", self.escalation)
        if self.escalation:
            action = "escalate"
        elif self.corrections:
            action = "auto_correct"
        else:
            action = "no_action"
        return {
            "action": action,
            "corrections": self.corrections,
            "blocked": self.blocked,
            "escalation": self.escalation,
            "closed": self.closed,
            "policy_searches": self.policy_searches,
            "citation_sendbacks": self.citation_sendbacks,
        }
