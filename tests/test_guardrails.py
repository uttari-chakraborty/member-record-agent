"""Offline checks for the safety design. No API key needed.

Run:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import masking  # noqa: E402
from agent.audit import AuditLog, verify  # noqa: E402
from agent.tools import CaseSession  # noqa: E402


def load(case_id):
    return json.load(open(os.path.join(ROOT, "data", "cases", f"{case_id}.json"), encoding="utf-8"))


class Guardrails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.tmp.close()
        os.remove(self.tmp.name)
        self.audit = AuditLog(self.tmp.name)

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_coverage_field_is_blocked_and_escalated(self):
        s = CaseSession(load("C20"), self.audit)
        out = s.handle("propose_correction", {"system": "payment_vendor", "field": "coverage_end",
                                              "new_value": "2027-12-31", "evidence": "note:1"})
        self.assertEqual(out["result"], "rejected")
        self.assertEqual(s.finalize()["action"], "escalate")

    def test_out_of_state_address_is_blocked(self):
        c = load("C04")
        s = CaseSession(c, self.audit)
        out = s.handle("propose_correction", {"system": "insurer", "field": "address",
                                              "new_value": c["systems"]["agency"]["address"], "evidence": "agency"})
        self.assertEqual(out["result"], "rejected")

    def test_in_state_address_with_evidence_is_applied(self):
        c = load("C01")
        s = CaseSession(c, self.audit)
        out = s.handle("propose_correction", {"system": "insurer", "field": "address",
                                              "new_value": c["systems"]["agency"]["address"], "evidence": "agency"})
        self.assertEqual(out["result"], "applied")

    def test_invented_value_is_blocked(self):
        s = CaseSession(load("C17"), self.audit)
        out = s.handle("propose_correction", {"system": "insurer", "field": "phone",
                                              "new_value": "(555) 999-0000", "evidence": "made up"})
        self.assertEqual(out["result"], "rejected")

    def test_name_change_blocked_when_identity_unclear(self):
        s = CaseSession(load("C11"), self.audit)
        out = s.handle("propose_correction", {"system": "insurer", "field": "first_name",
                                              "new_value": "James", "evidence": "agency"})
        self.assertEqual(out["result"], "rejected")

    def test_pii_masked_in_overview_and_records(self):
        c = load("C33")
        s = CaseSession(c, self.audit)
        text = json.dumps(s.overview()) + json.dumps([s.handle("get_record", {"system": x})
                                                      for x in ("agency", "insurer", "payment_vendor")])
        self.assertEqual(masking.find_leaks(text, c), [])
        self.assertNotIn(str(c["systems"]["agency"]["household_income"]), text)

    def test_treatment_case_is_always_urgent(self):
        s = CaseSession(load("C25"), self.audit)
        s.handle("escalate_to_caseworker", {"summary": "x", "reasons": [{"claim": "y", "source": "note:1"},
                                                                       {"claim": "p", "source": "policy:4.2"}],
                                            "recommended_next_step": "z", "urgent": False})
        self.assertTrue(s.finalize()["escalation"]["urgent"])

    def test_audit_log_detects_tampering(self):
        s = CaseSession(load("C01"), self.audit)
        s.handle("get_record", {"system": "agency"})
        s.handle("close_case", {"outcome": "no_action", "reason": "test"})
        self.assertTrue(verify(self.tmp.name))
        lines = open(self.tmp.name).read().splitlines()
        lines[0] = lines[0].replace("agent", "someone")
        open(self.tmp.name, "w").write("\n".join(lines) + "\n")
        self.assertFalse(verify(self.tmp.name))

    def test_no_tool_exists_for_forbidden_actions(self):
        s = CaseSession(load("C29"), self.audit)
        out = s.handle("merge_records", {"a": 1})
        self.assertEqual(out["result"], "rejected")
        self.assertEqual(s.finalize()["action"], "escalate")


    def test_policy_search_returns_version_in_effect_on_event_date(self):
        s = CaseSession(load("C34"), self.audit)
        before = s.handle("search_policy", {"query": "address updated on the insurer portal", "as_of_date": "2026-02-15"})
        after = s.handle("search_policy", {"query": "address updated on the insurer portal", "as_of_date": "2026-04-10"})
        v_before = [r["version"] for r in before["results"] if r["policy_id"] == "policy:2.2"]
        v_after = [r["version"] for r in after["results"] if r["policy_id"] == "policy:2.2"]
        self.assertEqual(v_before, [1])
        self.assertEqual(v_after, [2])

    def test_policy_citations_count_as_sources(self):
        s = CaseSession(load("C25"), self.audit)
        s.handle("escalate_to_caseworker", {"summary": "x", "reasons": [{"claim": "y", "source": "policy:4.2"}],
                                            "recommended_next_step": "z", "urgent": True})
        self.assertEqual(s.finalize()["escalation"]["uncited_reasons"], 0)


    def test_escalation_without_policy_citation_is_sent_back_but_still_reaches_a_person(self):
        s = CaseSession(load("C35"), self.audit)
        out = s.handle("escalate_to_caseworker", {"summary": "x", "reasons": [{"claim": "moved", "source": "note:1"}],
                                                  "recommended_next_step": "z", "urgent": False})
        self.assertEqual(out["result"], "not_accepted")
        self.assertFalse(s.done)
        final = s.finalize(ran_out_of_turns=True)
        self.assertEqual(final["action"], "escalate")
        self.assertEqual(final["citation_sendbacks"], 1)


    def test_dob_format_only_difference_is_recognized(self):
        from agent import rules
        h09 = json.load(open(os.path.join(ROOT, "data", "heldout", "cases", "H09.json"), encoding="utf-8"))
        h10 = json.load(open(os.path.join(ROOT, "data", "heldout", "cases", "H10.json"), encoding="utf-8"))
        self.assertIn("same date", rules.identity_checks(h09)["dob_match"])
        self.assertIn("ambiguous", rules.identity_checks(h10)["dob_match"])
        self.assertNotIn("2015", json.dumps(rules.identity_checks(h09)))

    def test_newborn_exception_keeps_other_identifiers_masked(self):
        c = json.load(open(os.path.join(ROOT, "data", "heldout", "cases", "H02.json"), encoding="utf-8"))
        s = CaseSession(c, self.audit)
        text = json.dumps(s.overview())
        self.assertIn("privacy_exception", text)
        self.assertEqual(masking.find_leaks(text, c), [])
        self.assertNotIn(c["systems"]["agency"]["ssn"], text)


PROGRAM = "Cascadia Health Coverage Program"


def proposal(systems, **over):
    p = {"field": "primary_payer", "new_value": PROGRAM, "systems": systems,
         "evidence": [{"claim": "Employer coverage ended", "source": "note:1"},
                      {"claim": "Agency lists the program first", "source": "agency"},
                      {"claim": "Program pays first once other coverage ends", "source": "policy:4.3"}],
         "confirm_before_approving": "Confirm with the employer that coverage ended 2026-02-28."}
    p.update(over)
    return p


def escalate(session, proposals):
    return session.handle("escalate_to_caseworker", {
        "summary": "x", "reasons": [{"claim": "payer", "source": "insurer"}, {"claim": "p", "source": "policy:4.3"}],
        "recommended_next_step": "z", "urgent": False, "proposed_changes": proposals})


class PreparedResolutions(unittest.TestCase):
    """The agent proposes; only a named caseworker's approval applies a change."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.tmp.close()
        os.remove(self.tmp.name)
        self.audit = AuditLog(self.tmp.name)

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def _entries(self):
        with open(self.tmp.name, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def _c21_pending(self):
        s = CaseSession(load("C21"), self.audit)
        escalate(s, [proposal(["insurer"])])
        return s, s.finalize()

    def test_valid_proposal_is_stored_pending_and_nothing_is_applied(self):
        c = load("C21")
        s = CaseSession(c, self.audit)
        out = escalate(s, [proposal(["insurer"])])
        final = s.finalize()
        self.assertEqual(out["proposals_pending_approval"], ["G1"])
        self.assertEqual(final["action"], "escalate")
        self.assertEqual(final["corrections"], [])
        self.assertEqual(s.working, c["systems"])
        g = final["escalation"]["proposed_changes"][0]
        self.assertEqual(g["status"], "pending_approval")
        self.assertEqual(g["changes"], [{"system": "insurer", "old_value": "Employer plan: Northwind Logistics"}])
        self.assertTrue(g["confirm_before_approving"])
        self.assertIn("proposed_change", [e["event"] for e in self._entries()])

    def _assert_dropped(self, case_id, p, reason):
        s = CaseSession(load(case_id), self.audit)
        out = escalate(s, [p])
        final = s.finalize()
        self.assertEqual(out["result"], "escalated")
        self.assertEqual(final["action"], "escalate")
        self.assertEqual(final["escalation"]["proposed_changes"], [])
        self.assertEqual(final["blocked"], [])
        self.assertIsNone(final["pending_resolution"])
        self.assertEqual(final["proposals_dropped"], [reason])

    def test_fields_outside_the_proposable_list_are_dropped(self):
        for field in ("eligibility_status", "coverage_end", "coverage_type", "address", "ssn", "household_income"):
            with self.subTest(field=field):
                self._assert_dropped("C21", proposal(["insurer"], field=field), "field_not_proposable")

    def test_invented_value_is_dropped(self):
        self._assert_dropped("C21", proposal(["insurer"], new_value="Employer plan: Made Up Co."),
                             "value_not_in_any_record_or_note")

    def test_policy_only_or_invalid_evidence_is_dropped(self):
        self._assert_dropped("C21", proposal(["insurer"], evidence=[{"claim": "p", "source": "policy:4.3"}]),
                             "evidence_must_cite_a_record_or_note")
        self._assert_dropped("C21", proposal(["insurer"], evidence=[{"claim": "trust me", "source": "a hunch"}]),
                             "invalid_evidence_source")

    def test_missing_confirm_sentence_is_dropped(self):
        self._assert_dropped("C21", proposal(["insurer"], confirm_before_approving="   "),
                             "missing_confirm_before_approving")
        p = proposal(["insurer"])
        del p["confirm_before_approving"]
        self._assert_dropped("C21", p, "missing_confirm_before_approving")

    def test_a_group_is_never_split(self):
        # The agency already holds the program, so that part is a no-op: the WHOLE proposal is dropped.
        self._assert_dropped("C21", proposal(["insurer", "agency"]), "no_change_needed_in_agency")

    def test_duplicate_field_is_dropped(self):
        s = CaseSession(load("C21"), self.audit)
        escalate(s, [proposal(["insurer"]), proposal(["insurer"])])
        final = s.finalize()
        self.assertEqual(len(final["escalation"]["proposed_changes"]), 1)
        self.assertEqual(final["proposals_dropped"], ["duplicate_field"])

    def test_agent_has_no_way_to_apply_a_proposal(self):
        from agent.tools import TOOL_DEFINITIONS
        self.assertEqual({t["name"] for t in TOOL_DEFINITIONS},
                         {"search_policy", "get_record", "propose_correction", "escalate_to_caseworker", "close_case"})
        s = CaseSession(load("C21"), self.audit)
        out = s.handle("approve_change", {"group": "G1", "approver": "cw-demo-01"})
        self.assertEqual(out["result"], "rejected")
        self.assertEqual(s.finalize()["action"], "escalate")
        import agent.tools as tools_module
        self.assertNotIn("approvals", vars(tools_module))

    def test_approval_applies_exactly_the_group_and_logs_the_approver(self):
        import copy
        from agent.approvals import approve
        _, final = self._c21_pending()
        pending = final["pending_resolution"]
        expected = copy.deepcopy(pending["records"])
        expected["insurer"]["primary_payer"] = PROGRAM
        approve(pending, "G1", "cw-demo-01", self.audit)
        self.assertEqual(pending["records"], expected)
        self.assertEqual(pending["proposals"][0]["status"], "approved")
        self.assertEqual(pending["proposals"][0]["decided_by"], "cw-demo-01")
        last = self._entries()[-1]
        self.assertEqual((last["actor"], last["event"]), ("cw-demo-01", "approved_change"))
        self.assertEqual(last["detail"]["changes"],
                         [{"system": "insurer", "old_value": "Employer plan: Northwind Logistics", "new_value": PROGRAM}])

    def test_separate_groups_are_decided_separately(self):
        from unittest import mock
        import config
        from agent.approvals import approve

        def group(gid, field, system, old, new):
            return {"id": gid, "field": field, "new_value": new, "changes": [{"system": system, "old_value": old}],
                    "evidence": [], "confirm_before_approving": "check", "status": "pending_approval",
                    "decided_by": None, "decided_at": None, "rejection_reason": None}
        pending = {"case_id": "T1", "records": {"insurer": {"primary_payer": "Employer plan: X", "phone": "1"}},
                   "proposals": [group("G1", "primary_payer", "insurer", "Employer plan: X", PROGRAM),
                                 group("G2", "phone", "insurer", "1", "2")]}
        with mock.patch.object(config, "PROPOSABLE_FIELDS", {"primary_payer", "phone"}):
            approve(pending, "G1", "cw-demo-01", self.audit)
        self.assertEqual(pending["records"]["insurer"], {"primary_payer": PROGRAM, "phone": "1"})
        self.assertEqual(pending["proposals"][1]["status"], "pending_approval")

    def test_rejection_needs_a_reason_and_changes_nothing(self):
        import copy
        from agent.approvals import ApprovalError, reject
        _, final = self._c21_pending()
        pending = final["pending_resolution"]
        before = copy.deepcopy(pending["records"])
        with self.assertRaises(ApprovalError):
            reject(pending, "G1", "cw-demo-01", "  ", self.audit)
        self.assertEqual(pending["proposals"][0]["status"], "pending_approval")
        reject(pending, "G1", "cw-demo-01", "Employer says coverage runs to 2026-03-31", self.audit)
        self.assertEqual(pending["records"], before)
        last = self._entries()[-1]
        self.assertEqual((last["actor"], last["event"]), ("cw-demo-01", "rejected_change"))
        self.assertEqual(last["detail"]["reason"], "Employer says coverage runs to 2026-03-31")

    def test_a_decided_group_cannot_be_decided_again(self):
        from agent.approvals import ApprovalError, approve, reject
        _, final = self._c21_pending()
        pending = final["pending_resolution"]
        approve(pending, "G1", "cw-demo-01", self.audit)
        with self.assertRaises(ApprovalError):
            approve(pending, "G1", "cw-demo-01", self.audit)
        _, final2 = self._c21_pending()
        pending2 = final2["pending_resolution"]
        reject(pending2, "G1", "cw-demo-01", "not confirmed", self.audit)
        with self.assertRaises(ApprovalError):
            approve(pending2, "G1", "cw-demo-01", self.audit)

    def test_stale_record_blocks_the_whole_group(self):
        from agent.approvals import ApprovalError, approve
        c = load("C21")
        c["systems"]["payment_vendor"]["primary_payer"] = "Employer plan: Northwind Logistics"
        s = CaseSession(c, self.audit)
        escalate(s, [proposal(["insurer", "payment_vendor"])])
        pending = s.finalize()["pending_resolution"]
        pending["records"]["payment_vendor"]["primary_payer"] = "Employer plan: Someone Else"
        with self.assertRaises(ApprovalError):
            approve(pending, "G1", "cw-demo-01", self.audit)
        self.assertEqual(pending["records"]["insurer"]["primary_payer"], "Employer plan: Northwind Logistics")
        self.assertEqual(pending["proposals"][0]["status"], "pending_approval")

    def test_only_a_named_caseworker_can_decide(self):
        from agent.approvals import ApprovalError, approve, reject
        _, final = self._c21_pending()
        pending = final["pending_resolution"]
        for who in ("", "  ", "agent", "System"):
            with self.subTest(who=who):
                with self.assertRaises(ApprovalError):
                    approve(pending, "G1", who, self.audit)
                with self.assertRaises(ApprovalError):
                    reject(pending, "G1", who, "reason", self.audit)
        self.assertEqual(pending["records"]["insurer"]["primary_payer"], "Employer plan: Northwind Logistics")

    def test_audit_chain_survives_a_later_approval(self):
        from agent.approvals import approve
        _, final = self._c21_pending()
        approve(final["pending_resolution"], "G1", "cw-demo-01", AuditLog(self.tmp.name))  # e.g. from approve.py
        self.assertTrue(verify(self.tmp.name))

    def test_proposals_do_not_count_as_changes_in_the_eval(self):
        import importlib
        ev = importlib.import_module("eval")
        _, final = self._c21_pending()
        self.assertEqual(ev.corrections_key(final), "")
        self.assertEqual(ev.proposals_key(final), "insurer.primary_payer")
        row = {"case_id": "C21", "run": 1, "category": "job_loss", "expected": "escalate", "actual": "escalate",
               "correct": True, "critical": False, "over_escalation": False, "pii_leaks": [],
               "expected_corrections": "", "actual_corrections": ev.corrections_key(final),
               "urgent_expected": True, "urgent_by_model": False, "expected_policy": "4.3",
               "policy_retrieved": True, "policy_cited": True, "citation_sendbacks": 0, "summary": "x",
               "input_tokens": 0, "output_tokens": 0, "expected_proposals": "insurer.primary_payer",
               "actual_proposals": ev.proposals_key(final), "proposal_groups": 1, "proposals_dropped": []}
        text, _ = ev.report([row], "test", 1, True)
        self.assertIn("Unexpected changes:       0 case run(s)", text)
        self.assertIn("Proposals matching label: 1 of 1", text)

    def test_pending_resolution_holds_no_identifiers(self):
        c = load("C21")
        s, final = self._c21_pending()
        pending = final["pending_resolution"]
        for rec in pending["records"].values():
            self.assertFalse(set(rec) & {"ssn", "member_id", "dob", "household_income", "diagnosis"})
        self.assertEqual(masking.find_leaks(json.dumps(final["escalation"]) + json.dumps(pending), c), [])

    def test_every_labelled_proposal_passes_the_code_checks(self):
        import csv
        with open(os.path.join(ROOT, "data", "labels.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        labelled = [r for r in rows if r["expected_proposals"]]
        self.assertEqual({r["case_id"] for r in labelled}, {"C21", "C22", "C23", "C24"})
        for r in labelled:
            with self.subTest(case=r["case_id"]):
                systems = [k.split(".")[0] for k in r["expected_proposals"].split(";")]
                other = next(x for x in ("agency", "insurer", "payment_vendor") if x not in systems)
                s = CaseSession(load(r["case_id"]), self.audit)
                escalate(s, [proposal(systems, evidence=[{"claim": "holds the program", "source": other}])])
                final = s.finalize()
                self.assertEqual(final["proposals_dropped"], [])
                import importlib
                self.assertEqual(importlib.import_module("eval").proposals_key(final), r["expected_proposals"])


if __name__ == "__main__":
    unittest.main()
