"""UK wrapper kinds and rules (roadmap 3.1, 9.2; planning §4.2, §5.1, §6).

Implements the core :class:`~glidepath.core.WrapperRuleset` protocol for
the UK wrapper kinds — workplace DC, SIPP, ISA, LISA, GIA and cash.
Every figure — the pension tax-free lump-sum fraction, the ISA and LISA
allowances, the LISA bonus rate — comes from the tax-year data files
(§5.3); nothing is hardcoded here (guard-tested). The kind-to-rules
*structure* (which kinds are pensions, which relief mechanics each may
operate) is scheme mechanics, not policy figures:

- Workplace DC schemes operate either relief mechanic: relief at source
  or a net pay arrangement, per the employer's scheme.
- A SIPP is a personal pension, so contributions get relief at source.
- ISAs and LISAs attract no contribution relief; growth and withdrawals
  are tax-free. The LISA's 25% government bonus is a contribution
  bonus, not tax relief (it never extends tax bands), and its
  contributions are age-windowed (18 to 50, pro-rated by whole months).
- GIA and cash accounts are bare taxable accounts: paid from taxed
  income, growth taxable as it arises (dividends and interest feed the
  §6 savings/dividend layers), withdrawals themselves tax-free.
- Pension money in is tax-relieved, grows tax-free, and comes out as
  taxable income after the tax-free lump-sum fraction (EET).

Access follows the §4.1 gate convention: pension kinds are gated by the
NMPA (via :class:`~glidepath.regions.uk.ages.UkAgeRules`, including the
2028 step-up); LISAs by the charge-free access age of 60 — the engine
never models a charged early withdrawal, so the 25% withdrawal charge
ships as data but no draw ever bears it (planning §5.2 gates are
errors, never silent draws); ISAs, GIAs and cash have no access age.
Contribution caps: the ISA annual allowance is a per-person cap shared
across all ISAs and LISAs through the ``uk.isa`` allowance group, and
the LISA allowance is a sub-cap inside it (its own group); pension
kinds have no per-kind cap here — the annual allowance is a
cross-pension measure of pension input amounts and lands in roadmap
3.3, the member relief limit with contribution schedules in 3.2.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from glidepath.core import (
    ContributionCap,
    ContributionTaxTreatment,
    ContributionTerms,
    GrowthTaxTreatment,
    ReliefMechanic,
    WithdrawalTaxTreatment,
    WrapperKindId,
    WrapperTaxTreatment,
)
from glidepath.regions.uk.ages import UkAgeRules
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from glidepath.core import Money, Period
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import TaxYearFile

WORKPLACE_DC_KIND = WrapperKindId("uk.workplace_dc")
"""An employer-sponsored defined-contribution pension scheme."""

SIPP_KIND = WrapperKindId("uk.sipp")
"""A self-invested personal pension."""

ISA_KIND = WrapperKindId("uk.isa")
"""A (stocks-and-shares or cash) individual savings account."""

LISA_KIND = WrapperKindId("uk.lisa")
"""A lifetime ISA: bonus-bearing, age-windowed, gated until 60."""

GIA_KIND = WrapperKindId("uk.gia")
"""A general investment account: bare, taxable as growth arises."""

CASH_KIND = WrapperKindId("uk.cash")
"""A cash savings account outside any wrapper: interest is taxable."""

ISA_ALLOWANCE_GROUP = "uk.isa"
"""Allowance group of the overall ISA annual allowance (ISA + LISA)."""

LISA_ALLOWANCE_GROUP = "uk.lisa"
"""Allowance group of the LISA sub-allowance inside the overall one."""

_PENSION_KINDS = frozenset({WORKPLACE_DC_KIND, SIPP_KIND})
_TAX_FREE_SAVINGS_KINDS = frozenset({ISA_KIND, LISA_KIND})
_TAXABLE_KINDS = frozenset({GIA_KIND, CASH_KIND})

_RELIEF_MECHANICS: Mapping[WrapperKindId, frozenset[ReliefMechanic]] = {
    WORKPLACE_DC_KIND: frozenset(
        {ReliefMechanic.RELIEF_AT_SOURCE, ReliefMechanic.NET_PAY}
    ),
    SIPP_KIND: frozenset({ReliefMechanic.RELIEF_AT_SOURCE}),
    ISA_KIND: frozenset(),
    LISA_KIND: frozenset(),
    GIA_KIND: frozenset(),
    CASH_KIND: frozenset(),
}


class UkWrapperError(ValueError):
    """A wrapper query the shipped UK data cannot answer."""


def _unknown_kind(kind: WrapperKindId) -> NoReturn:
    """Reject a wrapper kind the UK region does not define."""
    msg = f"unknown UK wrapper kind {kind!r}"
    raise UkWrapperError(msg)


@dataclass(frozen=True, slots=True)
class UkWrapperRuleset:
    """UK implementation of the core ``WrapperRuleset`` protocol.

    Holds the tax-year files its figures come from, plus the age rules
    behind the pension access gate. Figure queries (``tax_treatment``,
    ``annual_contribution_limit``) share the tax system's coverage
    semantics: shipped data beats extrapolation, and the query's tax
    year is always resolved — even when the answer needs no yearly
    figure — so a query outside coverage fails loudly rather than
    answering from the wrong year. Access gates read the effective-dated
    age rules instead and carry no tax-year coverage constraint.
    """

    tax_years: tuple[TaxYearFile, ...]
    ages: UkAgeRules
    future_years: FutureYearsExtension | None = None

    def __post_init__(self) -> None:
        """Require at least one year, ascending and non-overlapping."""
        try:
            self._series()
        except UkTaxYearError as exc:
            raise UkWrapperError(str(exc)) from exc

    @classmethod
    def from_shipped_data(
        cls, future_years: FutureYearsExtension | None = None
    ) -> UkWrapperRuleset:
        """Build a ruleset over every shipped data file."""
        years = available_tax_years()
        return cls(
            tax_years=tuple(load_tax_year(year) for year in years),
            ages=UkAgeRules.from_shipped_data(),
            future_years=future_years,
        )

    def tax_treatment(self, kind: WrapperKindId, period: Period) -> WrapperTaxTreatment:
        """The in/during/out treatment of ``kind`` during ``period`` (§4.2).

        Pension kinds are EET with the year's tax-free lump-sum
        fraction on the way out; ISAs and LISAs are TEE; GIA and cash
        accounts are taxed as growth arises, with withdrawals
        themselves tax-free (module docstring).
        """
        year = self._year_for(period)
        if kind in _PENSION_KINDS:
            return WrapperTaxTreatment(
                contributions=ContributionTaxTreatment.TAX_RELIEVED,
                growth=GrowthTaxTreatment.TAX_FREE,
                withdrawals=WithdrawalTaxTreatment.PARTIALLY_TAX_FREE,
                tax_free_fraction=year.pension.tax_free_lump_sum_fraction,
            )
        if kind in _TAX_FREE_SAVINGS_KINDS:
            return WrapperTaxTreatment(
                contributions=ContributionTaxTreatment.FROM_TAXED_INCOME,
                growth=GrowthTaxTreatment.TAX_FREE,
                withdrawals=WithdrawalTaxTreatment.TAX_FREE,
            )
        if kind in _TAXABLE_KINDS:
            return WrapperTaxTreatment(
                contributions=ContributionTaxTreatment.FROM_TAXED_INCOME,
                growth=GrowthTaxTreatment.TAXABLE,
                withdrawals=WithdrawalTaxTreatment.TAX_FREE,
            )
        _unknown_kind(kind)

    def contribution_terms(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> ContributionTerms:
        """The contribution terms of ``kind`` for one person and period.

        The ISA annual allowance is per person per tax year, shared
        across all their ISAs and LISAs through the ``uk.isa`` group;
        the LISA allowance is a sub-cap inside it, and LISA
        contributions carry the year's bonus rate within the exact
        18-to-50 contribution window (planning §6) — the engine
        intersects the window with the period and the run window and
        pro-rates by whole months. Pension, GIA and cash kinds are
        uncapped
        here — the pension annual allowance is a cross-pension measure
        of pension input amounts (roadmap 3.3), not a per-kind cap.
        """
        year = self._year_for(period)
        if kind == ISA_KIND:
            return ContributionTerms(
                caps=(ContributionCap(ISA_ALLOWANCE_GROUP, year.isa.annual_allowance),)
            )
        if kind == LISA_KIND:
            return ContributionTerms(
                caps=(
                    ContributionCap(LISA_ALLOWANCE_GROUP, year.isa.lisa_allowance),
                    ContributionCap(ISA_ALLOWANCE_GROUP, year.isa.annual_allowance),
                ),
                bonus_rate=year.isa.lisa_bonus_rate,
                window=self.ages.lisa_contribution_window(date_of_birth),
            )
        if kind in _PENSION_KINDS or kind in _TAXABLE_KINDS:
            return ContributionTerms()
        _unknown_kind(kind)

    def permitted_relief_mechanics(
        self, kind: WrapperKindId
    ) -> frozenset[ReliefMechanic]:
        """The relief mechanics ``kind`` may operate (module docstring)."""
        mechanics = _RELIEF_MECHANICS.get(kind)
        if mechanics is None:
            _unknown_kind(kind)
        return mechanics

    def lump_sum_allowance(self, period: Period) -> Money | None:
        """The year's lump sum allowance (planning §6; roadmap 5.2).

        The per-person lifetime cap on pension tax-free cash — PCLS
        and the tax-free elements of UFPLS payments. The engine tracks
        usage across the run; this only answers the period's figure,
        with the usual coverage semantics (class docstring).
        """
        return self._year_for(period).pension.lump_sum_allowance

    def is_access_open(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> bool:
        """Whether *new* access to ``kind`` may open in ``period``.

        Pension kinds follow the NMPA access gate (§4.1), including the
        2028 step-up; LISAs the charge-free access age of 60 (module
        docstring); ISAs, GIAs and cash are always accessible. Only
        *new* pension access (crystallisation, UFPLS) is gated — funds
        already crystallised and benefits already in payment are never
        re-gated (planning §5.1), so someone who crystallised at 55
        before the 2028 step keeps drawing at 56 even while this gate
        is shut.
        """
        if kind in _PENSION_KINDS:
            return self.ages.is_pension_access_open(date_of_birth, period)
        if kind == LISA_KIND:
            return self.ages.is_lisa_access_open(date_of_birth, period)
        if kind == ISA_KIND or kind in _TAXABLE_KINDS:
            return True
        _unknown_kind(kind)

    def _series(self) -> TaxYearSeries:
        """The shared year-resolution series over this ruleset's files."""
        return TaxYearSeries(tax_years=self.tax_years, future_years=self.future_years)

    def _year_for(self, period: Period) -> TaxYearFile:
        """The shipped or synthesized file fully containing ``period``."""
        try:
            return self._series().year_for(period)
        except UkTaxYearError as exc:
            raise UkWrapperError(str(exc)) from exc
