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


if __name__ == "__main__":
    unittest.main()
