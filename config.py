"""Settings for the member record agent. Change these, not the code.

Everything here is a product decision. The values below are starting defaults;
set your own bar BEFORE the first run and write it in the README.
"""

# --- Models -----------------------------------------------------------------
# Check https://docs.claude.com for current model names before running.
DEFAULT_MODEL = "claude-sonnet-5-5"
SMALL_MODEL = "claude-haiku-5-5"
LARGE_MODEL = "claude-opus-5-5"

# Optional: price per million tokens (input, output) in USD, from
# https://www.anthropic.com/pricing. Leave as None to report tokens only.
PRICES_PER_MILLION = {
    # "claude-sonnet-5-5": (3.00, 15.00),
}

# --- The program ------------------------------------------------------------
# A fictional state, so nothing points at a real agency or client.
PROGRAM_STATE = "CS"  # "Cascadia"
PROGRAM_NAME = "Cascadia Health Coverage Program"

SYSTEMS = ["agency", "insurer", "payment_vendor"]

# Which system the rules trust for each field. The agent sees this, and must
# notice when the evidence says the source of truth is out of date.
SOURCE_OF_TRUTH = {
    "first_name": "agency",
    "middle_name": "agency",
    "last_name": "agency",
    "address": "agency",
    "phone": "agency",
    "email": "agency",
    "eligibility_status": "agency",
    "coverage_start": "insurer",
    "coverage_end": "insurer",
    "coverage_type": "insurer",   # self, or dependent on someone else's plan
    "primary_payer": "insurer",   # which plan pays first
    "payment_history": "payment_vendor",  # past payments and claim outcomes
}

# --- What the agent may change (enforced in code, not just the prompt) -------
AUTO_CORRECT_FIELDS = {"address", "first_name", "middle_name", "last_name", "phone", "email"}

# Fields the agent's role can never see. Identifiers are replaced by match
# results; restricted fields are left out entirely.
IDENTIFIER_FIELDS = {"ssn", "member_id", "dob"}
RESTRICTED_FIELDS = {"household_income", "diagnosis"}

# --- The bar (decide before the first run) ----------------------------------
BAR = {
    "action_accuracy_min": 0.90,
    "critical_errors_max": 0,
    "over_escalations_max": 3,
    "pii_leaks_max": 0,
}

MAX_AGENT_TURNS = 10
