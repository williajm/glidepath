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
  the increment (+1% per 9 whole weeks deferred, shipped as data);
  increments uprate by CPI only, so they land in the entitlement's
  CPI slice alongside any protected payment (planning §6).

Amounts are returned in the weekly rates of the tax year containing
the run's ``today`` (annualised at 52 weeks); uprating beyond that is
the engine's concern, governed by the ``policy.state_pension.uprating``
assumption. Past the last shipped file the rate is carried forward
untouched by the future-years machinery (planning §5.3) — uprating is
never the tax extension's job.
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
from glidepath.regions.uk.years import TaxYearSeries

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import StatePensionRecord
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import StatePensionRules

_ZERO = Money(Decimal(0))
_WEEKS_PER_YEAR = Decimal(52)
_DAYS_PER_WEEK = 7


class UkStatePensionError(ValueError):
    """A state pension record the shipped UK rules cannot evaluate."""


@dataclass(frozen=True, slots=True)
class UkStatePensionScheme:
    """UK implementation of the core ``StatePensionScheme`` protocol.

    Holds the age rules (SPA timetable, deferral increment, new-system
    start date) and the tax-year series the current full rate is read
    from.
    """

    ages: UkAgeRules
    years: TaxYearSeries

    @classmethod
    def from_shipped_data(
        cls, future_years: FutureYearsExtension | None = None
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
        )

    def entitlement(
        self, record: StatePensionRecord, date_of_birth: date, today: date
    ) -> StatePensionEntitlement:
        """The record's entitlement in the rates current at ``today``.

        Raises:
            UkStatePensionError: If the record needs the derivation but
                the NI record is missing or pre-2016, or states neither
                a forecast nor qualifying years.
            UkAgeError: If the date of birth predates SPA timetable
                coverage.
            UkTaxYearError: If no shipped or synthesized tax-year data
                covers ``today``.
        """
        spa_date = self.ages.state_pension_date(date_of_birth)
        start_date = add_months(spa_date, deferral_months(record))
        main_weekly, protected_weekly = self._weekly_amounts(record, today)
        increment_weekly = (main_weekly + protected_weekly) * self._deferral_uplift(
            spa_date, start_date
        )
        return StatePensionEntitlement(
            start_date=start_date,
            annual_amount=main_weekly * _WEEKS_PER_YEAR,
            cpi_uprated_annual_amount=(protected_weekly + increment_weekly)
            * _WEEKS_PER_YEAR,
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
        return rules.new_full_weekly * (
            Decimal(years) / Decimal(rules.qualifying_years_full)
        )

    def _rules_at(self, today: date) -> StatePensionRules:
        """The state pension rules of the tax year containing ``today``."""
        return self.years.year_containing(today).state_pension

    def _deferral_uplift(self, spa_date: date, start_date: date) -> Decimal:
        """The deferral increment rate earned between SPA and start.

        +1% per 9 whole weeks deferred (shipped as data): whole weeks
        from the exact dates, floored to completed increments.
        """
        deferral = self.ages.rules.deferral
        weeks = (start_date - spa_date).days // _DAYS_PER_WEEK
        increments = weeks // deferral.per_weeks
        return deferral.increment_rate.value * increments
