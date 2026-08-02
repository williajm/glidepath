"""Invariant tests for the contribution boundary types (issue 3.2)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    AssumptionKey,
    ContributionSchedule,
    Decision,
    Fact,
    MemberContributionOutcome,
    Money,
    ReliefMechanic,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def money(amount: str) -> Money:
    """Build ``Money`` from a decimal string."""
    return Money(Decimal(amount))


def employee(amount: str) -> Decision[Money]:
    """A chosen gross employee contribution."""
    return Decision(value=money(amount), recorded_on=RECORDED)


def employer(amount: str) -> Fact[Money]:
    """A stated employer contribution."""
    return Fact(value=money(amount), as_of=date(2026, 8, 1), recorded_on=RECORDED)


def outcome(
    gross_to_pot: str = "100",
    member_cash_cost: str = "80",
    provider_relief: str = "20",
) -> MemberContributionOutcome:
    """Build an outcome; defaults model a valid relief-at-source case."""
    return MemberContributionOutcome(
        gross_to_pot=money(gross_to_pot),
        member_cash_cost=money(member_cash_cost),
        provider_relief=money(provider_relief),
        taxable_pay_deduction=money("0"),
        assessment_relief_gross=money(gross_to_pot),
        unrelieved_excess=money("0"),
    )


def test_schedule_holds_choice_fact_and_mechanic() -> None:
    """The employee amount is a decision, the employer amount a fact."""
    schedule = ContributionSchedule(
        employee_amount=employee("6000"),
        employer_amount=employer("4000"),
        relief_mechanic=ReliefMechanic.NET_PAY,
        escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
    )
    assert schedule.employee_amount.value == money("6000")
    assert schedule.employer_amount is not None
    assert schedule.employer_amount.value == money("4000")
    assert schedule.escalation is AssumptionKey.EARNINGS_GROWTH_REAL


def test_schedule_defaults_model_a_reliefless_wrapper() -> None:
    """No employer, no mechanic, no escalation: an ISA-style schedule."""
    schedule = ContributionSchedule(employee_amount=employee("5000"))
    assert schedule.employer_amount is None
    assert schedule.relief_mechanic is None
    assert schedule.escalation is None


def test_schedule_rejects_negative_employee_amount() -> None:
    """A negative chosen contribution is a construction error."""
    negative = employee("-1")
    with pytest.raises(ValueError, match="employee_amount"):
        ContributionSchedule(employee_amount=negative)


def test_schedule_rejects_negative_employer_amount() -> None:
    """A negative employer contribution is a construction error."""
    chosen = employee("100")
    negative = employer("-1")
    with pytest.raises(ValueError, match="employer_amount"):
        ContributionSchedule(employee_amount=chosen, employer_amount=negative)


def test_outcome_enforces_pot_cash_relief_identity() -> None:
    """``gross_to_pot`` must equal member cash plus provider relief."""
    with pytest.raises(ValueError, match="member_cash_cost"):
        outcome(gross_to_pot="100", member_cash_cost="80", provider_relief="19")


def test_outcome_rejects_negative_amounts() -> None:
    """Every outcome amount must be non-negative."""
    with pytest.raises(ValueError, match="non-negative"):
        outcome(member_cash_cost="-20", provider_relief="120")


def test_outcome_net_pay_shape_constructs() -> None:
    """A net-pay outcome (no provider relief, full pay deduction) is valid."""
    net_pay = MemberContributionOutcome(
        gross_to_pot=money("100"),
        member_cash_cost=money("100"),
        provider_relief=money("0"),
        taxable_pay_deduction=money("100"),
        assessment_relief_gross=money("0"),
        unrelieved_excess=money("0"),
    )
    assert net_pay.taxable_pay_deduction == money("100")
