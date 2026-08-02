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

    v1 carries non-savings income only (employment, pension and property
    income share one ladder in the UK); further categories (savings,
    dividends) are added as fields when the wrappers that produce them
    land (roadmap 9.2).
    """

    residency: TaxResidencyId
    non_savings_income: Money

    def __post_init__(self) -> None:
        """Reject negative income."""
        if self.non_savings_income < _ZERO:
            msg = "TaxInput.non_savings_income must be non-negative"
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
