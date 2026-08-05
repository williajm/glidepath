"""Generic tax-assessment shapes crossing the core/region boundary.

Implements the planning §4.2 boundary: the core never knows band names or
policy figures. A region's :class:`TaxSystem` assesses a
:class:`TaxInput` — categorised gross income plus an opaque residency
id — for one period and returns a :class:`TaxResult` whose band labels
are region-supplied strings.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from glidepath.core.money import Money

if TYPE_CHECKING:
    from glidepath.core.entities import TaxResidencyId
    from glidepath.core.money import Rate
    from glidepath.core.periods import Period

_ZERO = Money(Decimal(0))


@dataclass(frozen=True, slots=True)
class TaxInput:
    """One person's categorised gross income for a single period.

    ``non_savings_income`` is the employment/pension/property ladder;
    ``savings_income`` (interest) and ``dividend_income`` arise from
    taxable-growth wrappers (roadmap 9.2). How the categories stack —
    ordering, nil rates, which schedule each uses — is wholly the
    region's concern (planning §4.2).

    ``relief_at_source_contributions`` is the period's gross member
    pension contributions paid under a relief-at-source mechanic
    (roadmap 3.2): basic-rate relief already arrived at source, and the
    region's assessment grants the higher rates — e.g. by extending its
    band thresholds — and deducts the gross amount from any
    allowance-taper income measure. Net-pay contributions never appear
    here: they leave pay before tax, so the caller excludes them from
    ``non_savings_income``.
    """

    residency: TaxResidencyId
    non_savings_income: Money
    savings_income: Money = _ZERO
    dividend_income: Money = _ZERO
    relief_at_source_contributions: Money = _ZERO

    def __post_init__(self) -> None:
        """Reject negative amounts."""
        amounts = (
            ("non_savings_income", self.non_savings_income),
            ("savings_income", self.savings_income),
            ("dividend_income", self.dividend_income),
            (
                "relief_at_source_contributions",
                self.relief_at_source_contributions,
            ),
        )
        for name, amount in amounts:
            if amount < _ZERO:
                msg = f"TaxInput.{name} must be non-negative"
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TaxLine:
    """Tax charged within one region-defined band of an assessment."""

    band: str
    rate: Rate
    taxed: Money
    tax: Money

    def __post_init__(self) -> None:
        """Reject unnamed bands and negative amounts."""
        if not self.band:
            msg = "TaxLine.band must not be empty"
            raise ValueError(msg)
        if self.taxed < _ZERO or self.tax < _ZERO:
            msg = "TaxLine amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TaxResult:
    """The outcome of one period's assessment for one person.

    ``lines`` is the band-by-band breakdown. ``tax_due`` must equal the
    sum of the line taxes (enforced), so any result that exists is
    self-consistent.
    """

    tax_due: Money
    taxable_income: Money
    tax_free_allowance: Money
    lines: tuple[TaxLine, ...]

    def __post_init__(self) -> None:
        """Require non-negative amounts and a consistent breakdown."""
        if self.taxable_income < _ZERO or self.tax_free_allowance < _ZERO:
            msg = "TaxResult amounts must be non-negative"
            raise ValueError(msg)
        total = sum((line.tax for line in self.lines), start=_ZERO)
        if self.tax_due != total:
            msg = "TaxResult.tax_due must equal the sum of its lines"
            raise ValueError(msg)


class TaxSystem(Protocol):
    """Region-supplied tax assessment (planning §4.2).

    The same function serves both the withdrawal gross-up iteration and
    the final period assessment (planning §5.2 steps 4-5), so the two are
    consistent by construction.
    """

    def assess(self, period: Period, tax_input: TaxInput) -> TaxResult:
        """Assess ``tax_input`` for ``period`` under this region's rules."""
        ...

    def annual_allowance_charge(
        self, period: Period, tax_input: TaxInput, excess: Money
    ) -> tuple[TaxLine, ...]:
        """Price the tax on a pension-input excess for ``period``.

        ``excess`` is the chargeable excess a region's annual-allowance
        measurement produced
        (:meth:`~glidepath.core.contributions.ContributionRuleset.annual_allowance`);
        ``tax_input`` is the same full income picture the period's
        final assessment sees, fixing the excess's position in the
        region's rate structure. The excess is a charge, not income —
        it must never feed back into ``assess`` (it would distort
        income-measured allowances) — so it prices here as separate
        lines the engine appends to the final result. A region without
        such a charge returns no lines.
        """
        ...
