# Domain model

> Status: design spec (pre-implementation) · Last updated 2026-08-01 ·
> Back to [planning](../planning.md) · Terms: [glossary](../glossary.md)

Typed sketches only — signatures, not implementations. All dataclasses are
frozen; all money is `Decimal`; all datetimes are timezone-aware
([ADR-0006](../adr/0006-engine-purity-and-reproducibility.md)).

## 1. Facts vs assumptions (the load-bearing principle)

Every number entering a projection is either a **fact** (the user stated it)
or an **assumption** (the app defaulted or estimated it). The distinction is
first-class in the type system and flows through to results, so the UI can
always answer *"which of these numbers did I state, and which did you
assume?"*

```python
class Provenance(Enum):
    USER_FACT = auto()  # the user stated it
    DEFAULT_ASSUMPTION = auto()  # shipped default, not overridden
    USER_OVERRIDE = auto()  # user overrode the default
    SCENARIO_OVERRIDE = auto()  # a scenario overrode it (ADR-0003)


@dataclass(frozen=True)
class Fact[T]:
    """A value the user stated."""

    value: T
    as_of: date  # when it was true (e.g. balance date)
    recorded_on: datetime  # tz-aware
    note: str | None = None


@dataclass(frozen=True)
class Assumption[T]:
    """A value the app defaulted or estimated. Always overridable."""

    key: AssumptionKey  # stable dotted id, e.g. "returns.equity.real"
    value: T
    default_value: T  # what the shipped default was
    provenance: Provenance  # DEFAULT / USER_OVERRIDE / SCENARIO_OVERRIDE
    source: str  # citation/URL for the default's basis
    recorded_on: datetime
    description: str
```

Rules:

- `AssumptionKey` is a stable enum of dotted string ids; the
  [assumptions register](../assumptions.md) is the catalogue of keys,
  defaults, and sources.
- `AssumptionSet` is a typed registry keyed by `AssumptionKey`. **The engine
  may not read a tunable number any other way** — step functions receive
  only `Plan` + `AssumptionSet` + `Region` + `RunConfig`.
- The `AssumptionSet` **records every key actually read** during a run.
- `ProjectionResult.provenance` therefore lists: facts used, assumptions
  used (each flagged default vs overridden), the region data-file version,
  and the RNG seed. That is exactly the payload the UI's "stated vs
  assumed" inspector renders — no UI-side bookkeeping.
- Future-policy uncertainty is modelled as assumptions with keys (e.g.
  `policy.state_pension.uprating`, `policy.tax.future_years`), so scenarios
  can flip them like any other override.

## 2. Entity catalogue

```python
@dataclass(frozen=True)
class Household:  # ADR-0004: 1..2 persons; v1 validates == 1
    persons: tuple[Person, ...]
    spending: SpendingPlan  # household-level
    planned_outflows: tuple[PlannedOutflow, ...]  # household-level


@dataclass(frozen=True)
class Person:
    date_of_birth: Fact[date]
    sex_for_longevity: Fact[str] | None  # optional, longevity default only
    tax_residency: TaxResidency  # rUK | SCOTLAND (designed-for)
    employment_income: Fact[Money] | None
    target_retirement_age: int  # what-if-overridable (ADR-0003)
    wrappers: tuple[Wrapper, ...]
    db_pensions: tuple[DBPension, ...]
    state_pension: StatePensionRecord
    glide_path: GlidePathConfig
```

### Wrappers

```python
class WrapperKind(Enum):
    WORKPLACE_DC = auto()
    SIPP = auto()
    ISA = auto()  # v1
    LISA = auto()
    GIA = auto()
    CASH = auto()  # extensions


@dataclass(frozen=True)
class Wrapper:
    kind: WrapperKind
    balance: Fact[Money]
    allocation: AssetAllocation  # or supplied by glide path
    fees: FeeSchedule  # platform + fund, annual %
    contributions: ContributionSchedule | None


@dataclass(frozen=True)
class ContributionSchedule:
    employee_amount: Fact[Money]  # per year; % of salary variant too
    employer_amount: Fact[Money] | None  # incl. match rules
    relief_mechanic: ReliefMechanic  # RELIEF_AT_SOURCE | NET_PAY (region)
    escalation: AssumptionRef | None  # e.g. grows with earnings assumption
```

Tax treatment in / during / out is **not** stored per wrapper instance — it
is the region's `WrapperRuleset` keyed by `WrapperKind`
([ADR-0002](../adr/0002-core-region-boundary.md)).

### Defined benefit

```python
@dataclass(frozen=True)
class DBPension:
    accrued_annual_pension: Fact[Money]  # at date of leaving / statement
    statement_date: date
    normal_pension_age: Fact[int]  # scheme fact
    revaluation_basis: RevaluationBasis  # scheme fact (e.g. CPI capped 5%)
    early_late_factors: FactorTable  # scheme facts, user-entered
    commutation_factor: Fact[Decimal] | None  # £ lump sum per £1 pension
    taken_at_age: int | None  # decision variable (what-if)
    commuted_fraction: Decimal  # decision variable (what-if)
```

### State pension

```python
@dataclass(frozen=True)
class StatePensionRecord:
    forecast_weekly_amount: Fact[Money] | None  # from user's official forecast
    qualifying_years: Fact[int] | None  # NI record, if no forecast
    planned_extra_years: int  # what-if: years still to accrue
    deferral_years: Decimal  # what-if
    # SPA derives from DOB via region AgeRules; uprating is an assumption key.
```

If the user has an official forecast it is the fact and wins; otherwise
entitlement is derived from qualifying years (fact) via the region's
`StatePensionScheme`.

### Spending, outflows, annuities

```python
@dataclass(frozen=True)
class SpendingPlan:
    annual_spending_real: Fact[Money]  # today's money; may vary by stage
    stage_multipliers: Mapping[LifeStage, Decimal] | None  # e.g. go-go years


@dataclass(frozen=True)
class PlannedOutflow:  # mortgage payoff, gift, purchase
    label: str
    amount_real: Fact[Money]
    at_age_of: tuple[PersonRef, int]  # person + age it occurs


@dataclass(frozen=True)
class AnnuityPurchase:  # what-if decision variable
    at_age: int
    fraction_of_pot: Decimal  # partial annuitisation supported
    annuity_type: AnnuityType  # LEVEL | ESCALATING | INFLATION_LINKED
    basis: AnnuityBasis  # SINGLE | JOINT
    # rate comes from the annuity-rate assumption table by age/type
```

## 3. Life stages and glide path

A person is not a snapshot: the projection moves them through stages, and
the glide path is the app's namesake de-risking mechanism.

```python
class LifeStage(Enum):
    EARLY_ACCUMULATION = auto()  # far from retirement, growth allocation
    MID_ACCUMULATION = auto()
    PRE_RETIREMENT = auto()  # de-risking window
    DECUMULATION = auto()


@dataclass(frozen=True)
class GlidePathConfig:
    target_allocation_by_years_to_retirement: FactorTable
    # allocation(period) = interpolate(table, years_to_target); the engine
    # derives the stage from age vs target retirement age each period.
```

Stage boundaries are derived, not stored: stage is a function of
years-to-target-retirement (and, in decumulation, of having retired). The
glide path maps years-to-retirement → `AssetAllocation`; the default shape
is an assumption (`glidepath.default_shape`), overridable per person.

## 4. Scenarios

See [ADR-0003](../adr/0003-scenario-model.md). `Scenario` = named
`tuple[Override, ...]`; `Override.target` is an `AssumptionKey` or a
whitelisted what-if field (retirement age, contribution rates, planned
outflows, withdrawal strategy, annuitisation, state pension deferral).

## 5. Serialisation

Every entity above serialises to the `.glidepath.json` schema per
[ADR-0005](../adr/0005-persistence-format.md): facts inline under `plan`,
assumption overrides only under `assumption_overrides`, scenarios as
override lists. Defaults re-resolve on load.
