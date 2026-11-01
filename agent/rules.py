"""Step 1: rule-based mismatch checks (standing in for the old SQL / PL/SQL).

Deliberately simple: exact comparison after trimming whitespace. Rules don't
know that "St" means "Street" or that a note explains a difference; deciding
what a mismatch means is the agent's job.
"""
import config

COMPARED_FIELDS = [
    "first_name", "middle_name", "last_name", "address", "phone", "email",
    "coverage_start", "coverage_end", "eligibility_status", "coverage_type", "primary_payer",
]


def _norm(value):
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, str):
        return value.strip()
    return value


def find_mismatches(case):
    """Return [{field, values: {system: value}}] for every field that differs."""
    systems = case["systems"]
    flagged = []
    for field in COMPARED_FIELDS:
        values = {s: systems[s].get(field) for s in config.SYSTEMS}
        if len({repr(_norm(v)) for v in values.values()}) > 1:
            flagged.append({"field": field, "values": values})
    # Identifiers are compared too, but their values never leave this function.
    for field in ["ssn", "member_id", "dob"]:
        if len({systems[s].get(field) for s in config.SYSTEMS}) > 1:
            flagged.append({"field": field, "values": "masked; see identity_checks"})
    return flagged


def _date_candidates(value):
    """Every real date a stored birthdate could mean, as (year, month, day).
    '2015-01-16' -> one; '01/16/2015' -> one (no month 16); '01/06/2015' -> two (Jan 6 or Jun 1)."""
    import re
    v = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return {(int(m[1]), int(m[2]), int(m[3]))}
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if not m:
        return set()
    a, b, y = int(m[1]), int(m[2]), int(m[3])
    out = set()
    if a <= 12 and 1 <= b <= 31:
        out.add((y, a, b))  # month/day
    if b <= 12 and 1 <= a <= 31:
        out.add((y, b, a))  # day/month
    return out


def _compare_dates(vals):
    cands = [_date_candidates(v) for v in vals]
    if any(not c for c in cands):
        return None
    common = set.intersection(*cands)
    if not common:
        return "different dates"
    if all(len(c) == 1 for c in cands):
        return "same date, written in different formats"
    return "possibly the same date, but the format is ambiguous (day and month could be swapped); cannot confirm"


def identity_checks(case):
    """Compare identifiers without revealing them: the agent sees only results."""
    systems = case["systems"]
    out = {}
    for field in ["ssn", "member_id", "dob"]:
        vals = [systems[s].get(field) for s in config.SYSTEMS]
        if len(set(vals)) == 1:
            out[f"{field}_match"] = "all systems match"
        else:
            detail = "differ"
            if field == "dob" and _compare_dates(vals) in ("same date, written in different formats",):
                out["dob_match"] = "same date, written in different formats in each system"
                continue
            if field == "dob" and _compare_dates(vals) and _compare_dates(vals).startswith("possibly"):
                out["dob_match"] = "possibly the same date, but the format is ambiguous (day and month could be swapped); cannot confirm"
                continue
            if field == "dob":
                a, b = sorted(set(vals))[:2]
                diff = sum(1 for x, y in zip(a, b) if x != y)
                detail = f"differ ({diff} character(s) different)"
            pairs = {s: chr(ord('A') + sorted(set(vals)).index(systems[s].get(field))) for s in config.SYSTEMS}
            out[f"{field}_match"] = f"{detail}; groups: {pairs}"
    extra = case.get("identity_flags")
    if extra:
        out.update(extra)
    return out
