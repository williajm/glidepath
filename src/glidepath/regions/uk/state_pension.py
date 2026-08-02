"""UK state pension scheme (roadmap 4.3; planning §5.1, §6).

Implements the core :class:`~glidepath.core.StatePensionScheme`
protocol. Every figure — the full new state pension rate and
qualifying-year rules (tax-year files), the SPA timetable, the deferral
increment and the new-system start date (``age_rules.toml``) — comes
from the shipped data files (§5.3); nothing is hardcoded here
(guard-tested).

The §5.1 rules, in order:

- An official forecast, when present, **is the fact and wins**; any
  protected payment is the CPI-only slice of it (validated by the core
  record).
- Without a forecast, the ÷35 qualifying-years derivation applies —
  but **only to NI records starting on or after the new system began**
  (6 April 2016, shipped as data): earlier records are governed by
  transitional starting-amount rules (old/new comparison,
  contracting-out, possible protected payment) this model does not
  compute, so they are refused with a demand for a forecast, never
  guessed.
- Fewer qualifying years (stated plus planned) than the minimum earns
  no state pension at all; the count is capped at the full-rate count.
- Deferral shifts the start date past SPA in whole months and earns
  the increment: one ninth of 1% for each whole week deferred, payable
  only once at least nine weeks are deferred (~5.8% per 52 weeks;
  parameters shipped as data). The uplift is returned as a *fraction*
  because it applies to the rate payable at claim — upratings earned
  during deferment included — which only the engine knows; the engine
  CPI-uprates the resulting increment from the claim onwards
  (planning §5.1, §6).

Amounts are returned in the weekly rates of the tax year containing
the run's ``today`` (annualised at 52 weeks); uprating beyond that is
the engine's concern, governed by the ``policy.state_pension.uprating``
assumption. Past the last shipped file the future-years machinery
carries the rate forward untouched (planning §5.3) — uprating is never
the tax extension's job — so this scheme steps the derived rate
forward itself, one whole April uprating per tax year from the last
shipped file to ``today``, using the uprating policy it was built
with. A derivation past shipped coverage without that policy fails
loudly rather than answering in stale rates.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from glidepath.core import (
    Money,
    StatePensionEntitlement,
    add_months,
    deferral_months,
)
from glidepath.regions.uk.ages import UkAgeRules
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.schema import tax_year_start_year
from glidepath.regions.uk.years import TaxYearSeries

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import StatePensionRecord, StatePensionUprating
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import StatePensionRules

_ZERO = Money(Decimal(0))
_ZERO_RATE = Decimal(0)
_ONE = Decimal(1)
_WEEKS_PER_YEAR = Decimal(52)
_DAYS_PER_WEEK = 7


class UkStatePensionError(ValueError):
    """A state pension record the shipped UK rules cannot evaluate."""


@dataclass(frozen=True, slots=True)
class UkStatePensionScheme:
    """UK implementation of the core ``StatePensionScheme`` protocol.

    Holds the age rules (SPA timetable, deferral increment, new-system
    start date), the tax-year series the full rate is read from, and —
    when built for projection past shipped data — the uprating policy
    that steps the rate forward to ``today`` (module docstring). Like
    the future-years extension, the policy is read at region-build
    time, so its effect is identified through the region data version
    rather than the run's assumption read list.
    """

    ages: UkAgeRules
    years: TaxYearSeries
    uprating: StatePensionUprating | None = None

    @classmethod
    def from_shipped_data(
        cls,
        future_years: FutureYearsExtension | None = None,
        uprating: StatePensionUprating | None = None,
    ) -> UkStatePensionScheme:
        """Build the scheme over every shipped data file."""
        return cls(
            ages=UkAgeRules.from_shipped_data(),
            years=TaxYearSeries(
                tax_years=tuple(
                    load_tax_year(start_year) for start_year in available_tax_years()
                ),
                future_years=future_years,
            ),
            uprating=uprating,
        )

    def entitlement(
        self, record: StatePensionRecord, date_of_birth: date, today: date
    ) -> StatePensionEntitlement:
        """The record's entitlement in the rates current at ``today``.

        Raises:
            UkStatePensionError: If the record needs the derivation but
                the NI record is missing or pre-2016, states neither a
                forecast nor qualifying years, or falls past shipped
                rate coverage without an uprating policy to step the
                rate forward.
            UkAgeError: If the date of birth predates SPA timetable
                coverage.
            UkTaxYearError: If no shipped or synthesized tax-year data
                covers ``today``.
        """
        spa_date = self.ages.state_pension_date(date_of_birth)
        start_date = add_months(spa_date, deferral_months(record))
        main_weekly, protected_weekly = self._weekly_amounts(record, today)
        return StatePensionEntitlement(
            start_date=start_date,
            annual_amount=main_weekly * _WEEKS_PER_YEAR,
            cpi_uprated_annual_amount=protected_weekly * _WEEKS_PER_YEAR,
            deferral_uplift=self._deferral_uplift(spa_date, start_date),
        )

    def _weekly_amounts(
        self, record: StatePensionRecord, today: date
    ) -> tuple[Money, Money]:
        """The (policy-uprated, CPI-only) weekly amounts before deferral.

        The forecast wins when present (planning §5.1); otherwise the
        qualifying-years derivation runs under the module-docstring
        gates.
        """
        if record.forecast_weekly_amount is not None:
            protected = _ZERO
            if record.protected_payment is not None:
                protected = record.protected_payment.value
            return record.forecast_weekly_amount.value - protected, protected
        return self._derived_weekly_amount(record, today), _ZERO

    def _derived_weekly_amount(self, record: StatePensionRecord, today: date) -> Money:
        """The ÷35 derivation over the current tax year's full rate."""
        system_start = self.ages.rules.new_state_pension.system_start
        if record.ni_record_start is None:
            msg = (
                "the qualifying-years derivation needs the NI record start"
                " date to prove a post-2016 record; state it or provide an"
                " official forecast (planning §5.1)"
            )
            raise UkStatePensionError(msg)
        if record.ni_record_start.value < system_start:
            msg = (
                f"NI record started {record.ni_record_start.value}, before"
                f" the new state pension began {system_start}: transitional"
                " starting-amount rules apply that this model does not"
                " compute — an official forecast is required (planning §5.1)"
            )
            raise UkStatePensionError(msg)
        if record.qualifying_years is None:
            msg = (
                "a state pension record needs an official forecast or a"
                " qualifying-years count (planning §5.1)"
            )
            raise UkStatePensionError(msg)
        rules = self._rules_at(today)
        years = min(
            record.qualifying_years.value + record.planned_extra_years.value,
            rules.qualifying_years_full,
        )
        if years < rules.qualifying_years_min:
            return _ZERO
        derived = rules.new_full_weekly * (
            Decimal(years) / Decimal(rules.qualifying_years_full)
        )
        return derived * self._rate_step_factor(today)

    def _rules_at(self, today: date) -> StatePensionRules:
        """The state pension rules of the tax year containing ``today``."""
        return self.years.year_containing(today).state_pension

    def _rate_step_factor(self, today: date) -> Decimal:
        """Whole April upratings from the last shipped rate to ``today``.

        The future-years extension carries the state pension rate
        forward untouched (planning §5.3), so a run starting past the
        last shipped file would otherwise answer in stale rates: the
        engine's own uprating factors only start at the run. One whole
        annual uprating applies per intervening tax year (module
        docstring), at the policy rate for the assumed CPI.

        Raises:
            UkStatePensionError: If steps are needed but the scheme was
                built without an uprating policy.
        """
        last_shipped = self.years.tax_years[-1]
        steps = tax_year_start_year(today) - tax_year_start_year(
            last_shipped.meta.start_date
        )
        if steps <= 0:
            return _ONE
        future_years = self.years.future_years
        if self.uprating is None or future_years is None:
            msg = (
                f"the shipped state pension rate ends with tax year"
                f" {last_shipped.meta.tax_year}; deriving an entitlement at"
                f" {today} needs the uprating policy to step it forward"
                " (planning §5.1)"
            )
            raise UkStatePensionError(msg)
        annual = self.uprating.annual_rate(future_years.cpi.value)
        return (_ONE + annual) ** steps

    def _deferral_uplift(self, spa_date: date, start_date: date) -> Decimal:
        """The deferral increment fraction earned between SPA and start.

        One ninth of 1% for each whole week deferred — the shipped
        ``increment_rate / per_weeks`` per week — payable only once at
        least ``per_weeks`` whole weeks are deferred (the statutory
        minimum; ~5.8% per 52 weeks, planning §6).
        """
        deferral = self.ages.rules.deferral
        weeks = (start_date - spa_date).days // _DAYS_PER_WEEK
        if weeks < deferral.per_weeks:
            return _ZERO_RATE
        return deferral.increment_rate.value * (
            Decimal(weeks) / Decimal(deferral.per_weeks)
        )
