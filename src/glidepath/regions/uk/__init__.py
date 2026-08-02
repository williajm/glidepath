"""UK region package (planning §4.2, §5.3).

Implements the core boundary protocols for the UK. Every UK policy
figure — tax bands, allowances, state pension rates, age rules — is
loaded from the TOML data files under ``data/`` (each carrying
``verified_on`` + ``sources``), never hardcoded; a guard test enforces
this. Shipped default assumptions mirror planning §7 and are kept in
sync by a doc-sync test.
"""

from glidepath.regions.uk.ages import UkAgeError, UkAgeRules
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

__all__ = [
    "AGE_RULES_FILENAME",
    "ASSUMPTIONS_FILENAME",
    "RUK_RESIDENCY",
    "SCHEMA_VERSION",
    "SCOTLAND_RESIDENCY",
    "AgeRulesFile",
    "AssumptionDefault",
    "AssumptionValue",
    "AssumptionsFile",
    "DataFileError",
    "FileMeta",
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
    "UkAgeError",
    "UkAgeRules",
    "UkTaxError",
    "UkTaxSystem",
    "available_tax_years",
    "load_age_rules",
    "load_default_assumptions",
    "load_tax_year",
    "parse_age_rules",
    "parse_default_assumptions",
    "parse_tax_year",
    "tax_year_filename",
]
