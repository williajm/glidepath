"""UK state pension scheme (roadmap 4.3; planning §5.1, §6).

Implements the core :class:`~glidepath.core.StatePensionScheme`
protocol. The official DWP forecast is the only *fact* route to an
amount (planning §5.1): it is authoritative, free, and instant to
obtain from gov.uk/check-state-pension, so this scheme never computes
what DWP has already computed. A record without a forecast instead
takes the full new State Pension rate published in the shipped
tax-year files — a labelled default assumption the engine scales and
uprates (planning §4.13), never a derivation from an NI record. The
age figures — the SPA timetable and the deferral increment — come from
the shipped ``age_rules.toml`` (§5.3); nothing is hardcoded here
(guard-tested).

The §5.1 rules, in order:

- The official forecast **is the fact**; any protected payment is the
  CPI-only slice of it (validated by the core record).
- Deferral shifts the start date past SPA in whole months and earns
  the increment: one ninth of 1% for each whole week deferred, payable
  only once at least nine weeks are deferred (~5.8% per 52 weeks;
  parameters shipped as data). The uplift is returned as a *fraction*
  because it applies to the rate payable at claim — upratings earned
  during deferment included — which only the engine knows; the engine
  CPI-uprates the resulting increment from the claim onwards
  (planning §5.1, §6).

Amounts are returned in the weekly rates the forecast states
(annualised at 52 weeks); uprating — from the forecast's own date to
the run start (§4.8) and onward from there — is the engine's concern,
governed by the ``policy.state_pension.uprating`` assumption
(planning §5.1, §7).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from glidepath.core import (
    FullRateEntitlement,
    Money,
    StatePensionEntitlement,
    StatePensionRate,
    add_months,
    deferral_months,
)
from glidepath.regions.uk.ages import UkAgeRules
from glidepath.regions.uk.loader import available_tax_years, load_tax_year

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import StatePensionRecord

_ZERO = Money(Decimal(0))
_ZERO_RATE = Decimal(0)
_WEEKS_PER_YEAR = Decimal(52)
_DAYS_PER_WEEK = 7


class UkStatePensionError(ValueError):
    """A state pension record the shipped UK rules cannot evaluate."""


@dataclass(frozen=True, slots=True)
class UkStatePensionScheme:
    """UK implementation of the core ``StatePensionScheme`` protocol.

    Holds the age rules (SPA timetable, deferral increment) and the
    full rates published in the shipped tax-year files, oldest first —
    read only for a record without a forecast (module docstring).
    """

    ages: UkAgeRules
    full_rates: tuple[StatePensionRate, ...] = ()

    @classmethod
    def from_shipped_data(cls) -> UkStatePensionScheme:
        """Build the scheme over the shipped age-rules and tax-year files."""
        rates = []
        for start_year in available_tax_years():
            year = load_tax_year(start_year)
            rates.append(
                StatePensionRate(
                    weekly=year.state_pension.full_weekly_rate,
                    rates_from=year.meta.start_date,
                    label=year.meta.tax_year,
                )
            )
        return cls(ages=UkAgeRules.from_shipped_data(), full_rates=tuple(rates))

    def full_rate_entitlement(
        self, record: StatePensionRecord, date_of_birth: date, on: date
    ) -> FullRateEntitlement:
        """The full-rate entitlement for a record without a forecast (§4.13).

        The rate is the latest shipped one whose tax year starts on or
        before ``on``; the start date and deferral uplift follow the
        same SPA and deferral rules as a forecast.

        Raises:
            UkStatePensionError: If no shipped tax year starts on or
                before ``on``.
            UkAgeError: If the date of birth predates SPA timetable
                coverage.
        """
        published = [rate for rate in self.full_rates if rate.rates_from <= on]
        if not published:
            msg = (
                f"no shipped full State Pension rate applies on {on};"
                " enter your DWP forecast from gov.uk/check-state-pension"
            )
            raise UkStatePensionError(msg)
        rate = max(published, key=lambda candidate: candidate.rates_from)
        spa_date = self.ages.state_pension_date(date_of_birth)
        start_date = add_months(spa_date, deferral_months(record))
        return FullRateEntitlement(
            entitlement=StatePensionEntitlement(
                start_date=start_date,
                annual_amount=rate.weekly * _WEEKS_PER_YEAR,
                cpi_uprated_annual_amount=_ZERO,
                deferral_uplift=self._deferral_uplift(spa_date, start_date),
            ),
            rate=rate,
        )

    def entitlement(
        self, record: StatePensionRecord, date_of_birth: date
    ) -> StatePensionEntitlement:
        """The record's entitlement in the rates its forecast states.

        Raises:
            UkStatePensionError: If the record has no official forecast
                (such a record takes :meth:`full_rate_entitlement`).
            UkAgeError: If the date of birth predates SPA timetable
                coverage.
        """
        spa_date = self.ages.state_pension_date(date_of_birth)
        start_date = add_months(spa_date, deferral_months(record))
        main_weekly, protected_weekly = self._weekly_amounts(record)
        return StatePensionEntitlement(
            start_date=start_date,
            annual_amount=main_weekly * _WEEKS_PER_YEAR,
            cpi_uprated_annual_amount=protected_weekly * _WEEKS_PER_YEAR,
            deferral_uplift=self._deferral_uplift(spa_date, start_date),
        )

    @staticmethod
    def _weekly_amounts(record: StatePensionRecord) -> tuple[Money, Money]:
        """The (policy-uprated, CPI-only) weekly amounts before deferral.

        Raises:
            UkStatePensionError: If the record has no official forecast.
        """
        if record.forecast_weekly_amount is None:
            msg = (
                "a state pension record without a DWP forecast takes the"
                " full rate (planning §4.13), not a forecast entitlement"
            )
            raise UkStatePensionError(msg)
        protected = _ZERO
        if record.protected_payment is not None:
            protected = record.protected_payment.value
        return record.forecast_weekly_amount.value - protected, protected

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
