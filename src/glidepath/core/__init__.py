"""Region-agnostic core primitives (roadmap Phase 1).

The core defines value types, calendars, provenance kinds and entities.
It never imports region code (planning §4.2) — enforced by a guard test —
and never contains policy figures, which live in region data files.
"""

from glidepath.core.contributions import (
    ContributionRuleset,
    ContributionSchedule,
    MemberContributionOutcome,
)
from glidepath.core.entities import (
    EntityId,
    Household,
    Person,
    Sex,
    TaxResidencyId,
    new_entity_id,
    validate_household_v1,
)
from glidepath.core.money import Money, Rate
from glidepath.core.periods import (
    AgeRules,
    AnnualCalendar,
    FiscalCalendar,
    Period,
    add_months,
    age_on,
    birthday_in_year,
    date_age_attained,
    is_age_attained_by_period_start,
    prorata_fraction,
    whole_months_between,
)
from glidepath.core.provenance import (
    Assumption,
    AssumptionKey,
    AssumptionReadRecorder,
    AssumptionSet,
    Decision,
    Fact,
    Provenance,
    TrackedAssumptions,
)
from glidepath.core.tax import TaxInput, TaxLine, TaxResult, TaxSystem
from glidepath.core.wrappers import (
    ContributionTaxTreatment,
    GrowthTaxTreatment,
    ReliefMechanic,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperRuleset,
    WrapperTaxTreatment,
)

__all__ = [
    "AgeRules",
    "AnnualCalendar",
    "Assumption",
    "AssumptionKey",
    "AssumptionReadRecorder",
    "AssumptionSet",
    "ContributionRuleset",
    "ContributionSchedule",
    "ContributionTaxTreatment",
    "Decision",
    "EntityId",
    "Fact",
    "FiscalCalendar",
    "GrowthTaxTreatment",
    "Household",
    "MemberContributionOutcome",
    "Money",
    "Period",
    "Person",
    "Provenance",
    "Rate",
    "ReliefMechanic",
    "Sex",
    "TaxInput",
    "TaxLine",
    "TaxResidencyId",
    "TaxResult",
    "TaxSystem",
    "TrackedAssumptions",
    "WithdrawalTaxTreatment",
    "Wrapper",
    "WrapperKindId",
    "WrapperRuleset",
    "WrapperTaxTreatment",
    "add_months",
    "age_on",
    "birthday_in_year",
    "date_age_attained",
    "is_age_attained_by_period_start",
    "new_entity_id",
    "prorata_fraction",
    "validate_household_v1",
    "whole_months_between",
]
