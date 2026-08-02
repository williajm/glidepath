"""UK region package (planning §4.2, §5.3).

Implements the core boundary protocols for the UK. Every UK policy
figure — tax bands, allowances, state pension rates, age rules — is
loaded from the TOML data files under ``data/`` (each carrying
``verified_on`` + ``sources``), never hardcoded; a guard test enforces
this. Shipped default assumptions mirror planning §7 and are kept in
sync by a doc-sync test.
"""

from glidepath.regions.uk.ages import UkAgeError, UkAgeRules
from glidepath.regions.uk.contributions import (
    AnnualAllowanceAssessment,
    UkContributionError,
    UkContributionRuleset,
    adjusted_income,
    assess_annual_allowance,
    is_mpaa_active,
    tapered_annual_allowance,
    threshold_income,
)
from glidepath.regions.uk.extension import (
    FutureYearsExtension,
    FutureYearsMode,
    FutureYearsPolicy,
    extend_tax_year,
)
from glidepath.regions.uk.loader import (
    AGE_RULES_FILENAME,
    ASSUMPTIONS_FILENAME,
    available_tax_years,
    load_age_rules,
    load_default_assumptions,
    load_tax_year,
    parse_age_rules,
    parse_default_assumptions,
    parse_tax_year,
    tax_year_filename,
)
from glidepath.regions.uk.region import (
    default_assumption_set,
    future_years_extension,
    uk_region,
)
from glidepath.regions.uk.schema import (
    SCHEMA_VERSION,
    AgeRulesFile,
    AssumptionDefault,
    AssumptionsFile,
    AssumptionValue,
    DataFileError,
    FileMeta,
    IncomeTaxSchedule,
    IsaRules,
    LisaAges,
    NmpaStep,
    PensionRules,
    SpaAgeBand,
    SpaBand,
    SpaDateBand,
    StatePensionDeferral,
    StatePensionRules,
    TaxBand,
    TaxYearFile,
    TaxYearMeta,
)
from glidepath.regions.uk.tax import (
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    UkTaxError,
    UkTaxSystem,
)
from glidepath.regions.uk.wrappers import (
    ISA_KIND,
    SIPP_KIND,
    WORKPLACE_DC_KIND,
    UkWrapperError,
    UkWrapperRuleset,
)
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

__all__ = [
    "AGE_RULES_FILENAME",
    "ASSUMPTIONS_FILENAME",
    "ISA_KIND",
    "RUK_RESIDENCY",
    "SCHEMA_VERSION",
    "SCOTLAND_RESIDENCY",
    "SIPP_KIND",
    "WORKPLACE_DC_KIND",
    "AgeRulesFile",
    "AnnualAllowanceAssessment",
    "AssumptionDefault",
    "AssumptionValue",
    "AssumptionsFile",
    "DataFileError",
    "FileMeta",
    "FutureYearsExtension",
    "FutureYearsMode",
    "FutureYearsPolicy",
    "IncomeTaxSchedule",
    "IsaRules",
    "LisaAges",
    "NmpaStep",
    "PensionRules",
    "SpaAgeBand",
    "SpaBand",
    "SpaDateBand",
    "StatePensionDeferral",
    "StatePensionRules",
    "TaxBand",
    "TaxYearFile",
    "TaxYearMeta",
    "TaxYearSeries",
    "UkAgeError",
    "UkAgeRules",
    "UkContributionError",
    "UkContributionRuleset",
    "UkTaxError",
    "UkTaxSystem",
    "UkTaxYearError",
    "UkWrapperError",
    "UkWrapperRuleset",
    "adjusted_income",
    "assess_annual_allowance",
    "available_tax_years",
    "default_assumption_set",
    "extend_tax_year",
    "future_years_extension",
    "is_mpaa_active",
    "load_age_rules",
    "load_default_assumptions",
    "load_tax_year",
    "parse_age_rules",
    "parse_default_assumptions",
    "parse_tax_year",
    "tapered_annual_allowance",
    "tax_year_filename",
    "threshold_income",
    "uk_region",
]
