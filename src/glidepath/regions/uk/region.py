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
    StatePensionUprating,
    decimal_assumption_value,
)
from glidepath.regions.uk.ages import UkAgeRules
from glidepath.regions.uk.contributions import UkContributionRuleset
from glidepath.regions.uk.extension import FutureYearsExtension, FutureYearsPolicy
from glidepath.regions.uk.loader import (
    AGE_RULES_FILENAME,
    ASSUMPTIONS_FILENAME,
    available_tax_years,
    data_file_digest,
    load_age_rules,
    load_default_assumptions,
    load_tax_year,
    tax_year_filename,
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


def state_pension_uprating(assumptions: AssumptionSet) -> StatePensionUprating:
    """The parsed ``policy.state_pension.uprating`` value (planning §7).

    Read at region-build time like :func:`future_years_extension`: the
    state pension scheme uses it to step the shipped rate forward to a
    run start past the last shipped file, and its effect is identified
    through the region data version rather than the run's assumption
    read list (module docstring).
    """
    return StatePensionUprating.from_assumption_value(
        assumptions.get(AssumptionKey.POLICY_STATE_PENSION_UPRATING).value
    )


def uk_region(
    future_years: FutureYearsExtension | None = None,
    uprating: StatePensionUprating | None = None,
) -> Region:
    """The UK region bundle over every shipped data file (planning §4.2).

    Without ``future_years`` the bundle answers only for the shipped
    tax years and fails loudly past them; pass
    :func:`future_years_extension` to project beyond the last shipped
    file (planning §5.3). ``uprating`` (see
    :func:`state_pension_uprating`) is likewise only needed to start a
    run past the last shipped file: it steps the shipped state pension
    rate forward to ``today``, since the extension deliberately never
    uprates that rate.
    """
    return Region(
        calendar=AnnualCalendar(
            anchor_month=_TAX_YEAR_ANCHOR_MONTH, anchor_day=_TAX_YEAR_ANCHOR_DAY
        ),
        ages=UkAgeRules.from_shipped_data(),
        tax=UkTaxSystem.from_shipped_data(future_years),
        wrappers=UkWrapperRuleset.from_shipped_data(future_years),
        contributions=UkContributionRuleset.from_shipped_data(future_years),
        state_pension=UkStatePensionScheme.from_shipped_data(future_years, uprating),
        data_version=_data_version(future_years, uprating),
    )


def _data_version(
    future_years: FutureYearsExtension | None,
    uprating: StatePensionUprating | None = None,
) -> str:
    """A deterministic content-version string over the shipped files.

    Names every file with its ``verified_on`` date and a short content
    digest — the date alone cannot tell two same-day revisions apart
    (planning §4.6) — plus the full future-years policy (mode, freeze
    end, CPI) and the state pension uprating policy when configured:
    both are region-build inputs whose effect must be identifiable
    from the version string, not the assumption read list.
    """
    parts = [f"uk schema={SCHEMA_VERSION}"]
    for start_year in available_tax_years():
        year = load_tax_year(start_year)
        digest = data_file_digest(tax_year_filename(start_year))
        parts.append(
            f"tax_year {year.meta.tax_year} verified {year.meta.verified_on}"
            f" sha256={digest}"
        )
    ages = load_age_rules()
    ages_digest = data_file_digest(AGE_RULES_FILENAME)
    parts.append(f"age_rules verified {ages.meta.verified_on} sha256={ages_digest}")
    assumptions = load_default_assumptions()
    assumptions_digest = data_file_digest(ASSUMPTIONS_FILENAME)
    parts.append(
        f"assumptions verified {assumptions.meta.verified_on}"
        f" sha256={assumptions_digest}"
    )
    if future_years is not None:
        detail = future_years.policy.mode.value
        if future_years.policy.frozen_until_start_year is not None:
            detail += f" until={future_years.policy.frozen_until_start_year}"
        parts.append(f"future_years {detail} cpi={future_years.cpi.value}")
    if uprating is not None:
        detail = uprating.rule.name.lower()
        if uprating.floor is not None:
            detail += f" floor={uprating.floor}"
        if uprating.cpi_margin is not None:
            detail += f" margin={uprating.cpi_margin}"
        parts.append(f"state_pension_uprating {detail}")
    return "; ".join(parts)
