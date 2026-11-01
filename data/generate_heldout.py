"""Builds the 10 held-out test cases, written from Uttari's descriptions.

Held out means the agent's instructions were NOT changed for these cases.
Everything is invented; SSNs use the never-issued 9xx range.

Run:  python data/generate_heldout.py
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_cases import addr, base, note  # same record builder as the main set

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heldout")
CASES, LABELS = [], []


def add(case_id, short, systems, notes, action, why, policy="", urgent=False, corrections="", treatment=False):
    CASES.append({"case_id": case_id, "category": "heldout", "systems": systems, "notes": notes,
                  "ongoing_treatment": treatment})
    if short == "Newborn":
        CASES[-1]["dob_needed_for_decision"] = "Birthdate shown: newborn coverage starts on the date of birth."
    LABELS.append({"case_id": case_id, "category": short, "expected_action": action,
                   "expected_urgent": "yes" if urgent else "no", "expected_corrections": corrections,
                   "why": why, "expected_policy": policy})


# H01 COBRA after divorce (harder version)
s = base(41, "Maya", "Ortiz", "1977-02-09")
s["insurer"]["coverage_type"] = "dependent"
s["insurer"]["primary_payer"] = "Employer plan: Northgate Freight (former spouse, continued through COBRA)"
add("H01", "COBRA after divorce", s,
    [note("2026-03-18", "agency call center", "Member reports divorce finalized in March."),
     note("2026-04-02", "insurer", "Member elected COBRA continuation on former spouse's employer plan.")],
    "escalate", "COBRA keeps her ex-spouse's plan valid, so the insurer is right and the others are stale; which plan pays first needs a caseworker.",
    policy="4.2|4.3", urgent=True, treatment=True)

# H02 Newborn not enrolled from birth (new situation)
s = base(42, "Noah", "Ferris", "2026-01-01")
s["agency"]["coverage_start"] = "2026-01-01"
for sysname in ("insurer", "payment_vendor"):
    s[sysname]["coverage_start"] = "2026-01-15"
s["payment_vendor"]["payment_history"] = "Clinic visit on 2026-01-05 denied as not covered; family billed out of pocket"
add("H02", "Newborn", s,
    [note("2026-01-15", "agency call center", "Mother added her newborn, born 2026-01-01, to her case."),
     note("2026-01-28", "agency call center", "Mother called about a bill for the baby's visit on 2026-01-05; she was told newborns are covered from birth for 30 days.")],
    "escalate", "The newborn is covered from birth under the 30-day rule, so the bill was wrong; fixing coverage dates and reprocessing the claim needs a caseworker.",
    policy="3.2")

# H03 Temporary out-of-state move (harder version)
s = base(43, "Grace", "Holloway", "1969-06-23")
s["agency"]["coverage_end"] = "2026-12-31"
for sysname in ("insurer", "payment_vendor"):
    s[sysname]["coverage_end"] = "2026-06-30"
add("H03", "Temporary move out of state", s,
    [note("2026-04-10", "agency call center", "Member reports a temporary move to Nevada for 2-3 months to care for a parent; advised coverage continues if she returns within 3 months."),
     note("2026-07-08", "agency call center", "Member has returned to Riverton; her coverage now shows as ended on 2026-06-30.")],
    "escalate", "She followed the agency's guidance for a temporary move, but coverage was ended anyway; restoring it is an eligibility and coverage-dates decision.",
    policy="2.3|4.1", urgent=True, treatment=True)

# H04 Employer reported the wrong end date (harder version)
s = base(44, "Derek", "Lam", "1984-10-02")
s["insurer"]["primary_payer"] = "Employer plan: Westbrook Retail (ends 2026-09-30 per employer)"
s["payment_vendor"]["payment_history"] = "Claim on 2026-07-14 denied: employer plan listed as primary"
add("H04", "Wrong employer end date", s,
    [note("2026-07-20", "agency call center", "Member lost his job; employer coverage actually ended 2026-06-30, but the employer reported 2026-09-30. He asks to correct the end date and reprocess his denied claim.")],
    "escalate", "The wrong employer end date made a private plan look active and caused a denied claim; correcting it and reprocessing claims needs a caseworker.",
    policy="4.3")

# H05 Jennifer vs. Jenny: two different members (harder version)
s = base(45, "Jennifer", "Singer", "1990-03-12")
s["insurer"]["first_name"] = "Jenny"
s["payment_vendor"]["payment_history"] = "3 recent claims for treatments the member says she never received"
add("H05", "Jennifer vs. Jenny", s,
    [note("2026-08-11", "agency call center", "Member Jennifer Singer, who goes by Jenny, received bills for treatments she never had. She believes they belong to a different member named Jenny Singer.")],
    "escalate", "Two different members share the name 'Jenny', and Jennifer received another member's bills; an automatic name change could make the mix-up worse.",
    policy="5.1")

# H06 Medicare starting in the future (new situation)
s = base(46, "Walter", "Brennan", "1961-11-20")
s["insurer"]["primary_payer"] = "Medicare (effective 2026-12-01)"
s["payment_vendor"]["payment_history"] = "Claims since 2026-10-01 sent to Medicare and rejected: Medicare not yet effective"
add("H06", "Medicare starts in the future", s,
    [note("2026-10-20", "agency call center", "Member's Medicare enrollment is approved, effective 2026-12-01. He asks that claims stop being sent to Medicare before then.")],
    "escalate", "Medicare's start date is in the future, so claims went to Medicare too early; routing them back changes which plan pays first.",
    policy="4.3", urgent=True, treatment=True)

# H07 Claims paid after coverage ended (new situation)
s = base(47, "Irene", "Kowalski", "1958-05-30")
for sysname in ("agency", "insurer"):
    s[sysname]["coverage_end"] = "2026-08-30"
s["payment_vendor"]["payment_history"] = "Claim for a visit on 2026-09-05 paid by the program"
add("H07", "Claims paid after coverage ended", s,
    [note("2026-09-12", "payment vendor", "Flagged: a claim for 2026-09-05 was paid, after the coverage end date of 2026-08-30.")],
    "escalate", "The payment vendor paid a claim after coverage ended, so the records disagree; recovering payments is a billing decision and the agent can never bill a member.",
    policy="4.1")

# H08 Contradictory notes about an email (new situation)
s = base(48, "Sana", "Dada", "1993-01-05")
s["agency"]["email"] = "sana.dadda05@example.com"
add("H08", "Contradictory notes", s,
    [note("2026-05-02", "agency call center",
          f"Member (SSN {s['agency']['ssn']}, ID {s['agency']['member_id']}, DOB 01/05/1993) confirmed a new email: sana.dadda05@example.com."),
     note("2026-05-03", "agency call center", "Member called back: yesterday's email had a typo. The correct email is sana.dada05@example.com.")],
    "auto_correct", "The later note corrects a typo in the earlier one; email is low risk and the corrected value is in a note, so every system should use the newest value.",
    policy="2.1", corrections="agency.email;insurer.email;payment_vendor.email")

# H09 Birthdate in two formats, unambiguous (no action)
s = base(49, "Liam", "Porter", "2015-01-16")
s["agency"]["dob"] = "01/16/2015"
for sysname in ("insurer", "payment_vendor"):
    s[sysname]["dob"] = "16/01/2015"
add("H09", "DOB format, unambiguous", s, [],
    "no_action", "Same date in two formats, and unambiguous because there is no month 16. Expected to fail at first: masking hides the format from the agent.")

# H10 Birthdate in two formats, ambiguous (escalate)
s = base(50, "Ava", "Porter", "2015-01-06")
s["agency"]["dob"] = "01/06/2015"
for sysname in ("insurer", "payment_vendor"):
    s[sysname]["dob"] = "06/01/2015"
add("H10", "DOB format, ambiguous", s, [],
    "escalate", "01/06/2015 could be January 6 or June 1, so the records can't show it is the same birthdate; identity needs a person.",
    policy="5.1")


def main():
    os.makedirs(os.path.join(HERE, "cases"), exist_ok=True)
    for c in CASES:
        with open(os.path.join(HERE, "cases", f"{c['case_id']}.json"), "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2)
    with open(os.path.join(HERE, "labels.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(LABELS[0].keys()))
        w.writeheader()
        w.writerows(LABELS)
    print(f"Wrote {len(CASES)} held-out cases")


if __name__ == "__main__":
    main()
