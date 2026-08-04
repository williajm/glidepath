"""UK age rules (roadmap 2.4; planning §4.1, §4.2, §6).

Implements the core :class:`~glidepath.core.AgeRules` protocol plus the
UK-only LISA age gates. Every figure — the SPA timetable, the NMPA
schedule, the LISA ages — comes from ``age_rules.toml`` (§5.3); nothing
is hardcoded here (guard-tested).

The three §4.1 conventions appear as three shapes here:

- **Access gates** (NMPA, LISA access at 60) are booleans per period,
  open only if the age is attained on or before the period's first day.
- **Income entitlements** (SPA) are exact dates for the core to
  pro-rate.
- **Eligibility windows** (LISA opening 18-39, contributions to 50) are
  exact date spans — never rounded to periods in either direction; the
  consumer (the wrapper ruleset, roadmap 3.1/9.2) intersects them with
  the period and pro-rates any flow by whole months.

The NMPA schedule is effective-dated, and the age in force is read on
the period's first day. Around a legislated step-up this correctly
denies *new* access to the caught cohort — old enough under the
outgoing age but not the incoming one — until they attain the new age;
benefits already in payment are never re-gated (planning §5.1).
Protected pension ages are out of scope for v1 (§6).
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from glidepath.core import (
    Period,
    add_months,
    date_age_attained,
    is_age_attained_by_period_start,
    period_active_fraction,
)
from glidepath.regions.uk.loader import load_age_rules
from glidepath.regions.uk.schema import SpaAgeBand

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from glidepath.regions.uk.schema import AgeRulesFile, SpaBand


class UkAgeError(ValueError):
    """An age query the shipped UK data cannot answer."""


@dataclass(frozen=True, slots=True)
class UkAgeRules:
    """UK implementation of the core ``AgeRules`` protocol.

    Holds one validated :class:`AgeRulesFile`; every answer is a pure
    function of that file and the query.
    """

    rules: AgeRulesFile

    @classmethod
    def from_shipped_data(cls) -> UkAgeRules:
        """Build the rules over the shipped ``age_rules.toml``."""
        return cls(rules=load_age_rules())

    def state_pension_date(self, date_of_birth: date) -> date:
        """The exact date this date of birth reaches state pension age.

        Age-based timetable bands add whole years then whole months to
        the (leap-day-deemed) birthday, clamping to the target month's
        end; date-based bands reach SPA on their legislated date.

        Raises:
            UkAgeError: If the date of birth predates the timetable's
                coverage (earlier cohorts had phased, pre-2016-system
                SPAs this forward-looking planner does not model).
        """
        band = self._spa_band_for(date_of_birth)
        if isinstance(band, SpaAgeBand):
            return add_months(date_age_attained(date_of_birth, band.years), band.months)
        return band.reaches_on

    def _spa_band_for(self, date_of_birth: date) -> SpaBand:
        """The SPA timetable band containing ``date_of_birth``."""
        bands = self.rules.spa_bands
        first_covered = bands[0].dob_from
        if first_covered is not None and date_of_birth < first_covered:
            msg = (
                f"date of birth {date_of_birth} predates SPA timetable"
                f" coverage (which starts {first_covered})"
            )
            raise UkAgeError(msg)
        *bounded, open_ended = bands
        for band in bounded:
            if band.dob_to is not None and date_of_birth <= band.dob_to:
                return band
        return open_ended

    def normal_minimum_pension_age(self, on: date) -> int:
        """The normal minimum pension age in force on ``on``."""
        baseline, *dated = self.rules.nmpa
        age = baseline.age
        for step in dated:
            if step.effective_from is not None and step.effective_from <= on:
                age = step.age
        return age

    def is_pension_access_open(self, date_of_birth: date, period: Period) -> bool:
        """Whether new pension access is open for ``period`` (§4.1).

        The NMPA in force on the period's first day must be attained on
        or before that day. Only *new* crystallisations are gated here;
        benefits already in payment continue regardless (planning §5.1).
        """
        age = self.normal_minimum_pension_age(period.start)
        return is_age_attained_by_period_start(date_of_birth, age, period)

    def lisa_opening_window(self, date_of_birth: date) -> Period:
        """The exact dates a LISA may be opened (§4.1 eligibility window).

        Runs from the opening birthday to the day before the birthday
        after the last eligible age. Consumers intersect this with the
        engine period; a mid-period opening birthday makes the rest of
        that period eligible.
        """
        lisa = self.rules.lisa
        return _age_window(date_of_birth, lisa.open_age_min, lisa.open_age_max + 1)

    def lisa_contribution_window(self, date_of_birth: date) -> Period:
        """The exact dates LISA contributions may be made (§4.1 window).

        The age window only — holding an open LISA is the wrapper's
        concern (roadmap 9.2). It runs from the opening birthday (a
        contributor must be old enough to hold an account) to the eve
        of the closing birthday; contribution flows crossing either
        edge are pro-rated by the consumer like other partial years.
        """
        lisa = self.rules.lisa
        return _age_window(date_of_birth, lisa.open_age_min, lisa.contribute_until_age)

    def is_lisa_access_open(self, date_of_birth: date, period: Period) -> bool:
        """Whether charge-free LISA access is open for ``period`` (§4.1 gate).

        The age gate only; the other charge-free events (first home,
        terminal illness, death) are out of scope for v1 (§6).
        """
        return is_age_attained_by_period_start(
            date_of_birth, self.rules.lisa.access_age, period
        )

    def lisa_contribution_fraction(
        self, date_of_birth: date, period: Period
    ) -> Decimal:
        """The whole-month share of ``period`` open to LISA contributions.

        Intersects the contribution window with the period per the
        module's eligibility-window convention: flows crossing either
        edge pro-rate by whole months, and an overlap under one whole
        month is zero.
        """
        window = self.lisa_contribution_window(date_of_birth)
        return period_active_fraction(period, window.start, window.end)


def _age_window(date_of_birth: date, opens_at: int, closes_at: int) -> Period:
    """The inclusive span from one birthday to the eve of a later one."""
    return Period(
        start=date_age_attained(date_of_birth, opens_at),
        end=date_age_attained(date_of_birth, closes_at) - timedelta(days=1),
    )
