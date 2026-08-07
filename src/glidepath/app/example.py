"""The example plan the facts form opens with (planning §4.9).

A first launch shows a filled form and a live projection instead of a
blank screen, so a new user sees what glidepath produces before typing
anything. The example is nothing but raw form text: it flows through
``parse_facts_form`` exactly like a user submission, is labelled as an
example in the UI copy, and one click clears it (§4.9 — the facts
principle survives because nothing here is ever silently treated as
the user's own statement).

The persona extends the §4.5 golden scenario: a 35-year-old on a
£52,000 salary targeting retirement at 62, with a workplace DC pension
(net pay, employer-matched, growing with earnings), a stocks & shares
ISA, a mid-career state pension forecast, and a £24,000 net spending
need in today's money. The numbers are tuned so the plan holds
together — the deterministic projection meets every period's need and
a seeded Monte Carlo run succeeds far more often than not, while still
leaving visibly failing paths on the fan chart — because the launch
surface is a demonstration, and a first impression of a plan already
in ruin reads as a broken app rather than an honest warning.
"""

from glidepath.app.forms import FactsFormData

_PERSON = {
    "date_of_birth": "1991-06-15",
    "tax_residency": "uk.ruk",
    "employment_income": "52000",
    "target_retirement_age": "62",
}
_SPENDING = {"annual_spending_real": "24000"}
# Example values deliberately avoid every policy-figure literal (the
# boundary guard greps for those) — e.g. the forecast is below the full
# new state pension rate, as a real mid-career forecast would be.
_STATE_PENSION = {"forecast_weekly_amount": "230.25"}
_WORKPLACE_DC = {
    "kind": "uk.workplace_dc",
    "balance": "48000",
    "employee_contribution": "4200",
    "employer_contribution": "3150",
    "relief_mechanic": "net_pay",
    "escalation": "earnings",
}
# The ISA saving is £400/month — deliberately neither the £4,000 LISA
# allowance nor the £5,000 savings starting-rate limit either side of it.
_ISA = {
    "kind": "uk.isa",
    "balance": "16500",
    "employee_contribution": "4800",
}


def example_facts_form_data() -> FactsFormData:
    """The example plan as raw form text (module docstring).

    Guaranteed parseable: a test submits it through
    ``parse_facts_form`` and projects the result, so the form the app
    opens with can never show an error.
    """
    return FactsFormData(
        person=dict(_PERSON),
        spending=dict(_SPENDING),
        state_pension=dict(_STATE_PENSION),
        wrappers=(dict(_WORKPLACE_DC), dict(_ISA)),
        db_pensions=(),
    )
