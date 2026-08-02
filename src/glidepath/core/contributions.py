"""Contribution schedules and the relief-mechanics boundary (roadmap 3.2).

A :class:`ContributionSchedule` records what a person has *chosen* to pay
into one wrapper each year — the employee amount is a
:class:`~glidepath.core.provenance.Decision` (a scenario-overridable
choice, planning §4.3), the employer amount a
:class:`~glidepath.core.provenance.Fact` (employment terms). How tax
relief is delivered is the region's concern: the core defines the
mechanics vocabulary (:class:`~glidepath.core.wrappers.ReliefMechanic`)
and the outcome shape (:class:`MemberContributionOutcome`); a region's
:class:`ContributionRuleset` turns a gross contribution into cash flows
under its own relief rules and limits (planning §5.1).

Amounts are *gross* annual contributions — the amount intended to land
in the wrapper — so schedules are comparable across relief mechanics:
under relief at source the member pays less cash and the provider tops
the pot up to the gross amount, while under net pay the member's pay is
reduced by the full gross amount before tax.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from glidepath.core.money import Money

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionKey, Decision, Fact
    from glidepath.core.wrappers import ReliefMechanic

_ZERO = Money(Decimal(0))


@dataclass(frozen=True, slots=True)
class ContributionSchedule:
    """One wrapper's planned annual contributions (planning §5.1).

    ``employee_amount`` is the chosen *gross* annual contribution;
    ``employer_amount`` is the employer's annual contribution under the
    employment terms (pension wrappers only). ``relief_mechanic`` is
    ``None`` for wrapper kinds whose contributions attract no relief
    (the region's permitted-mechanics set is empty, e.g. an ISA).
    ``escalation`` names the assumption the engine grows the amounts by
    (e.g. the earnings-growth assumption); applying it is the engine
    step's job (roadmap 4.1).
    """

    employee_amount: Decision[Money]
    employer_amount: Fact[Money] | None = None
    relief_mechanic: ReliefMechanic | None = None
    escalation: AssumptionKey | None = None

    def __post_init__(self) -> None:
        """Reject negative contribution amounts."""
        if self.employee_amount.value < _ZERO:
            msg = "ContributionSchedule.employee_amount must be non-negative"
            raise ValueError(msg)
        if self.employer_amount is not None and self.employer_amount.value < _ZERO:
            msg = "ContributionSchedule.employer_amount must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MemberContributionRequest:
    """One member contribution to resolve through a region's relief rules.

    ``gross`` is the intended gross contribution to one wrapper;
    ``relevant_earnings`` is the period's earned income the region's
    relief limit measures against; ``date_of_birth`` lets the region
    apply any relief age limits. Relief limits are per *person* per
    period, shared across every wrapper and mechanic — so
    ``already_relieved_gross`` must carry the gross member
    contributions relief has already been granted on this period
    (across all the person's wrappers); the region grants relief only
    on the remaining headroom. ``mechanic`` is ``None`` when the
    wrapper kind attracts no relief.
    """

    gross: Money
    relevant_earnings: Money
    date_of_birth: date
    mechanic: ReliefMechanic | None = None
    already_relieved_gross: Money = _ZERO

    def __post_init__(self) -> None:
        """Reject negative monetary inputs."""
        amounts = (self.gross, self.relevant_earnings, self.already_relieved_gross)
        if any(amount < _ZERO for amount in amounts):
            msg = "MemberContributionRequest amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MemberContributionOutcome:
    """One member contribution resolved through a region's relief rules.

    ``gross_to_pot`` is what lands in the wrapper;
    ``member_cash_cost`` is the cash the member pays (from taxed income
    under relief at source, from gross pay under net pay);
    ``provider_relief`` is the top-up the provider reclaims at source;
    ``taxable_pay_deduction`` is the pre-tax pay reduction (net pay);
    ``assessment_relief_gross`` is the gross amount the tax assessment
    must grant further relief on (feeds
    :attr:`~glidepath.core.tax.TaxInput.relief_at_source_contributions`);
    ``unrelieved_excess`` is intended gross beyond the region's relief
    limit — clipped, not contributed (routing it to a taxable wrapper
    is a later concern, roadmap 9.2).

    The identity ``gross_to_pot == member_cash_cost + provider_relief``
    is enforced, so any outcome that exists is internally consistent.
    """

    gross_to_pot: Money
    member_cash_cost: Money
    provider_relief: Money
    taxable_pay_deduction: Money
    assessment_relief_gross: Money
    unrelieved_excess: Money

    def __post_init__(self) -> None:
        """Require non-negative amounts and the pot-cash-relief identity."""
        amounts = (
            self.gross_to_pot,
            self.member_cash_cost,
            self.provider_relief,
            self.taxable_pay_deduction,
            self.assessment_relief_gross,
            self.unrelieved_excess,
        )
        if any(amount < _ZERO for amount in amounts):
            msg = "MemberContributionOutcome amounts must be non-negative"
            raise ValueError(msg)
        if self.gross_to_pot != self.member_cash_cost + self.provider_relief:
            msg = (
                "MemberContributionOutcome.gross_to_pot must equal"
                " member_cash_cost + provider_relief"
            )
            raise ValueError(msg)


class ContributionRuleset(Protocol):
    """Region-supplied contribution relief mechanics (planning §4.2).

    Resolves one member contribution for one period under the region's
    relief rules — gross-up at source, pre-tax deduction, and the
    region's member relief limits. Which mechanics a wrapper kind may
    operate is the wrapper ruleset's call
    (:meth:`~glidepath.core.wrappers.WrapperRuleset.permitted_relief_mechanics`);
    cross-wrapper contribution measures (e.g. a pension annual
    allowance) are a separate regional concern (roadmap 3.3).
    """

    def member_contribution(
        self, request: MemberContributionRequest, period: Period
    ) -> MemberContributionOutcome:
        """Resolve one gross member contribution for ``period``."""
        ...
