"""The UK region bundle and shipped assumption set (roadmap 4.1; planning §4.2).

Builds the :class:`~glidepath.core.Region` the engine runs against:
the UK fiscal calendar (tax years, 6 April to 5 April), the age, tax,
wrapper, and contribution rulesets over the shipped data files, and a
content-version string naming each file with its ``verified_on`` date —
the region part of the run manifest (planning §4.6).

Also converts the shipped ``assumptions_default.toml`` into the core
:class:`~glidepath.core.AssumptionSet` (every entry
``DEFAULT_ASSUMPTION``-provenanced, carrying its basis), and derives
the future-years extension from the ``policy.tax.future_years`` and
``inflation.cpi`` assumptions. Both reads happen at region-build time —
before the run's read recorder exists — so the extension's effect on a
result is identified through the region data version, not the
assumption read list.
"""

from datetime import UTC, datetime, time

from glidepath.core import (
    AnnualCalendar,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    Provenance,
    Rate,
    Region,
    decimal_assumption_value,
)
from glidepath.regions.uk.ages import UkAgeRules
from glidepath.regions.uk.contributions import UkContributionRuleset
from glidepath.regions.uk.extension import FutureYearsExtension, FutureYearsPolicy
from glidepath.regions.uk.loader import (
    available_tax_years,
    load_age_rules,
    load_default_assumptions,
    load_tax_year,
)
from glidepath.regions.uk.schema import SCHEMA_VERSION
from glidepath.regions.uk.state_pension import UkStatePensionScheme
from glidepath.regions.uk.tax import UkTaxSystem
from glidepath.regions.uk.wrappers import UkWrapperRuleset

_TAX_YEAR_ANCHOR_MONTH = 4
_TAX_YEAR_ANCHOR_DAY = 6


def default_assumption_set() -> AssumptionSet:
    """The shipped UK defaults as a core assumption set (planning §7).

    Every entry carries ``DEFAULT_ASSUMPTION`` provenance with its
    default value equal to its effective value; overriding one is the
    caller's concern (a new :class:`~glidepath.core.Assumption` with
    ``USER_OVERRIDE`` or ``SCENARIO_OVERRIDE`` provenance in a new
    set). ``recorded_on`` is the data file's ``verified_on`` at
    midnight UTC.
    """
    file = load_default_assumptions()
    recorded_on = datetime.combine(file.meta.verified_on, time.min, tzinfo=UTC)
    return AssumptionSet(
        Assumption(
            key=entry.key,
            value=entry.value,
            default_value=entry.value,
            provenance=Provenance.DEFAULT_ASSUMPTION,
            source=entry.basis,
            recorded_on=recorded_on,
            description=f"Shipped UK default for '{entry.key.value}'",
        )
        for entry in file.defaults
    )


def future_years_extension(assumptions: AssumptionSet) -> FutureYearsExtension:
    """The tax-data extension the ``policy.tax.future_years`` value asks for.

    Reads the policy table and the CPI assumption directly from the
    set (module docstring: region construction happens before the
    run's read recorder exists).
    """
    policy = FutureYearsPolicy.from_assumption_value(
        assumptions.get(AssumptionKey.POLICY_TAX_FUTURE_YEARS).value
    )
    cpi = Rate(decimal_assumption_value(assumptions.get(AssumptionKey.INFLATION_CPI)))
    return FutureYearsExtension(policy=policy, cpi=cpi)


def uk_region(future_years: FutureYearsExtension | None = None) -> Region:
    """The UK region bundle over every shipped data file (planning §4.2).

    Without ``future_years`` the bundle answers only for the shipped
    tax years and fails loudly past them; pass
    :func:`future_years_extension` to project beyond the last shipped
    file (planning §5.3).
    """
    return Region(
        calendar=AnnualCalendar(
            anchor_month=_TAX_YEAR_ANCHOR_MONTH, anchor_day=_TAX_YEAR_ANCHOR_DAY
        ),
        ages=UkAgeRules.from_shipped_data(),
        tax=UkTaxSystem.from_shipped_data(future_years),
        wrappers=UkWrapperRuleset.from_shipped_data(future_years),
        contributions=UkContributionRuleset.from_shipped_data(future_years),
        state_pension=UkStatePensionScheme.from_shipped_data(future_years),
        data_version=_data_version(future_years),
    )


def _data_version(future_years: FutureYearsExtension | None) -> str:
    """A deterministic content-version string over the shipped files.

    Names every file with its ``verified_on`` date, plus the full
    future-years policy (mode, freeze end, CPI) when an extension is
    configured — enough to tell two runs apart whenever the data
    behind them differs (planning §4.6).
    """
    parts = [f"uk schema={SCHEMA_VERSION}"]
    for start_year in available_tax_years():
        year = load_tax_year(start_year)
        parts.append(f"tax_year {year.meta.tax_year} verified {year.meta.verified_on}")
    ages = load_age_rules()
    parts.append(f"age_rules verified {ages.meta.verified_on}")
    assumptions = load_default_assumptions()
    parts.append(f"assumptions verified {assumptions.meta.verified_on}")
    if future_years is not None:
        detail = future_years.policy.mode.value
        if future_years.policy.frozen_until_start_year is not None:
            detail += f" until={future_years.policy.frozen_until_start_year}"
        parts.append(f"future_years {detail} cpi={future_years.cpi.value}")
    return "; ".join(parts)
