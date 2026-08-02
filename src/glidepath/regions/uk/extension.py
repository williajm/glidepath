"""Future-year extension of UK tax-year data (roadmap 2.5; planning §5.3).

Past the last shipped ``tax_year_*.toml`` the region synthesizes
tax-year files by extending the final shipped year per the
``policy.tax.future_years`` assumption (planning §7):

- ``frozen`` — every figure carried forward unchanged indefinitely
  (nominal freeze: fiscal drag forever);
- ``cpi_indexed`` — money figures compound with assumed CPI from the
  last shipped year (the schedule holds constant in real terms);
- ``frozen_then_cpi_indexed`` — frozen up to and including
  ``frozen_until_tax_year``, CPI-indexed thereafter. The shipped
  default: the legislated freeze to 2030/31 (planning §6), then
  indexation.

Legislated data always beats extrapolation: extension applies only past
the last shipped file and re-bases automatically when a newer file
ships. Modelling conventions (planning §5.3): indexation scales the
money figures of the income-tax schedules and the pension and ISA
allowances, quantized to whole pounds with ``ROUND_HALF_EVEN`` (the
core rounding mode); band and taper *rates* never extrapolate; the
state pension rate is carried forward untouched because its uprating
is governed by ``policy.state_pension.uprating`` (§7), not by this
policy. A target year's figures compound once from the base file, so
they do not depend on intermediate synthesized years.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, NoReturn

from glidepath.core import Money
from glidepath.regions.uk.schema import (
    DataFileError,
    IncomeTaxSchedule,
    IsaRules,
    PensionRules,
    TaxBand,
    TaxYearFile,
    TaxYearMeta,
    parse_tax_year_label,
    tax_year_end,
    tax_year_label,
    tax_year_start,
)

if TYPE_CHECKING:
    from glidepath.core import Rate
    from glidepath.regions.uk.schema import AssumptionValue

_CONTEXT = "policy.tax.future_years"
_POUND = Decimal(1)


def _fail(context: str, problem: str) -> NoReturn:
    """Raise a :class:`DataFileError` locating ``problem`` at ``context``."""
    msg = f"{context}: {problem}"
    raise DataFileError(msg)


class FutureYearsMode(StrEnum):
    """How the last shipped tax year extrapolates (planning §5.3, §7)."""

    FROZEN = "frozen"
    CPI_INDEXED = "cpi_indexed"
    FROZEN_THEN_CPI_INDEXED = "frozen_then_cpi_indexed"


def _parse_mode(raw: object | None) -> FutureYearsMode:
    """Parse the required ``mode`` tag of the assumption value."""
    if raw is None:
        _fail(_CONTEXT, "missing required key 'mode'")
    if not isinstance(raw, str):
        _fail(f"{_CONTEXT}.mode", f"expected a string, got {type(raw).__name__}")
    try:
        return FutureYearsMode(raw)
    except ValueError:
        known = ", ".join(mode.value for mode in FutureYearsMode)
        _fail(f"{_CONTEXT}.mode", f"unknown mode {raw!r} (one of: {known})")


@dataclass(frozen=True, slots=True)
class FutureYearsPolicy:
    """The parsed ``policy.tax.future_years`` assumption value."""

    mode: FutureYearsMode
    frozen_until_start_year: int | None = None
    """Start year of the last frozen tax year (``frozen_then_cpi_indexed``)."""

    def __post_init__(self) -> None:
        """Require the freeze end exactly when the mode uses one."""
        needs_until = self.mode is FutureYearsMode.FROZEN_THEN_CPI_INDEXED
        if needs_until and self.frozen_until_start_year is None:
            _fail(_CONTEXT, f"mode {self.mode} requires frozen_until_tax_year")
        if not needs_until and self.frozen_until_start_year is not None:
            _fail(_CONTEXT, f"mode {self.mode} does not take frozen_until_tax_year")

    @classmethod
    def from_assumption_value(cls, value: AssumptionValue) -> FutureYearsPolicy:
        """Parse the assumption's table value into a typed policy."""
        if not isinstance(value, Mapping):
            _fail(_CONTEXT, f"expected a table value, got {type(value).__name__}")
        entries = dict(value)
        mode = _parse_mode(entries.pop("mode", None))
        frozen_raw = entries.pop("frozen_until_tax_year", None)
        if entries:
            _fail(_CONTEXT, f"unknown keys: {', '.join(sorted(entries))}")
        frozen_until: int | None = None
        if frozen_raw is not None:
            context = f"{_CONTEXT}.frozen_until_tax_year"
            if not isinstance(frozen_raw, str):
                type_name = type(frozen_raw).__name__
                _fail(context, f"expected a 'YYYY/YY' string, got {type_name}")
            frozen_until = parse_tax_year_label(frozen_raw, context)
        return cls(mode=mode, frozen_until_start_year=frozen_until)

    def indexation_steps(self, *, base_start_year: int, target_start_year: int) -> int:
        """Years of CPI compounding the target year gets over the base year.

        Compounding starts after whichever is later of the base year (the
        last shipped, legislated file) and the policy's freeze end —
        legislated data always beats extrapolation.
        """
        if self.mode is FutureYearsMode.FROZEN:
            return 0
        floor = base_start_year
        if self.frozen_until_start_year is not None:
            floor = max(floor, self.frozen_until_start_year)
        return max(0, target_start_year - floor)


@dataclass(frozen=True, slots=True)
class FutureYearsExtension:
    """Everything :class:`UkTaxSystem` needs to extend past shipped data."""

    policy: FutureYearsPolicy
    cpi: Rate
    """Assumed annual CPI (the ``inflation.cpi`` assumption)."""

    def __post_init__(self) -> None:
        """Reject a CPI at or below -100%: growth factors must stay positive."""
        if self.cpi.growth_factor <= 0:
            _fail("FutureYearsExtension.cpi", "CPI must be greater than -100%")


def _indexed_money(money: Money, factor: Decimal) -> Money:
    """Scale a threshold or allowance, quantized to whole pounds (§5.3)."""
    return Money((money.amount * factor).quantize(_POUND, rounding=ROUND_HALF_EVEN))


def _indexed_band(band: TaxBand, factor: Decimal) -> TaxBand:
    """Index a band's upper bound; the rate never extrapolates."""
    upper = None if band.upper is None else _indexed_money(band.upper, factor)
    return TaxBand(name=band.name, rate=band.rate, upper=upper)


def _indexed_schedule(
    schedule: IncomeTaxSchedule, factor: Decimal
) -> IncomeTaxSchedule:
    """Index one regime's allowance, taper threshold, and band uppers."""
    return IncomeTaxSchedule(
        personal_allowance=_indexed_money(schedule.personal_allowance, factor),
        pa_taper_threshold=_indexed_money(schedule.pa_taper_threshold, factor),
        pa_taper_rate=schedule.pa_taper_rate,
        bands=tuple(_indexed_band(band, factor) for band in schedule.bands),
    )


def _indexed_pension(pension: PensionRules, factor: Decimal) -> PensionRules:
    """Index the pension allowances; relief and taper rates are unchanged."""
    return PensionRules(
        annual_allowance=_indexed_money(pension.annual_allowance, factor),
        aa_taper_threshold_income=_indexed_money(
            pension.aa_taper_threshold_income, factor
        ),
        aa_taper_adjusted_income=_indexed_money(
            pension.aa_taper_adjusted_income, factor
        ),
        aa_taper_rate=pension.aa_taper_rate,
        aa_taper_floor=_indexed_money(pension.aa_taper_floor, factor),
        mpaa=_indexed_money(pension.mpaa, factor),
        relief_at_source_rate=pension.relief_at_source_rate,
        tax_free_lump_sum_fraction=pension.tax_free_lump_sum_fraction,
        lump_sum_allowance=_indexed_money(pension.lump_sum_allowance, factor),
        lump_sum_death_benefit_allowance=_indexed_money(
            pension.lump_sum_death_benefit_allowance, factor
        ),
    )


def _indexed_isa(isa: IsaRules, factor: Decimal) -> IsaRules:
    """Index the ISA allowances; the LISA bonus and charge are unchanged."""
    return IsaRules(
        annual_allowance=_indexed_money(isa.annual_allowance, factor),
        lisa_allowance=_indexed_money(isa.lisa_allowance, factor),
        lisa_bonus_rate=isa.lisa_bonus_rate,
        lisa_withdrawal_charge=isa.lisa_withdrawal_charge,
    )


def extend_tax_year(
    base: TaxYearFile,
    target_start_year: int,
    *,
    policy: FutureYearsPolicy,
    cpi: Rate,
) -> TaxYearFile:
    """Synthesize the tax-year file for ``target_start_year`` from ``base``.

    ``base`` should be the last shipped file: extension only reaches
    forward, and shipped (legislated) data always beats extrapolation.
    The synthesized meta keeps the base file's ``verified_on`` and
    ``sources`` — they date the figures the extrapolation rests on.

    Raises:
        ValueError: If ``target_start_year`` is not after the base year.
    """
    base_start_year = base.meta.start_date.year
    if target_start_year <= base_start_year:
        msg = (
            f"target start year {target_start_year} is not after the last"
            f" shipped tax year {base.meta.tax_year}; use the shipped file"
        )
        raise ValueError(msg)
    meta = TaxYearMeta(
        tax_year=tax_year_label(target_start_year),
        start_date=tax_year_start(target_start_year),
        end_date=tax_year_end(target_start_year),
        verified_on=base.meta.verified_on,
        sources=base.meta.sources,
    )
    steps = policy.indexation_steps(
        base_start_year=base_start_year, target_start_year=target_start_year
    )
    if steps == 0:
        return TaxYearFile(
            schema_version=base.schema_version,
            meta=meta,
            income_tax_ruk=base.income_tax_ruk,
            income_tax_scotland=base.income_tax_scotland,
            pension=base.pension,
            isa=base.isa,
            state_pension=base.state_pension,
        )
    factor = cpi.growth_factor**steps
    return TaxYearFile(
        schema_version=base.schema_version,
        meta=meta,
        income_tax_ruk=_indexed_schedule(base.income_tax_ruk, factor),
        income_tax_scotland=_indexed_schedule(base.income_tax_scotland, factor),
        pension=_indexed_pension(base.pension, factor),
        isa=_indexed_isa(base.isa, factor),
        state_pension=base.state_pension,
    )
