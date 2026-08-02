"""The region bundle the engine runs against (roadmap 4.1; planning §4.2).

A :class:`Region` gathers one region's implementations of every core
boundary protocol — fiscal calendar, age rules, tax system, wrapper
rules, contribution rules, state pension scheme — plus a
content-version string identifying the data those implementations
answer from. The engine receives exactly
this bundle (planning §5.2: ``run(plan, assumptions, region, config)``)
and never anything region-specific; the version string lands in
``ProjectionResult.provenance`` so a result records which data produced
it (planning §4.6).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from glidepath.core.contributions import ContributionRuleset
    from glidepath.core.periods import AgeRules, FiscalCalendar
    from glidepath.core.state_pension import StatePensionScheme
    from glidepath.core.tax import TaxSystem
    from glidepath.core.wrappers import WrapperRuleset


@dataclass(frozen=True, slots=True)
class Region:
    """One region's boundary-protocol implementations, bundled.

    ``data_version`` identifies the region data content backing the
    bundle (e.g. file names with their ``verified_on`` dates) — part of
    the run manifest reproducibility is defined over (planning §4.6).
    """

    calendar: FiscalCalendar
    ages: AgeRules
    tax: TaxSystem
    wrappers: WrapperRuleset
    contributions: ContributionRuleset
    state_pension: StatePensionScheme
    data_version: str

    def __post_init__(self) -> None:
        """Require a non-empty data version."""
        if not self.data_version:
            msg = "Region.data_version must not be empty"
            raise ValueError(msg)
