# UK region design

> Status: design spec (pre-implementation) · Last updated 2026-08-01 ·
> Back to [planning](../planning.md) · Figures: [assumptions register](../assumptions.md)

`glidepath.regions.uk` implements the core protocols from
[ADR-0002](../adr/0002-core-region-boundary.md). **Every policy figure lives
in per-tax-year data files — never in logic.** Verified current values, with
sources and retrieval dates, are catalogued in
[assumptions.md](../assumptions.md); this doc specifies the rules and the
data format.

## 1. Rules catalogue

What the UK region implements (v1 unless marked otherwise):

| Rule area | Notes |
| --- | --- |
| Income tax | rUK bands + personal allowance taper. Scottish bands *designed for* (data present, logic deferred): Scottish rates apply to non-savings/non-dividend income only. Savings/dividend taxation deferred until GIA wrapper lands. |
| Pension relief | Relief at source (provider tops up basic rate; higher/additional relief via assessment) vs net pay. |
| Annual allowance | Standard AA, taper (threshold/adjusted income), MPAA once flexibly accessed. Carry-forward: deferred (extension). |
| Tax-free cash | 25% of crystallised value, capped by the Lump Sum Allowance; LSDBA tracked for completeness. |
| Access routes | UFPLS (25% of each payment tax-free, rest taxable; triggers MPAA) vs flexi-access drawdown (25% PCLS at crystallisation, income taxable; MPAA on first income draw). |
| ISA / LISA | ISA annual allowance. LISA (extension): bonus, age window, access age, withdrawal charge. |
| DB pensions | Scheme parameters are user-entered **facts** (revaluation basis, NPA, early/late factors, commutation factor) — schemes vary too much to ship as data. |
| State pension | Entitlement from forecast (fact) or qualifying years; SPA from DOB via banded timetable; deferral increments; uprating per assumption (triple lock vs CPI scenarios). |
| Age rules | NMPA (rising to 57 in 2028), SPA bands, LISA access age. |

Each rule's implementation cites its gov.uk source in the data file's
`sources` list; golden tests use worked examples from HMRC pages.

## 2. Data file layout

```
src/glidepath/regions/uk/data/
├── tax_year_2026_27.toml      # per-tax-year figures (one file per year)
├── age_rules.toml             # effective-dated legislative timetables
└── assumptions_default.toml   # shipped default assumptions (machine mirror
                               # of docs/assumptions.md part 2)
```

Loaded via `importlib.resources` + stdlib `tomllib`
([ADR-0005](../adr/0005-persistence-format.md)). Files are read-only at
runtime.

### Conventions (enforced by the loader)

- **Money and rates are TOML strings**, parsed to `Decimal`. A bare TOML
  float in a money/rate position is a load error (TOML floats are binary
  floats).
- Dates are TOML dates.
- Every file has `schema_version` and a `[meta]` table with `verified_on`
  (date figures were checked) and `sources` (list of URLs). Mandatory.
- Strict validation into frozen dataclasses; unknown keys are errors.
- A grep-based guard test asserts no UK policy figure literal exists outside
  `regions/uk/data/`.

### `tax_year_2026_27.toml` (values verified 2026-08-01 where shown; see [assumptions.md](../assumptions.md) for sources)

```toml
schema_version = 1

[meta]
tax_year    = "2026/27"
start_date  = 2026-04-06
end_date    = 2027-04-05
verified_on = 2026-08-01
sources = [
  "https://www.gov.uk/income-tax-rates",
  "https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027",
  "https://www.gov.uk/scottish-income-tax",
]

[income_tax.ruk]
personal_allowance = "12570"
pa_taper_threshold = "100000"     # adjusted net income; PA -£1 per £2 above
pa_taper_rate      = "0.5"
# Band widths are TAXABLE income above the personal allowance, ascending.
bands = [
  { name = "basic",      rate = "0.20", upper = "37700" },
  { name = "higher",     rate = "0.40", upper = "125140" },
  { name = "additional", rate = "0.45" },                  # no upper = unbounded
]

[income_tax.scotland]             # non-savings/non-dividend income only
personal_allowance = "12570"
pa_taper_threshold = "100000"
pa_taper_rate      = "0.5"
bands = [
  { name = "starter",      rate = "0.19", upper = "3967" },
  { name = "basic",        rate = "0.20", upper = "16956" },
  { name = "intermediate", rate = "0.21", upper = "31092" },
  { name = "higher",       rate = "0.42", upper = "62430" },
  { name = "advanced",     rate = "0.45", upper = "125140" },
  { name = "top",          rate = "0.48" },
]

[pension]
annual_allowance           = "60000"      # see assumptions.md for verification
aa_taper_threshold_income  = "200000"
aa_taper_adjusted_income   = "260000"
aa_taper_rate              = "0.5"
aa_taper_floor             = "10000"
mpaa                       = "10000"
relief_at_source_rate      = "0.20"
tax_free_lump_sum_fraction = "0.25"
lump_sum_allowance         = "268275"
lump_sum_death_benefit_allowance = "1073100"

[isa]
annual_allowance = "20000"
lisa_allowance   = "4000"         # counts within the overall ISA allowance
lisa_bonus_rate  = "0.25"
lisa_withdrawal_charge = "0.25"

[state_pension]
new_full_weekly       = "241.30"      # 2026/27, verified — see assumptions.md
qualifying_years_full = 35
qualifying_years_min  = 10
deferral_increment_per_9_weeks = "0.01"
```

*(The figures above mirror the verified table in
[assumptions.md](../assumptions.md); that register — not this example — is
the authority until the real data file exists.)*

### `age_rules.toml` (effective-dated, not per-year)

```toml
schema_version = 1

[meta]
verified_on = 2026-08-01
sources = [
  "https://www.gov.uk/state-pension-age",
  # NMPA legislation citation — see assumptions.md
]

[nmpa]
schedule = [
  { from_date = 1900-01-01, age_years = 55 },
  { from_date = 2028-04-06, age_years = 57 },   # protected pension ages exist;
]                                               # out of scope v1, noted in UI copy

[spa]
# DOB-banded schedule; exact bands from the verified timetable in
# assumptions.md (66→67 phasing 2026–2028, then legislated 67→68).
bands = [
  { dob_to = 1960-04-05, rule = "fixed_age", age_years = 66 },
  # ... phased 66→67 bands by DOB ...
  # ... fixed 67, then phased 67→68 bands ...
]

[lisa]
open_age_min = 18
open_age_max_exclusive = 40
contribute_age_max_exclusive = 50
access_age = 60
```

## 3. Future tax years

If a projection extends past the last shipped tax-year file, the region
extends the final known year according to the
`policy.tax.future_years` assumption key: `frozen` (thresholds nominal-flat)
or `cpi_indexed` (thresholds grow with the CPI assumption). Both are
scenario-flippable. Known *announced* changes with future effective dates
(e.g. threshold freeze end, pre-announced rate changes) ship as data in the
relevant future-year file as soon as they are legislated, so the model
prefers legislated data over extrapolation.

## 4. Adding a new tax year (recurring task)

1. After each Budget/fiscal event: create `tax_year_YYYY_YY.toml` from the
   previous year's file.
2. Verify every figure against gov.uk; update `verified_on` + `sources`.
3. Update the verified-figures table in [assumptions.md](../assumptions.md).
4. Run the loader's validation tests.
