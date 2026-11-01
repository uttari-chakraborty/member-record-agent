"""PII masking: nothing identifying reaches Claude.

Identifiers (SSN, member ID, date of birth) are removed from records and
replaced with match results (see rules.identity_checks). The same masking runs
on free-text case notes. Restricted fields are dropped entirely.
"""
import re
import config

SSN_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")
MEMBER_ID_RE = re.compile(r"\bM\d{8}\b")


def _dob_variants(dob):
    """'1968-04-12' -> the formats a person might type it in."""
    if len(dob) != 10 or dob[4] != "-":
        return {dob}  # stored in another format (e.g. 01/16/2015): mask it exactly as stored
    y, m, d = dob.split("-")
    mi, di = str(int(m)), str(int(d))
    return {dob, f"{m}/{d}/{y}", f"{mi}/{di}/{y}", f"{m}-{d}-{y}", f"{mi}-{di}-{y}"}


def raw_identifiers(case):
    """Every identifier value in the case, for masking and for the leak scan."""
    values = set()
    for rec in case["systems"].values():
        for f in ("ssn", "member_id"):
            if rec.get(f):
                values.add(rec[f])
                values.add(rec[f].replace("-", ""))
        # Documented exception: when a decision depends on the birthdate itself (e.g. newborn
        # coverage starts on the date of birth), it is not masked. Minimum necessary, not nothing.
        if rec.get("dob") and not case.get("dob_needed_for_decision"):
            values |= _dob_variants(rec["dob"])
    return values


def mask_text(text, case):
    for v in sorted(raw_identifiers(case), key=len, reverse=True):
        text = text.replace(v, "[REDACTED]")
    text = SSN_RE.sub("[SSN REDACTED]", text)
    text = MEMBER_ID_RE.sub("[MEMBER ID REDACTED]", text)
    return text


def mask_record(record, include_names):
    hidden = config.IDENTIFIER_FIELDS | config.RESTRICTED_FIELDS
    out = {k: v for k, v in record.items() if k not in hidden}
    if not include_names:
        for f in ("first_name", "middle_name", "last_name"):
            out.pop(f, None)
    return out


def masked_notes(case):
    return [
        {"note_id": f"note:{i + 1}", "date": n["date"], "source": n["source"],
         "text": mask_text(n["text"], case)}
        for i, n in enumerate(case.get("notes", []))
    ]


def find_leaks(text, case):
    """Return any raw identifier that appears in text (should always be empty)."""
    leaks = [v for v in raw_identifiers(case) if v in text]
    leaks += [m for m in SSN_RE.findall(text)]
    return sorted(set(leaks))
