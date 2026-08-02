"""UK age rules (roadmap 2.4; planning §4.1, §4.2, §6).

Implements the core :class:`~glidepath.core.AgeRules` protocol plus the
UK-only LISA age gates. Every figure — the SPA timetable, the NMPA
schedule, the LISA ages — comes from ``age_rules.toml`` (§5.3); nothing
is hardcoded here (guard-tested).

Every gate follows the §4.1 convention, evaluated on a period's first
day: an opening gate (NMPA, LISA access) is open for a period only if
the age is attained on or before that day, and a closing window (LISA
opening, LISA contributions) is shut for a period once its closing age
is attained by that day — so a mid-period closing birthday leaves the
window open for that whole period.

The NMPA schedule is effective-dated, and the age in force is read on
the period's first day. Around a legislated step-up this correctly
denies *new* access to the caught cohort — old enough under the
outgoing age but not the incoming one — until they attain the new age;
benefits already in payment are never re-gated (planning §5.1).
Protected pension ages are out of scope for v1 (§6).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from glidepath.core import (
    add_months,
    date_age_attained,
    is_age_attained_by_period_start,
)
from glidepath.regions.uk.loader import load_age_rules
from glidepath.regions.uk.schema import SpaAgeBand

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import Period
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

    def is_lisa_opening_allowed(self, date_of_birth: date, period: Period) -> bool:
        """Whether the LISA opening age window is open for ``period``.

        The window closes on the birthday after the last eligible age.
        """
        lisa = self.rules.lisa
        return _window_open(
            date_of_birth, lisa.open_age_min, lisa.open_age_max + 1, period
        )

    def is_lisa_contribution_allowed(self, date_of_birth: date, period: Period) -> bool:
        """Whether the LISA contribution age window is open for ``period``.

        The age window only — holding an open LISA is the wrapper's
        concern (roadmap 9.2). A contributor must have reached the
        opening age; contributions stop at the closing birthday.
        """
        lisa = self.rules.lisa
        return _window_open(
            date_of_birth, lisa.open_age_min, lisa.contribute_until_age, period
        )

    def is_lisa_access_open(self, date_of_birth: date, period: Period) -> bool:
        """Whether charge-free LISA access is open for ``period``.

        The age gate only; the other charge-free events (first home,
        terminal illness, death) are out of scope for v1 (§6).
        """
        return is_age_attained_by_period_start(
            date_of_birth, self.rules.lisa.access_age, period
        )


def _window_open(
    date_of_birth: date, opens_at: int, closes_at: int, period: Period
) -> bool:
    """§4.1 on both edges: attained the opening age, not yet the closing one."""
    return is_age_attained_by_period_start(
        date_of_birth, opens_at, period
    ) and not is_age_attained_by_period_start(date_of_birth, closes_at, period)
