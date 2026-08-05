"""Future-year extension of UK tax-year data (roadmap 2.5; planning §5.3).

Past the last shipped ``tax_year_*.toml`` the region synthesizes
tax-year files by extending the final shipped year per the
``policy.tax.future_years`` assumption (planning §7):

- ``frozen`` — every figure carried forward unchanged indefinitely
  (nominal freeze: fiscal drag forever);
- ``frozen_then_cpi_indexed`` — frozen up to and including
  ``frozen_until_tax_year``, CPI-indexed thereafter. The shipped
  default: the legislated freeze to 2030/31 (planning §6), then
  indexation.

The 2030/31 freeze is rUK/reserved legislation: it governs the rUK
schedule, the pension and ISA allowances, and — in both schedules —
the personal allowance and its taper (reserved to Westminster). It
never governs the devolved Scottish band uppers, which Scotland sets
annually (planning §6): those follow the policy's mandatory
``scotland`` sub-table (:class:`ScottishBandsPolicy`), which carries
separate freeze ends for the lower bands (below the higher rate) and
the Higher/Advanced/Top group. The shipped default holds the lower
thresholds only through the last shipped year — Scotland uprates them
in practice, so CPI is the proxy — and the upper group through the
announced 2028/29 commitment.

There is deliberately no index-immediately mode: a legislated freeze
end is a fact (planning §6), and a mode without one could synthesize
years that contradict known legislation. A freeze end at or before the
last shipped year already degrades to pure CPI indexation from that
year, so nothing is lost once the freeze lapses.

Legislated data always beats extrapolation: extension applies only past
the last shipped file and re-bases automatically when a newer file
ships. Modelling conventions (planning §5.3): indexation scales the
money figures of the income-tax schedules and the pension and ISA
allowances, quantized to whole pounds with ``ROUND_HALF_EVEN`` (the
core rounding mode); band and taper *rates* never extrapolate. A
target year's figures compound once from the base file, so they do
not depend on intermediate synthesized years.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, NoReturn

from glidepath.core import Money
from glidepath.regions.uk.schema import (
    DataFileError,
    DividendRules,
    IncomeTaxSchedule,
    IsaRules,
    PensionRules,
    SavingsRules,
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
_SCOTLAND_CONTEXT = f"{_CONTEXT}.scotland"
_POUND = Decimal(1)

HIGHER_BAND_NAME = "higher"
"""The Scottish higher-rate band: anchor of the upper-threshold group.

The Scottish Government's threshold commitments split at this band —
lower thresholds uprate annually while Higher/Advanced/Top freeze
(planning §6) — so Scottish extrapolation needs it present to group
the band uppers.
"""


def _fail(context: str, problem: str) -> NoReturn:
    """Raise a :class:`DataFileError` locating ``problem`` at ``context``."""
    msg = f"{context}: {problem}"
    raise DataFileError(msg)


class FutureYearsMode(StrEnum):
    """How the last shipped tax year extrapolates (planning §5.3, §7)."""

    FROZEN = "frozen"
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


def _freeze_end(raw: object, context: str) -> int:
    """Parse a ``'YYYY/YY'`` freeze-end label into its start year."""
    if not isinstance(raw, str):
        _fail(context, f"expected a 'YYYY/YY' string, got {type(raw).__name__}")
    return parse_tax_year_label(raw, context)


def _indexation_steps(
    *, base_start_year: int, target_start_year: int, frozen_until_start_year: int
) -> int:
    """Years of CPI compounding the target year gets over the base year.

    Compounding starts after whichever is later of the base year (the
    last shipped, legislated file) and the freeze end — shipped data
    always beats extrapolation.
    """
    return max(0, target_start_year - max(base_start_year, frozen_until_start_year))


@dataclass(frozen=True, slots=True)
class ScottishBandsPolicy:
    """Devolved Scottish band-threshold extrapolation (planning §5.3, §6).

    Scotland sets its income-tax thresholds annually, so the reserved
    rUK freeze never governs them. The lower bands (below the higher
    rate) and the Higher/Advanced/Top group each carry their own
    freeze end.
    """

    lower_frozen_until_start_year: int
    """Start year of the last tax year the lower-band uppers hold."""
    upper_frozen_until_start_year: int
    """Start year of the last tax year the higher+ band uppers hold."""

    @classmethod
    def from_assumption_value(cls, value: object) -> ScottishBandsPolicy:
        """Parse the policy's ``scotland`` sub-table."""
        if not isinstance(value, Mapping):
            _fail(
                _SCOTLAND_CONTEXT,
                f"expected a table value, got {type(value).__name__}",
            )
        entries = dict(value)
        lower = _scotland_freeze_end(entries, "lower_bands_frozen_until_tax_year")
        upper = _scotland_freeze_end(entries, "upper_bands_frozen_until_tax_year")
        if entries:
            _fail(_SCOTLAND_CONTEXT, f"unknown keys: {', '.join(sorted(entries))}")
        return cls(
            lower_frozen_until_start_year=lower,
            upper_frozen_until_start_year=upper,
        )


def _scotland_freeze_end(entries: dict[str, object], key: str) -> int:
    """Take one required freeze-end label off the ``scotland`` sub-table."""
    raw = entries.pop(key, None)
    if raw is None:
        _fail(_SCOTLAND_CONTEXT, f"missing required key {key!r}")
    return _freeze_end(raw, f"{_SCOTLAND_CONTEXT}.{key}")


@dataclass(frozen=True, slots=True)
class FutureYearsPolicy:
    """The parsed ``policy.tax.future_years`` assumption value."""

    mode: FutureYearsMode
    frozen_until_start_year: int | None = None
    """Start year of the last frozen tax year (``frozen_then_cpi_indexed``)."""
    scotland: ScottishBandsPolicy | None = None
    """Devolved Scottish band policy (``frozen_then_cpi_indexed`` only)."""

    def __post_init__(self) -> None:
        """Require a real mode and the sub-fields exactly when used.

        The mode must be an actual :class:`FutureYearsMode` member: a
        bare string would pass the ``is`` identity checks below as
        neither mode and silently index a "frozen" policy. The
        ``scotland`` table is mandatory with ``frozen_then_cpi_indexed``
        so the reserved freeze can never silently govern the devolved
        Scottish band uppers (module docstring).
        """
        if not isinstance(self.mode, FutureYearsMode):
            type_name = type(self.mode).__name__
            _fail(_CONTEXT, f"mode must be a FutureYearsMode member, got {type_name}")
        needs_until = self.mode is FutureYearsMode.FROZEN_THEN_CPI_INDEXED
        if needs_until and self.frozen_until_start_year is None:
            _fail(_CONTEXT, f"mode {self.mode} requires frozen_until_tax_year")
        if not needs_until and self.frozen_until_start_year is not None:
            _fail(_CONTEXT, f"mode {self.mode} does not take frozen_until_tax_year")
        if needs_until and self.scotland is None:
            _fail(_CONTEXT, f"mode {self.mode} requires a scotland table")
        if not needs_until and self.scotland is not None:
            _fail(_CONTEXT, f"mode {self.mode} does not take a scotland table")

    @classmethod
    def from_assumption_value(cls, value: AssumptionValue) -> FutureYearsPolicy:
        """Parse the assumption's table value into a typed policy."""
        if not isinstance(value, Mapping):
            _fail(_CONTEXT, f"expected a table value, got {type(value).__name__}")
        entries = dict(value)
        mode = _parse_mode(entries.pop("mode", None))
        frozen_raw = entries.pop("frozen_until_tax_year", None)
        scotland_raw = entries.pop("scotland", None)
        if entries:
            _fail(_CONTEXT, f"unknown keys: {', '.join(sorted(entries))}")
        frozen_until: int | None = None
        if frozen_raw is not None:
            frozen_until = _freeze_end(frozen_raw, f"{_CONTEXT}.frozen_until_tax_year")
        scotland: ScottishBandsPolicy | None = None
        if scotland_raw is not None:
            scotland = ScottishBandsPolicy.from_assumption_value(scotland_raw)
        return cls(mode=mode, frozen_until_start_year=frozen_until, scotland=scotland)

    def indexation_steps(self, *, base_start_year: int, target_start_year: int) -> int:
        """CPI steps for the reserved/rUK figures (module docstring)."""
        frozen_until = self.frozen_until_start_year
        if frozen_until is None:  # __post_init__ invariant: the mode is FROZEN
            return 0
        return _indexation_steps(
            base_start_year=base_start_year,
            target_start_year=target_start_year,
            frozen_until_start_year=frozen_until,
        )


def _require_indexable_cpi(cpi: Rate, context: str) -> None:
    """Reject a CPI at or below -100%: growth factors must stay positive.

    A non-positive growth factor collapses or sign-flips every money
    figure; with an even step count the output would even look valid.
    """
    if cpi.growth_factor <= 0:
        _fail(context, "CPI must be greater than -100%")


@dataclass(frozen=True, slots=True)
class FutureYearsExtension:
    """Everything :class:`UkTaxSystem` needs to extend past shipped data."""

    policy: FutureYearsPolicy
    cpi: Rate
    """Assumed annual CPI (the ``inflation.cpi`` assumption)."""

    def __post_init__(self) -> None:
        """Validate the CPI at construction time."""
        _require_indexable_cpi(self.cpi, "FutureYearsExtension.cpi")


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


def _scottish_split_index(bands: tuple[TaxBand, ...]) -> int:
    """The index of the higher band — where the upper group starts."""
    for index, band in enumerate(bands):
        if band.name == HIGHER_BAND_NAME:
            return index
    _fail(
        "income_tax.scotland",
        f"no band named {HIGHER_BAND_NAME!r} to anchor the upper-threshold group",
    )


def _indexed_scottish_schedule(
    schedule: IncomeTaxSchedule,
    *,
    reserved_factor: Decimal,
    lower_factor: Decimal,
    upper_factor: Decimal,
) -> IncomeTaxSchedule:
    """Index the Scottish schedule per its devolved band groups (§5.3).

    The personal allowance and its taper are reserved to Westminster,
    so they scale by the UK-wide policy factor; band uppers below the
    higher band take the lower-group factor, the higher band and above
    the upper-group factor.
    """
    split = _scottish_split_index(schedule.bands)
    return IncomeTaxSchedule(
        personal_allowance=_indexed_money(schedule.personal_allowance, reserved_factor),
        pa_taper_threshold=_indexed_money(schedule.pa_taper_threshold, reserved_factor),
        pa_taper_rate=schedule.pa_taper_rate,
        bands=tuple(
            _indexed_band(band, lower_factor if index < split else upper_factor)
            for index, band in enumerate(schedule.bands)
        ),
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
        aa_carry_forward_years=pension.aa_carry_forward_years,
        member_relief_basic_amount=_indexed_money(
            pension.member_relief_basic_amount, factor
        ),
        member_relief_max_age=pension.member_relief_max_age,
        relief_at_source_rate=pension.relief_at_source_rate,
        tax_free_lump_sum_fraction=pension.tax_free_lump_sum_fraction,
        lump_sum_allowance=_indexed_money(pension.lump_sum_allowance, factor),
        lump_sum_death_benefit_allowance=_indexed_money(
            pension.lump_sum_death_benefit_allowance, factor
        ),
        db_valuation_factor=pension.db_valuation_factor,
    )


def _indexed_isa(isa: IsaRules, factor: Decimal) -> IsaRules:
    """Index the ISA allowances; the LISA bonus and charge are unchanged."""
    return IsaRules(
        annual_allowance=_indexed_money(isa.annual_allowance, factor),
        lisa_allowance=_indexed_money(isa.lisa_allowance, factor),
        lisa_bonus_rate=isa.lisa_bonus_rate,
        lisa_withdrawal_charge=isa.lisa_withdrawal_charge,
    )


def _indexed_savings(savings: SavingsRules, factor: Decimal) -> SavingsRules:
    """Index the savings nil-rate amounts (reserved figures, §5.3).

    The starting-rate limit is legislated frozen with the rUK schedule
    (planning §6), and the PSA amounts follow the same reserved policy;
    a zero tier (the additional-rate PSA) stays zero under any factor.
    """
    return SavingsRules(
        starting_rate_limit=_indexed_money(savings.starting_rate_limit, factor),
        psa_basic=_indexed_money(savings.psa_basic, factor),
        psa_higher=_indexed_money(savings.psa_higher, factor),
        psa_additional=_indexed_money(savings.psa_additional, factor),
    )


def _indexed_dividend(dividend: DividendRules, factor: Decimal) -> DividendRules:
    """Index the dividend allowance; the rates never extrapolate."""
    return DividendRules(
        allowance=_indexed_money(dividend.allowance, factor),
        rates=dividend.rates,
    )


@lru_cache(maxsize=256)
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

    The function is pure over immutable inputs, so it is memoized:
    every projection date past the last shipped year resolves through
    here (:meth:`~glidepath.regions.uk.years.TaxYearSeries.year_containing`),
    which made re-synthesis the dominant cost of a Monte Carlo run
    (planning §5.2). The cache bound comfortably holds a horizon's worth
    of years for several concurrent policy/CPI variants (scenario
    overrides build their own regions).

    Raises:
        ValueError: If ``target_start_year`` is not after the base year.
        DataFileError: If ``cpi`` is at or below -100%.
    """
    _require_indexable_cpi(cpi, "extend_tax_year.cpi")
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
    scotland_policy = policy.scotland
    if scotland_policy is None:
        # __post_init__ invariant: the mode is FROZEN (steps is 0) —
        # every figure carries forward however distant the target.
        return TaxYearFile(
            schema_version=base.schema_version,
            meta=meta,
            income_tax_ruk=base.income_tax_ruk,
            income_tax_scotland=base.income_tax_scotland,
            pension=base.pension,
            isa=base.isa,
            savings=base.savings,
            dividend=base.dividend,
        )
    factor = cpi.growth_factor**steps
    lower_steps = _indexation_steps(
        base_start_year=base_start_year,
        target_start_year=target_start_year,
        frozen_until_start_year=scotland_policy.lower_frozen_until_start_year,
    )
    upper_steps = _indexation_steps(
        base_start_year=base_start_year,
        target_start_year=target_start_year,
        frozen_until_start_year=scotland_policy.upper_frozen_until_start_year,
    )
    scotland = (
        base.income_tax_scotland
        if steps == 0 and lower_steps == 0 and upper_steps == 0
        else _indexed_scottish_schedule(
            base.income_tax_scotland,
            reserved_factor=factor,
            lower_factor=cpi.growth_factor**lower_steps,
            upper_factor=cpi.growth_factor**upper_steps,
        )
    )
    return TaxYearFile(
        schema_version=base.schema_version,
        meta=meta,
        income_tax_ruk=(
            base.income_tax_ruk
            if steps == 0
            else _indexed_schedule(base.income_tax_ruk, factor)
        ),
        income_tax_scotland=scotland,
        pension=base.pension if steps == 0 else _indexed_pension(base.pension, factor),
        isa=base.isa if steps == 0 else _indexed_isa(base.isa, factor),
        savings=(
            base.savings if steps == 0 else _indexed_savings(base.savings, factor)
        ),
        dividend=(
            base.dividend if steps == 0 else _indexed_dividend(base.dividend, factor)
        ),
    )
