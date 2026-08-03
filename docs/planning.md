# Glidepath planning

> Status: master planning document — implementation issues are raised from
> §8 · Last updated 2026-08-01 · All UK figures verified against primary
> sources on 2026-08-01 (§6).

Contents: [1 Vision](#1-vision-and-product-principles) ·
[2 Scope](#2-scope) · [3 Architecture](#3-architecture) ·
[4 Decisions](#4-decision-records-proposed--awaiting-approval) ·
[5 Design](#5-design) · [6 Verified figures](#6-verified-uk-policy-figures-202627) ·
[7 Default assumptions](#7-default-assumptions-proposed) ·
[8 Roadmap](#8-phased-roadmap--issue-basis) ·
[9 Open questions](#9-open-questions)

---

## 1. Vision and product principles

Glidepath is a desktop retirement/investment planner: model a person moving
through life stages — accumulation, de-risking glide path (the namesake),
decumulation — under explicit, inspectable inputs.

1. **Facts vs assumptions is the product.** Every number is either a
   **fact** the user stated (DOB, balances, contributions, accrued DB
   entitlement, NI record) or an **assumption** the app defaulted or
   estimated (returns, inflation, annuity rates, future tax rules,
   longevity). The distinction is first-class in the data model, flows
   through every projection, and is surfaceable in the UI: a user can
   always ask *"which of these numbers did I state, and which did you
   assume?"* Every assumption carries value, source, date recorded, and
   whether the user overrode the default.
2. **Not financial advice.** Glidepath is a personal modelling tool, not
   regulated financial advice. A disclaimer to this effect is a **product
   requirement**: UI (first run + About), any exported output, README.
3. **Private by construction.** All user data stays local; nothing is
   transmitted.
4. **Region-agnostic core.** UK specifics live in `regions/uk` behind typed
   protocols; policy figures live in data files, never logic.

## 2. Scope

| | Contents |
| --- | --- |
| **v1** | Single person, UK (rUK tax). Wrappers: workplace DC, SIPP, S&S ISA. DB pensions (deferred/accrued entitlements only). State pension. Deterministic annual projection. Withdrawal strategies: fixed real, fixed %. Scenarios + comparison. JSON persistence. |
| **Deferred (phased)** | Monte Carlo; guardrails + natural yield; annuities incl. partial annuitisation; LISA/GIA/cash wrappers; Scottish bands (designed-for now); dividend/savings taxation (needs GIA); AA carry-forward; DB active accrual (accrual rate, pensionable salary, service); couples activation; announced future rules (2027 cash-ISA reform, 2029 salary-sacrifice NICs). |
| **Out of scope** | Advice or recommendations; live market data; non-UK regions (architecture allows later); protected pension ages (noted in UI copy). |

## 3. Architecture

```
┌───────────────┐   ┌───────────────────┐   ┌──────────────────────────┐
│ GUI (PySide6) │──▶│ Scenario layer    │──▶│ Core engine (pure,       │
│ facts entry,  │   │ base ⊕ overrides  │   │ Decimal, seeded RNG)     │
│ "stated vs    │   │ (§4.3)            │   │ run(plan, assumptions,   │
│ assumed" view │   └───────────────────┘   │     region, config)      │
└───────────────┘                           └───────────┬──────────────┘
        ▲                                               │ typed protocols
        │ provenance record                             ▼ (§4.2)
┌────────────────┐                          ┌──────────────────────────┐
│ .glidepath.json│◀──────────────────────── │ regions/uk ◀── TOML data │
│ (local only)   │        (§4.5)            │ (tax years, age rules)   │
└────────────────┘                          └──────────────────────────┘
```

## 4. Decision records (Proposed — awaiting approval)

Each records: decision, rationale, rejected alternatives, accepted costs.
Once approved, a decision changes only by a superseding entry here.

### 4.1 Time step and calendar

**Decision.** Annual steps, where each step is a *period* supplied by the
region's `FiscalCalendar` protocol (UK: tax years, 6 Apr–5 Apr; core never
knows what "6 April" is). Age-triggered changes carry their **exact
dates** and follow one convention, tested at boundaries:

- **Access gates** (NMPA, LISA access) are open for a period only if the
  age is attained on or before the period's first day — conservative, so
  the model never simulates a withdrawal that would be unauthorised in
  reality (a 5-April birthday unlocks the *next* tax year, never the
  preceding 6 April).
- **Income entitlements** (state pension from SPA, DB from NPA, annuity
  start) begin at their exact date and are pro-rated by whole months
  within their starting period.
- **Eligibility windows** (LISA opening 18–39, LISA contributions to
  age 50) are *not* gates: they run from the exact opening birthday to
  the day before the closing birthday, with no per-period rounding in
  either direction. The region exposes the window as exact dates; the
  consumer intersects it with the period and pro-rates any flow by
  whole months like other partial years — so a 7-April 18th birthday
  is eligible from that date within that same tax year.

Other partial years (starting work, retiring mid-year) are pro-rated by
whole months as a `Decimal` fraction the same way; no sub-stepping.

**Why.** All UK tax and allowances are assessed per tax year, so a tax-year
step makes tax exact where it matters and keeps state small and auditable.
**Rejected:** monthly steps (12× state, false precision — tax must still be
annualised); calendar years (permanent mismatch with UK tax); birthday
years (breaks tax years *and* couples). **Accepted cost:** intra-year
sequence-of-returns effects invisible (year-order sequence risk still
captured by Monte Carlo).

### 4.2 Core/region boundary

**Decision.** `glidepath.core` defines typed Protocols crossing the
boundary — `FiscalCalendar`, `TaxSystem` (`assess(period, TaxInput) ->
TaxResult` with *generic* categorised income shapes, no UK band names),
`WrapperRuleset` (limits, relief mechanics, access ages, in/during/out tax
treatment per wrapper kind), `StatePensionScheme`, `AgeRules` — plus the
region-agnostic value types (`Money`, `Rate`, `Period`, `Fact`,
`Assumption`). `glidepath.regions.uk` (promoted from `uk.py` to a package)
implements them, loading every figure from data files (§5.3). A `Region`
aggregate is injected at run construction. Dependency direction is region →
core only, enforced by a test (core must not import `regions.*`) plus a
grep guard (no policy-figure literals outside `regions/uk/data/`).

**Why.** Protocol injection is what mypy `--strict` fully verifies, and it
turns the CLAUDE.md isolation rule into a cheap failing test. **Rejected:**
ABC inheritance into the engine (couples internals to regions); plugin
discovery (over-engineering for one region); region enum + branches in core
(banned by repo policy).

### 4.3 Scenario model

**Decision.** A user file holds one base `Plan` (facts) + one base
`AssumptionSet`, plus named `Scenario`s, each a list of typed
`Override(target, value, note)` records. Targets are assumption keys or
**decision variables** (§5.1) — retirement age, contribution amounts,
planned outflows, withdrawal strategy, annuitisation, state pension
deferral — addressed by **stable entity id + field path**: every person,
wrapper, DB pension and planned outflow carries a persisted `EntityId`,
so overrides survive reordering and insertion. An override whose target
entity no longer exists is an *orphan*: the file still loads, but the
scenario is flagged invalid until the user removes or retargets the
override. Effective inputs = base ⊕ overrides at run time; every override
carries `SCENARIO_OVERRIDE` provenance. Comparison is a per-period
metrics report across scenarios.

**Why.** A scenario stored as deltas *is* its own diff — "what's different?"
is a read, not a computation — and base-fact corrections propagate to every
what-if automatically. **Rejected:** deep-copied plan per scenario (drift,
needs structural diffing); event-sourced log (heavy, not human-readable);
scenario DSL (unserialisable). **Accepted cost:** what-ifs outside the
whitelist need a model change. The boundary is type-enforced: facts
(`Fact[T]` — balances, DOB, NI record, accrued DB) are **never**
scenario-overridable — a different balance is a different plan — while
decision variables (`Decision[T]`, §5.1) and assumptions are the only
legal override targets, so nothing user-stated is ever silently replaced.

### 4.4 Household and couples

**Decision.** Model `Household{persons: 1..2}` in the schema and engine
signatures **now**; everything taxed or age-gated (wrappers, DB, NI/state
pension, tax assessment) hangs off a `Person`; shared economics (spending
plan, one-off outflows, success metrics) hang off the `Household`. v1
validates `len(persons) == 1`. No couples UI, transfers, or
survivor/death modelling until a later couples spike adds its own decision
record.

**Why.** UK tax is individual, so computation is per-person anyway; the
expensive-to-retrofit fork is where spending and goals live, and placing
them at household level costs nothing today but avoids a schema + engine
migration later. **Rejected:** pure single-person schema (the corner the
brief warns about); full couples in v1 (drags in survivorship modelling
prematurely).

### 4.5 Persistence

**Decision.** Two stdlib-only formats for two jobs. **User data:** one JSON
document per plan, `.glidepath.json` — `schema_version`, sorted keys,
2-space indent, `\n` endings, `Decimal` as strings, ISO-8601 tz-aware
datetimes; deterministic output → clean diffs. Stored wherever the user
chooses; never transmitted. **Shipped region data:** TOML read with stdlib
`tomllib` (read-only at runtime — no TOML writer needed; comments carry
citations). User files store only assumption *overrides*; defaults
re-resolve on load against shipped data, and the file records the data
version it was last resolved against so default changes surface visibly.

**Why.** Zero runtime dependencies preserved for read and write; JSON is
the right app-owned canonical format, TOML the right hand-maintained one.
**Rejected:** TOML for user files (needs `tomli-w`); YAML (dependency +
coercion footguns); SQLite (opaque, not diffable). **Accepted cost:** a
versioned migration harness from day one (v1→v1 no-op).

### 4.6 Engine purity and reproducibility

**Decision.** The engine is pure typed functions over frozen dataclasses:
no I/O, no clock reads (`today` is an input), no global state. `Decimal`
end-to-end — money quantized to pennies (`ROUND_HALF_EVEN`) at every ledger
write; rates/factors unquantized. Randomness only via an injected
`RandomSource` protocol wrapping `random.Random(seed)`; the seed lives in
`RunConfig`; Monte Carlo path *i* uses a substream derived from
`(seed, i)` so paths are order-independent and individually re-runnable.
The derivation must be an explicit integer/bytes function (e.g. a
fixed-width digest of seed and path index): `random.Random((seed, i))` is
a `TypeError` on our pinned Python (3.11+ restricts seed types to
None/int/float/str/bytes/bytearray), so the derivation cannot be delegated
to the seed argument. Reproducibility is defined over a **run manifest**
persisted with every result: effective plan (facts + decisions), effective
assumptions, the full `RunConfig` (`today`, horizon, mode, path count,
withdrawal strategy, seed), the region data content version, and the
engine version — identical manifest → identical output, as a Hypothesis
property test.

**Why.** Purity makes provenance trustworthy (results depend only on
declared inputs) and the ≥90% coverage bar cheap (no mocking); seeding
makes MC debuggable ("re-run path 4711"). **Rejected:** numpy (runtime
dep, float-based, breaks the Decimal rule); module-level `random`; floats
internally. **Accepted cost:** Decimal MC is slow — annual steps and small
state keep it tractable; a measurement task gates any revisit (§8, 7.2).

## 5. Design

### 5.1 Domain model

Typed sketches (signatures, not implementations). All dataclasses frozen;
money `Decimal`; datetimes tz-aware.

**Facts vs assumptions — the type-level core:**

```python
class Provenance(Enum):
    USER_FACT = auto()  # the user stated it
    DEFAULT_ASSUMPTION = auto()  # shipped default, not overridden
    USER_OVERRIDE = auto()  # user overrode the default
    SCENARIO_OVERRIDE = auto()  # a scenario overrode it (see 4.3)


@dataclass(frozen=True)
class Fact[T]:
    """A value the user stated."""

    value: T
    as_of: date  # when it was true (e.g. balance date)
    recorded_on: datetime  # tz-aware
    note: str | None = None


@dataclass(frozen=True)
class Decision[T]:
    """A user choice — neither a fact about the world nor an estimate.

    The only scenario-overridable plan fields (see 4.3).
    """

    value: T
    recorded_on: datetime  # tz-aware
    note: str | None = None


@dataclass(frozen=True)
class Assumption[T]:
    """A value the app defaulted or estimated. Always overridable."""

    key: AssumptionKey  # stable dotted id, e.g. "returns.equity.real"
    value: T
    default_value: T  # what the shipped default was
    provenance: Provenance
    source: str  # citation/URL for the default's basis
    recorded_on: datetime
    description: str
```

Alongside facts and assumptions there is a third, deliberately named kind:
**decision variables** — user *choices* rather than statements about the
world, wrapped in `Decision[T]` (`target_retirement_age`, contribution
amounts, planned outflow amounts, `DBPension.taken_at_age`,
`commuted_fraction`, state pension deferral) or forming whole decision
records (`AnnuityPurchase`, withdrawal strategy). They are exactly the
scenario what-if whitelist (§4.3) and surface in the UI as "your choices"
— a third column beside stated facts and assumptions.

Rules: `AssumptionKey` is a stable enum of dotted ids catalogued in §7.
`AssumptionSet` is a typed registry — **the engine may not read a tunable
number any other way** (step functions receive only `Plan` +
`AssumptionSet` + `Region` + `RunConfig`). Every key actually read during a
run is tracked without compromising engine purity: reads accumulate in a
per-run recorder created inside `run()` and returned as part of the result
(the frozen `AssumptionSet` itself is never mutated), so
`ProjectionResult.provenance` lists facts used, assumptions used (default
vs overridden), decision variables in effect, region data version, and
seed: exactly the payload the UI's "stated vs assumed" inspector renders,
with no UI-side bookkeeping. The fact and decision lists cover the
`Fact[T]`/`Decision[T]`-wrapped values; *structural* plan fields that
also drive results — wrapper kinds, fee schedules, relief mechanics, DB
scheme structures (statement date, revaluation basis, factor tables) —
are part of the persisted plan itself and surface entity-level in the
Phase 6/8 inspector rather than as individual provenance rows.
Future-policy uncertainty (state pension
uprating, post-freeze tax indexation) is just assumptions with keys, so
scenarios can flip them.

**Entities:**

```python
@dataclass(frozen=True)
class Household:  # 4.4: 1..2 persons; v1 validates == 1
    persons: tuple[Person, ...]
    spending: SpendingPlan  # household-level
    planned_outflows: tuple[PlannedOutflow, ...]  # household-level


@dataclass(frozen=True)
class Person:
    id: EntityId  # stable; override targets (4.3) and couples need it
    date_of_birth: Fact[date]
    sex_for_longevity: Fact[Sex] | None  # enum; longevity default only
    tax_residency: TaxResidencyId  # opaque, region-defined (uk: ruk|scotland)
    employment_income: Fact[Money] | None
    target_retirement_age: Decision[int]
    mpaa_triggered_on: Fact[date] | None  # flexibly accessed before this plan
    lsa_used: Fact[Money] | None  # lump sum allowance already used
    wrappers: tuple[Wrapper, ...]
    db_pensions: tuple[DBPension, ...]
    state_pension: StatePensionRecord | None  # None: not modelled
    glide_path: GlidePathConfig | None  # None: default-shape assumption applies


# Wrapper kinds are OPAQUE region-defined ids ("uk.workplace_dc",
# "uk.sipp", "uk.isa"; extensions "uk.lisa", "uk.gia", "uk.cash") — core
# never enumerates them; the region's WrapperRuleset maps id -> rules, so
# no UK account type leaks into the core model (4.2).
WrapperKindId = NewType("WrapperKindId", str)
TaxResidencyId = NewType("TaxResidencyId", str)  # same pattern


@dataclass(frozen=True)
class Wrapper:
    id: EntityId  # stable; override targets (4.3) need it
    kind: WrapperKindId
    balance: Fact[Money]  # pension kinds: uncrystallised value
    crystallised_balance: Fact[Money] | None  # pension kinds: already in drawdown
    allocation: AssetAllocation | None  # None: the glide path supplies it
    fees: FeeSchedule | None  # platform + fund, annual %; None: fee assumptions
    contributions: ContributionSchedule | None


@dataclass(frozen=True)
class ContributionSchedule:
    employee_amount: Decision[Money]  # per year; % of salary variant too
    employer_amount: Fact[Money] | None  # employment terms incl. match rules
    relief_mechanic: ReliefMechanic  # RELIEF_AT_SOURCE | NET_PAY (region)
    escalation: AssumptionRef | None  # e.g. grows with earnings assumption


@dataclass(frozen=True)
class DBPension:  # v1: DEFERRED/ACCRUED entitlements only, no future accrual
    id: EntityId
    accrued_annual_pension: Fact[Money]  # at date of leaving / statement
    statement_date: date
    normal_pension_age: Fact[int]  # scheme fact
    revaluation_basis: RevaluationBasis  # scheme fact (e.g. CPI capped 5%)
    early_late_factors: FactorTable  # scheme facts, user-entered
    commutation_factor: Fact[Decimal] | None  # £ lump sum per £1 pension
    taken_at_age: Decision[int] | None
    commuted_fraction: Decision[Decimal]


@dataclass(frozen=True)
class StatePensionRecord:
    forecast_weekly_amount: Fact[Money] | None  # official forecast wins
    protected_payment: Fact[Money] | None  # pre-2016 transition; CPI-only
    ni_record_start: Fact[date] | None  # gates the derivation path below
    qualifying_years: Fact[int] | None  # NI record, if no forecast
    planned_extra_years: Decision[int]  # years still to accrue
    deferral_years: Decision[Decimal]
    # SPA derives from DOB via region AgeRules; uprating is an assumption key.


@dataclass(frozen=True)
class SpendingPlan:
    annual_spending_real: Fact[Money]  # today's money
    stage_multipliers: Mapping[LifeStage, Decimal] | None  # e.g. go-go years


@dataclass(frozen=True)
class PlannedOutflow:  # mortgage payoff, gift, purchase — a decision
    id: EntityId
    label: str
    amount_real: Decision[Money]
    at_age_of: tuple[EntityId, int]  # person + age it occurs


@dataclass(frozen=True)
class AnnuityPurchase:  # wholly a decision record (5.1)
    id: EntityId
    at_age: int
    fraction_of_pot: Decimal  # partial annuitisation supported
    annuity_type: AnnuityType  # LEVEL | ESCALATING | INFLATION_LINKED
    basis: AnnuityBasis  # SINGLE | JOINT
    # rate comes from the annuity-rate assumption table by age/type
```

DB scheme parameters (revaluation basis, NPA, early/late factors,
commutation factor) are user-entered **facts** — schemes vary too much to
ship as data. v1 modelling conventions (roadmap 4.2): the scheme's one
`RevaluationBasis` (CPI optionally capped, fixed, or none; CPI-linked
revaluation floored at zero) governs both revaluation in deferment and
increases in payment — splitting the two bases is a 9.6 extension.
Within the run, revaluation advances with each period's CPI under the
§5.2 linear whole-month convention; the span from the statement date to
`today` — which the run never models period-by-period — compounds the
assumed CPI over whole months (integer-exponent whole years plus a
linear remainder, exact `Decimal` per §4.6). Commutation trades pension
for `pension given up x commutation factor` of tax-free cash in the
period benefits start; a start date before `today` means the pension is
already in payment and the lump sum already lives in the stated
balances. In decumulation, net-of-tax DB/state-pension income and any
commutation lump sum meet the net spending need before wrappers are
drawn; income beyond the need is not banked — there is no cash/GIA
wrapper until roadmap 9.2.

State pension: an official forecast, when present, is the fact and wins.
The qualifying-years derivation (÷35) is valid **only for NI records
starting after 5 April 2016**: pre-2016 records are governed by a
transitional *starting amount* (old/new-system comparison,
contracting-out, possible protected payment) that the model does not
compute — for those users an official forecast is **required**, and any
protected payment is recorded separately because it uprates by CPI only,
not the full uprating policy. Conventions (roadmap 4.3): the forecast
weekly amount is the DWP total, of which `protected_payment` is the
CPI-only slice; the derivation caps stated-plus-planned years at the
full-rate count and pays nothing below the minimum; amounts are taken in
the rates of the tax year containing `today` (annualised at 52 weeks)
and uprated by the engine from the run start. Because upratings take
effect whole each 6 April — exactly a UK period boundary — the state
pension stream steps by a **full annual uprating at every period
boundary**, never scaled by a partial period's active fraction (a
deliberate deviation from the §5.2 linear convention, which models
continuously growing price and earnings levels); uprating is never
negative — a deflationary CPI freezes the rate, matching statute. A run
starting past the last shipped tax-year file steps the shipped rate
forward to `today` by one whole uprating per intervening tax year (the
extension deliberately carries the rate untouched, §5.3); the uprating
policy is read at region build like the future-years extension, so it
lands in the region data version. Deferral shifts the start past SPA in
whole months and earns one ninth of 1% per whole week deferred, payable
only from nine weeks (~5.8%/52 weeks; shipped as data); the uplift
fraction applies to the rate payable **at claim** — upratings earned
during deferment included — and the resulting increment uprates by CPI
only from then on (§6).

Planned outflows are dated one-offs. Conventions (roadmap 5.4): an
outflow lands whole — never pro-rated — in the period containing the
date its person attains the stated age, and only when that date lies
inside the run window `[today, horizon_end]`; an outflow already past
lives in the stated balances, not the model (the DB lump-sum
convention). The real amount inflates to nominal by the period-start
price level (the run's one inflation truth) and joins the period's net
need: in decumulation the configured strategy funds it after the income
offset above; before decumulation it is funded net-defined in the
default tax-aware order, since the withdrawal strategy is a
decumulation decision (§5.2).

Pre-existing pension access is likewise a set of facts:
`crystallised_balance` (funds already designated to drawdown), `lsa_used`,
and `mpaa_triggered_on`. They make an already-in-drawdown user modellable
— no fresh tax-free cash on crystallised funds, MPAA from day one, LSA
headroom reduced — and they carry the NMPA 2028 transition correctly:
benefits already in payment continue below 57, while new crystallisations
and UFPLS are gated by the NMPA schedule (§4.1).

**Life stages and glide path.** A person is not a snapshot: the projection
moves them through `EARLY_ACCUMULATION → MID_ACCUMULATION → PRE_RETIREMENT
(de-risking) → DECUMULATION`. Stage is *derived* each period from
years-to-target-retirement, not stored. The glide path maps
years-to-retirement → asset allocation by interpolating a factor table;
the default shape is an assumption (`glidepath.default_shape`),
overridable per person. Stage boundaries (3.5): `DECUMULATION` once the
target retirement age is attained by the period's first day
(years-to-retirement ≤ 0, the §4.1 gate convention); `PRE_RETIREMENT`
inside the table's de-risking window — the years at which the
allocation starts changing (the lowest knot of the top
constant-allocation plateau); the `EARLY`/`MID` accumulation split
falls at twice that window — the split is presentational (only the
allocation is mechanical), so a simple doubling rule suffices. A
constant-allocation table has a zero window and never de-risks, so
`PRE_RETIREMENT` is unreachable there.

### 5.2 Projection engine

```python
def run(
    plan: Plan, assumptions: AssumptionSet, region: Region, config: RunConfig
) -> ProjectionResult: ...
```

Pure and deterministic (§4.6). `config`: `today`, horizon end (default from
longevity assumption), mode (deterministic | monte-carlo), seed, paths,
withdrawal strategy. **The same step function runs under both modes; only
the `ReturnModel` differs** — a design invariant, not an aspiration.

**Order of operations within a period is part of the spec** (tested):

1. **Open period** — resolve ages, stage, glide-path allocation; apply the
   §4.1 convention: access gates (NMPA, LISA access) are open only if attained by
   the period's first day; income entitlements starting mid-period are
   marked for pro-rating.
2. **Income** — DB in payment (revalued/uprated), state pension (uprating
   assumption), annuity income, employment income; entitlements beginning
   this period are pro-rated by whole months from their exact start date.
3. **Contributions** — employee + employer, relief mechanics, AA/taper/MPAA
   checks (region ruleset).
4. **Withdrawals** — per strategy. `SpendingPlan` is a **net (after-tax)**
   need, so net-defined strategies (fixed real) are grossed up against the
   region tax system: iterate gross withdrawal → `TaxSystem.assess` → net
   cash until the need is met (a fixed-point that converges in a few
   rounds on piecewise-constant marginal rates; iteration cap, residual
   settled to cash). Gross-defined strategies (fixed-%) declare themselves
   gross and skip the iteration.
5. **Tax** — final `TaxSystem.assess` per person on the period's full
   categorised income picture. The gross-up in step 4 calls the same
   function, so the final assessment is consistent by construction.
6. **Fees** — platform + fund on average balances (the mean of the
   opening and post-flow balances; the fee never exceeds what the
   account holds).
7. **Growth** — apply the period's returns to each wrapper's allocation.
8. **Close period** — quantize ledger, emit `PeriodSnapshot`.

Income/contributions before tax (tax needs the full picture); fees before
growth approximates intra-year accrual acceptably at annual resolution.
`PeriodSnapshot` records per person/wrapper: opening/closing balances,
flows by category, tax with breakdown, ages, stage, allocation.

**Partial first and last periods (decided — roadmap 4.6).** The run
anchors on the period containing `config.today` and ends with the period
containing the horizon end, but models only the window
`[today, horizon_end]`. A period partly outside that window is scaled by
its *active fraction* — whole months inside the window over whole months
in the period, the §4.1 whole-month convention (`period_active_fraction`;
under one whole month the fraction is 0). Flows (employment income,
scheduled contributions, spending need) are multiplied by the fraction,
so a mid-period `today` never re-models months already reflected in the
balance facts and the final period never models time past the horizon
end. **Growth and fees scale the annual rate linearly by the same
fraction** rather than compounding by a fractional exponent: linear
scaling is exact `Decimal` arithmetic (multiplication and division only,
fully reproducible per §4.6), matches §4.1's linear whole-month
convention, and its error against fractional-exponent compounding is
second-order small and confined to at most two periods per run.
Fractional-exponent compounding was rejected because `Decimal` powers
with non-integer exponents are only "almost always correctly rounded"
(Python `decimal` docs), which is not the byte-identical reproducibility
§4.6 demands. The cumulative CPI and nominal escalation factors advance
between periods the same way — by the completed period's annual rate
scaled linearly by that period's active fraction — so a mid-year start
advances later price and earnings levels only by the months actually
modelled, never a whole year. **Accepted costs:** annual caps, allowances, and tax bands
apply whole to the partial year — the pro-rated income of the first
modelled year meets full-year bands, understating its tax slightly
(the elapsed months' income belongs to the pre-model past); and a run
starting with under one whole month of a period models that period as
zero-flow. Landed before the 4.5 golden scenario, so the hand-reviewed
expected output is written once against the corrected behaviour.

**Golden scenario (roadmap 4.5).** The "35-year-old, DC + ISA, retires
at 60" run lives in `tests/test_golden_scenario.py` with its
hand-reviewed expected output checked in at
`tests/golden/dc_isa_retire_60.json` (nominal ledger figures per period
plus each period's real closing balance, the provenance labels, and the
assumption keys read). Any engine change that shifts the output fails
the test by design; regenerate with
`uv run --locked pytest --no-cov tests/test_golden_scenario.py
--update-golden` (`--no-cov` because the repository-wide coverage gate
would fail a single-module run) and explain the diff in the pull
request. Companion tests pin
independently hand-computed anchors (first partial period, the
retirement transition, per-period ledger identities), so the file is
anchored to reviewed arithmetic, not to whatever the engine emitted
when it was first written.

**Real vs nominal.** The engine computes nominal (tax bands are nominal
objects); the reporting layer deflates by the run's CPI path. **Real
(today's money) is the default presentation**; nominal available. One
inflation truth per run. Deflators match what each amount is: flows
deflate by the snapshot's period-start factor (the level the engine
inflated them with); closing balances — which embed the period's own
nominal growth — deflate by the level at the period's modelled end,
`inflation_factor × (1 + cpi × year_fraction)`. Presented totals are
sums of the presented per-wrapper amounts, so report tables stay
internally consistent after penny rounding.

**Withdrawal strategies** are a protocol
(`withdraw(state, need) -> WithdrawalPlan`): v1 fixed-real and fixed-%;
then guardrails (Guyton–Klinger-style bands) and natural yield. Strategies
also encode wrapper ordering (tax-aware, configurable; the full default is
GIA/cash → ISA → pension, which in v1 — before the GIA/cash wrappers land
in Phase 9 — reduces to ISA → pension) and the tax-free cash strategy
(PCLS up front vs UFPLS-style phased). Conventions (roadmap 5.1): the
strategy is a decision record carried on `RunConfig`, defaulting to
fixed-real; the state a strategy sees lists every sub-balance
(uncrystallised and crystallised per wrapper, gate-closed ones present
but flagged) plus the period's active fraction. A plan is either
**net-defined** — a net target over an ordered source list, grossed up
source by source through the step-4 fixed point — or **gross-defined** —
exact gross amounts per source, no iteration; fixed-% draws its rate
times the *accessible* pot (gate-closed funds excluded), scaled by the
period's active fraction, allocated in the default order. Execution
enforces the access gates on every plan (a draw on a gate-closed source
is an engine error, never a silent draw); a gross-defined under-draw
against the need is reported as shortfall — the roadmap-7.3 ruin signal
survives strategies that ignore the need — and an over-draw is spent,
not banked (no cash/GIA wrapper before 9.2).

**Return model and Monte Carlo.** `ReturnModel.returns_for(period, path)`:
deterministic impl = expected real returns + CPI → nominal, same every
path; stochastic impl (MC phase) = lognormal draws with assumed
volatilities and correlation matrix (Cholesky in `Decimal`; performance
measured before optimising), randomness only from the injected seeded
source. Success metrics over paths: **probability of ruin**, **sustainable
income** (highest starting withdrawal meeting a target success rate, by
bisection), **ending-pot percentiles**. Sequence-of-returns risk is
demonstrated by fixtures: same returns, different order → different
outcome.

### 5.3 UK region data files

Location: `src/glidepath/regions/uk/data/`, loaded via
`importlib.resources` + stdlib `tomllib`. One file per tax year
(`tax_year_2026_27.toml`), plus effective-dated `age_rules.toml` and
`assumptions_default.toml` (machine mirror of §7; a doc-sync test keeps
them aligned). Loader rules: money/rates are TOML **strings** parsed to
`Decimal` (bare floats in money positions are load errors); mandatory
`[meta]` with `verified_on` + `sources`; `schema_version`; strict
validation into frozen dataclasses, unknown keys error.

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
pa_taper_threshold = "100000"  # adjusted net income; PA -£1 per £2 above
pa_taper_rate      = "0.5"
# Band widths are TAXABLE income above the personal allowance, ascending.
bands = [
  { name = "basic", rate = "0.20", upper = "37700" },
  { name = "higher", rate = "0.40", upper = "125140" },
  { name = "additional", rate = "0.45" },  # no upper = unbounded
]

[income_tax.scotland]  # non-savings/non-dividend income only
personal_allowance = "12570"
pa_taper_threshold = "100000"
pa_taper_rate      = "0.5"
bands = [
  { name = "starter", rate = "0.19", upper = "3967" },
  { name = "basic", rate = "0.20", upper = "16956" },
  { name = "intermediate", rate = "0.21", upper = "31092" },
  { name = "higher", rate = "0.42", upper = "62430" },
  { name = "advanced", rate = "0.45", upper = "125140" },
  { name = "top", rate = "0.48" },
]

[pension]
annual_allowance           = "60000"
aa_taper_threshold_income  = "200000"
aa_taper_adjusted_income   = "260000"
aa_taper_rate              = "0.5"
aa_taper_floor             = "10000"
mpaa                       = "10000"
member_relief_basic_amount = "3600"  # low/no-earner relief floor, RAS only
member_relief_max_age      = 75  # no relief on contributions from age 75
relief_at_source_rate      = "0.20"
tax_free_lump_sum_fraction = "0.25"
lump_sum_allowance         = "268275"
lump_sum_death_benefit_allowance = "1073100"

[isa]
annual_allowance = "20000"
lisa_allowance   = "4000"  # counts within the overall ISA allowance
lisa_bonus_rate  = "0.25"
lisa_withdrawal_charge = "0.25"

[state_pension]
new_full_weekly       = "241.30"
qualifying_years_full = 35
qualifying_years_min  = 10
```

`age_rules.toml` holds the durable, effective-dated policy parameters that
are not re-set each tax year: NMPA (55; 57 from 2028-04-06), the SPA
DOB-band table (§6), LISA ages (open 18–39, contribute to 50, access 60),
the state pension deferral increment (1% per 9 weeks), and the new state
pension system start (2016-04-06 — the gate on the qualifying-years
derivation, §5.1).

**Future years:** past the last shipped file, the region extends the final
year per the `policy.tax.future_years` assumption (scenario-flippable):
`frozen` (indefinitely) vs `frozen_then_cpi_indexed` (the shipped default:
the legislated freeze end, then CPI-indexed). There is deliberately no
index-immediately mode — a legislated freeze end is a fact (§6), and a
mode without one could synthesize years contradicting known legislation;
a freeze end at or before the last shipped year already degrades to pure
CPI indexation. Legislated future changes (freeze end, pre-announced
rates) ship as data in the relevant year's file, so legislated data
always beats extrapolation.
Extension conventions: indexation compounds assumed CPI once from the last
shipped file (a target year never depends on intermediate synthesized
years) and scales the money figures of the income-tax schedules and the
pension/ISA allowances, quantized to whole pounds (half-even); band and
taper *rates* never extrapolate; the state pension rate is carried forward
untouched because its uprating is governed by
`policy.state_pension.uprating` (§7), never by this policy. **Recurring
task** after each Budget: copy previous year's file, re-verify every
figure, update `verified_on`/`sources`, update §6.

## 6. Verified UK policy figures (2026/27)

All verified **2026-08-01** from live-fetched primary pages (gov.uk / HMRC
manuals / DWP / OBR / FCA) — no figures from model training data. These
become the Phase 2 data files.

### Income tax (rUK)

| Figure | Value | Source |
| --- | --- | --- |
| Personal allowance | £12,570 | [gov.uk/income-tax-rates](https://www.gov.uk/income-tax-rates); [employer rates 2026–27](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027) |
| PA taper | −£1 per £2 of adjusted net income above £100,000; PA = £0 at £125,140. Calculation rounding: reduction rounded down to the whole £, resulting allowance rounded up to the whole £ | [gov.uk/income-tax-rates](https://www.gov.uk/income-tax-rates); [HMRC tax logic guide — allowances](https://developer.service.hmrc.gov.uk/guides/tax-logic-service-guide/documentation/allowances-and-reliefs.html) |
| Per-band tax rounding | Each band's tax is rounded down to the penny (`roundDown(income × rate, 2)`) | [HMRC tax logic guide — tax calculation](https://developer.service.hmrc.gov.uk/guides/tax-logic-service-guide/documentation/tax-calculation.html) |
| Basic rate | 20% on taxable income £0–£37,700 above PA | both above (conventions cross-check: 12,570 + 37,700 = 50,270) |
| Higher rate | 40% on £37,701–£125,140 | same |
| Additional rate | 45% above £125,140 | same |
| Threshold freeze | PA + higher-rate threshold frozen to **5 April 2031** (Budget 2025 extension) | [threshold-maintenance policy paper](https://www.gov.uk/government/publications/maintaining-income-tax-and-equivalent-national-insurance-contributions-thresholds-until-5-april-2031/income-tax-maintaining-the-personal-allowance-and-the-basic-rate-limit-for-income-tax-and-equivalent-national-insurance-contributions-thresholds-unt) |
| Starting rate for savings limit | £5,000 (2026/27–2030/31) | [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Marriage allowance | £1,260 transferable (max £252/yr) | [gov.uk/marriage-allowance](https://www.gov.uk/marriage-allowance) — current, not year-stamped; arithmetically fixed while PA frozen |

### Income tax (Scotland — designed-for; non-savings/non-dividend income)

| Band | Rate | Taxable income above PA | Source |
| --- | --- | --- | --- |
| Starter | 19% | £0–£3,967 | [gov.uk/scottish-income-tax](https://www.gov.uk/scottish-income-tax); [employer rates 2026–27](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027) |
| Basic | 20% | £3,968–£16,956 | same |
| Intermediate | 21% | £16,957–£31,092 | same |
| Higher | 42% | £31,093–£62,430 | same |
| Advanced | 45% | £62,431–£125,140 | same |
| Top | 48% | above £125,140 | same |

### Announced future-dated changes (model as data, not code)

| Change | Effective | Source |
| --- | --- | --- |
| Dividend rates +2ppt: ordinary 10.75%, upper 35.75% (additional 39.35% unchanged) | **2026/27 (in force)** | [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Savings income rates 22% / 42% / 47% | 6 April 2027 | same |
| Separate property income rates 22% / 42% / 47% | 6 April 2027 | same |
| Cash ISA limit £12,000 for under-65s (overall £20,000 unchanged) | 6 April 2027 | [ISA reform factsheet](https://www.gov.uk/government/publications/fiscal-events-2026-factsheets/isa-reform-2027-anti-circumvention-rules-factsheet) |
| NICs on salary-sacrificed pension contributions above £2,000/yr | April 2029 | [Employer Bulletin Dec 2025](https://www.gov.uk/government/publications/employer-bulletin-december-2025/december-2025-issue-of-the-employer-bulletin) |

### Pensions

| Figure | Value | Source |
| --- | --- | --- |
| Annual allowance | £60,000 — measures total *pension input amounts* incl. employer contributions and DB accrual; excess charged via the AA charge. Distinct from the member relief limit below | [pension scheme rates](https://www.gov.uk/government/publications/rates-and-allowances-pension-schemes/pension-schemes-rates); [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance) |
| Member relief limit | tax relief on *member* contributions limited to 100% of relevant UK earnings; low/no earners keep the **£3,600 gross (£2,880 net)** basic amount, available via relief at source only. The limit is a per-person aggregate across all schemes and mechanics; contributions from **age 75** are never relievable (FA 2004 s188(3)(a)) (verified 2026-08-02) | [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance); [pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief); [FA 2004 s190](https://www.legislation.gov.uk/ukpga/2004/12/section/190); [PTM044100](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm044100) |
| AA taper | threshold income £200,000; adjusted income £260,000; −£1 per £2 (reduction rounded down to the whole £, PTM057100); floor £10,000. Adjusted income includes all employer-funded pension input (for DB: input amount net of member contributions). Known v1 limitation: the post-8-July-2015 salary-sacrifice add-back to threshold income is not modelled (no salary-sacrifice concept in v1) | rates page; [tapered AA guidance](https://www.gov.uk/guidance/pension-schemes-work-out-your-tapered-annual-allowance); [PTM057100](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm057100) |
| MPAA | £10,000; triggered by first FAD income payment, first UFPLS, etc. (not by PCLS-only or standard lifetime annuity); when triggered, DB accrual keeps an *alternative* annual allowance = AA − MPAA (£50,000; computed, not an independent figure — nil at maximum taper; carry-forward may top up the alternative AA but never the MPAA; verified 2026-08-02) | rates page; [PTM056520](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm056520); [HS345 (2026)](https://www.gov.uk/government/publications/pensions-tax-charges-on-any-excess-over-the-lifetime-allowance-annual-allowance-special-annual-allowance-and-on-unauthorised-payments-hs345-self/hs345-pension-savings-tax-charges-2026) |
| AA carry-forward | unused AA from previous 3 tax years (detail re-verify at implementation) | [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance) |
| Relief at source | provider adds 20% basic-rate relief (25% top-up on net); higher/additional via assessment; Scottish variants | [pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief) |
| Net pay | pre-tax deduction; full marginal relief automatic | same |
| Tax-free lump sum | up to 25%, capped by LSA £268,275 | [lump sum allowance](https://www.gov.uk/tax-on-your-private-pension/lump-sum-allowance); rates page |
| LSDBA | £1,073,100 | same |
| UFPLS | 25% of each payment tax-free, 75% taxed as income; triggers MPAA | [PTM063300](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm063300) |
| Flexi-access drawdown | 25% PCLS at designation; income taxed at marginal rate (PAYE); MPAA on first income draw | [PTM062730](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm062730) |
| NMPA | 55 now; **57 from 6 April 2028**; protected pension ages exist (out of scope v1) | [taking your pension](https://www.gov.uk/personal-pensions-your-rights/how-you-can-take-pension); [NMPA policy paper](https://www.gov.uk/government/publications/increasing-normal-minimum-pension-age) |

### ISA / LISA

| Figure | Value | Source |
| --- | --- | --- |
| ISA annual allowance | £20,000 (2026/27 stated explicitly) | [gov.uk/individual-savings-accounts](https://www.gov.uk/individual-savings-accounts) |
| LISA allowance | £4,000/yr, inside the £20,000 | [gov.uk/lifetime-isa](https://www.gov.uk/lifetime-isa) |
| LISA bonus | 25%, max £1,000/yr | same |
| LISA ages | open 18–39; contribute to 50; charge-free access at 60 (or first home ≤£450k, terminal illness, death) | same; [who can open](https://www.gov.uk/lifetime-isa/who-can-open-a-lifetime-isa); [withdrawing](https://www.gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa) |
| LISA withdrawal charge | 25% of amount withdrawn | [withdrawing](https://www.gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa) |

### State pension

| Figure | Value | Source |
| --- | --- | --- |
| Full new state pension | **£241.30/week** (£12,547.60/yr) 2026/27 | [what you'll get](https://www.gov.uk/new-state-pension/what-youll-get); [DWP rates 2026–27](https://www.gov.uk/government/publications/benefit-and-pension-rates-2026-to-2027/proposed-benefit-and-pension-rates-2026-to-2027) |
| April 2026 uprating | 4.8%, earnings-driven (AWE 4.8% > CPI 3.8% > 2.5%) | [Government Actuary report, 2026 up-rating order](https://www.gov.uk/government/publications/report-to-parliament-on-the-2026-re-rating-and-up-rating-orders/report-by-the-government-actuary-on-the-draft-social-security-benefits-up-rating-order-2026-and-the-draft-social-security-contributions-regulation) |
| Full basic (old) state pension | £184.90/week (context) | DWP rates page |
| Qualifying years | 35 full (pre-2016 contracted-out caveats); 10 minimum | [what you'll get](https://www.gov.uk/new-state-pension/what-youll-get); [new state pension](https://www.gov.uk/new-state-pension) |
| Deferral | +1% per 9 weeks (~5.8%/yr); increments CPI-uprated | [deferring (post-2016)](https://www.gov.uk/deferring-state-pension/if-you-reach-state-pension-age-on-or-after-6-april-2016) |
| SPA 66→67 | phased Apr 2026–Mar 2028: DOB 1960-04-06–1960-05-05 → 66y 1m, +1 month per DOB month to 1961-02-06–1961-03-05 → 66y 11m; DOB 1961-03-06–1977-04-05 → **67** | [SPA timetable](https://www.gov.uk/government/publications/state-pension-age-timetable/state-pension-age-timetable) |
| SPA 67→68 | legislated 2044–2046: DOB 1977-04-06–1978-04-05 phased; DOB ≥ 1978-04-06 → 68 | same |
| SPA review | third review launched July 2025, ongoing; no change legislated as of 2026-08-01 | [third SPA review](https://www.gov.uk/government/collections/third-state-pension-age-review) |
| Triple lock | committed "for this parliament" (~2029); nothing legislated beyond | [Budget 2025 fact sheet](https://www.gov.uk/government/news/budget-2025-fact-sheet-cutting-the-cost-of-living) |

## 7. Default assumptions (proposed)

Every row is a shipped default the user can override; each carries its
basis. Recorded 2026-08-01. This table is the human-readable mirror of the
future `regions/uk/data/assumptions_default.toml` (doc-sync test in
Phase 2). Announced-policy items in §6 are *facts*; these are estimates.

### Economic

| Key | Default | Basis |
| --- | --- | --- |
| `inflation.cpi` | 2.0%/yr | OBR EFO March 2026: CPI at target from 2027 ([obr.uk EFO](https://obr.uk/efo/economic-and-fiscal-outlook-march-2026/)) |
| `earnings.growth.real` | 0.5%/yr | OBR EFO March 2026 medium-term real earnings growth |
| `returns.equity.real` | 4.0%/yr | Below long-run global equity history (~5% real); above FCA intermediate (5% nominal − 2% CPI = 3% real) as conservative cross-check ([COBS 13 Annex 2](https://www.handbook.fca.org.uk/handbook/COBS/13/Annex2.html): 2/5/8% nominal maxima, tax-advantaged) |
| `returns.bonds.real` | 0.5%/yr | Consistent with current gilt real-yield ballpark and FCA lower rate |
| `returns.cash.real` | −0.5%/yr | Cash trails inflation after fees over long horizons |
| `volatility.equity` | 18%/yr | Long-run global equity annual volatility (commonly cited 15–20%) |
| `volatility.bonds` | 7%/yr | Long-run gilt/IG portfolio volatility |
| `volatility.cash` | 1%/yr | Near-riskless nominal |
| `correlation.equity_bonds` | 0.2 | Long-run average; regime-dependent (label prominently) |
| `correlation.equity_cash` | 0.0 | Near-zero long-run historical correlation |
| `correlation.bonds_cash` | 0.2 | Modest positive long-run historical correlation (short rates feed both) |
| `fees.platform` | 0.25%/yr | Typical UK platform fee |
| `fees.fund` | 0.15%/yr | Typical index-tracker OCF |

### Longevity, policy futures, annuities

| Key | Default | Basis |
| --- | --- | --- |
| `horizon.planning_age` | 95 | ~1-in-4 longevity risk at 65 per ONS cohort life expectancy ([ONS calculator](https://www.ons.gov.uk/peoplepopulationandcommunity/healthandsocialcare/healthandlifeexpectancies/articles/lifeexpectancycalculator/2019-06-07); exact values from 2024-based cohort tables at implementation) |
| `glidepath.default_shape` | 80/20 equity/bonds until 15 years to retirement, then linear de-risk to 40/60 at retirement, held through drawdown | Typical UK target-date/lifestyling shape; a starting point only — per-person glide paths override it |
| `policy.state_pension.uprating` | `triple_lock` (deterministic mode: CPI + 0.5% proxy for the long-run earnings premium) | Alternative scenario: `cpi`. In Monte Carlo the true rule — max(earnings, CPI, 2.5%) — is applied per path from the path's earnings and CPI draws; protected payments always uprate by CPI only (§5.1). Triple lock committed only to ~2029 (§6) |
| `policy.tax.future_years` | frozen to 2030/31 (legislated), then CPI-indexed | Freeze is fact (§6); post-2031 indexation is assumption. Alternative: frozen indefinitely |
| `annuity.level.single.65` | 7.75%/yr per £ purchase | Which? market table, snapshot 2026-07-27, retrieved 2026-08-01 ([which.co.uk](https://www.which.co.uk/money/pensions-and-retirement/accessing-your-pensions/annuities/annuity-rates-aQGfH6W5n2rm)); best rate 7.946% — volatile market snapshot, refresh before relying on |
| `annuity.escalating3.single.65` | 5.47%/yr | same snapshot |
| `annuity.inflation_linked.single.65` | 5.5%/yr | Indicative only — secondary source ([IFA Magazine](https://ifamagazine.com/annuity-rates-hit-7-75-as-retirement-incomes-reach-18-year-high/)); weakest-sourced default here |

*Planned but not yet shipped (excluded from the Phase 2 doc-sync mirror
until Phase 5): `annuity.age_adjustment` — a per-age/type + joint-life
rate table from a current market source.*

## 8. Phased roadmap — issue basis

Each unchecked item becomes one GitHub issue (~½–2 days). Format: item —
*acceptance criterion*. Items within a phase are mostly parallelisable;
phases are dependency-ordered. Labels: `core`, `region:uk`, `data-files`,
`docs`, `gui`, `needs-verification`.

### Phase 1 — Core primitives (no UK anything)

- [ ] 1.1 `Money`/`Rate` value types + rounding policy — *quantization rules
  of §5.2 enforced and property-tested (Hypothesis).*
- [ ] 1.2 `Period` + `FiscalCalendar` protocol + generic annual calendar —
  *periods iterate an arbitrary horizon; birthday-in-period helpers tested
  at boundaries (§4.1).*
- [ ] 1.3 `Fact[T]`, `Assumption[T]`, `Decision[T]`, `AssumptionKey`,
  `AssumptionSet` with read-tracking — *engine-side reads recorded;
  provenance enum round-trips.*
- [ ] 1.4 `Household`/`Person` skeleton — *1–2 persons representable with
  stable `EntityId`s; v1 validator rejects 2 (§4.4).*
- [ ] 1.5 Boundary guard tests — *test fails if `core` imports `regions.*`;
  grep test fails on policy-figure literals outside `regions/uk/data/`.*

### Phase 2 — UK region data and tax

- [ ] 2.1 Promote `regions/uk.py` → package; TOML loader with strict
  validation — *unknown keys, missing meta, or float-typed money are load
  errors (§5.3).*
- [ ] 2.2 `tax_year_2026_27.toml` + `age_rules.toml` +
  `assumptions_default.toml` from §6/§7 — *loader tests pass; `verified_on`
  + `sources` present; doc-sync test keeps §7 aligned with the defaults
  file.*
- [ ] 2.3 rUK income tax assessment (bands + PA taper) — *golden tests match
  hand-worked HMRC examples incl. the £100k–£125,140 zone.*
- [ ] 2.4 `AgeRules`: SPA from DOB (banded), NMPA schedule, LISA ages —
  *boundary tests either side of every band edge, the 2028-04-06 step, and
  the §4.1 access-gate / pro-rated-income convention.*
- [ ] 2.5 Future-year extension policy — *`policy.tax.future_years` drives
  extrapolation past the last data file (§5.3).*

### Phase 3 — Wrappers and accumulation

- [ ] 3.1 Wrapper model (DC/SIPP/ISA) + region `WrapperRuleset` — *tax
  treatment in/during/out resolved per wrapper kind.*
- [ ] 3.2 Contribution schedules: employee/employer, relief at source vs net
  pay — *mechanics match gov.uk worked examples; higher-rate relief via tax
  assessment.*
- [ ] 3.3 Annual allowance + taper + MPAA — *taper arithmetic golden-tested;
  AA measures pension input amounts (incl. employer/DB) separately from the
  member relief limit (§6); MPAA flips on first flexible access, persists,
  and leaves the alternative allowance for DB accrual (§9).*
- [ ] 3.4 Fees and growth application — *applied per §5.2 operation order.*
- [ ] 3.5 Glide-path / life-stage allocation — *allocation interpolates the
  years-to-retirement table; stage derived, not stored.*

### Phase 4 — Deterministic projection (first end-to-end result)

- [ ] 4.1 Engine step loop with specified operation order + `PeriodSnapshot`
  / `ProjectionResult` incl. provenance — *order-of-operations test fixes
  the spec; net-need withdrawals gross up against the tax system (§5.2
  step 4); provenance lists every assumption read.*
- [ ] 4.2 DB pension: revaluation in deferment, NPA, early/late factors,
  commutation — *scheme facts drive results; commutation trades pension for
  lump sum at the stated factor.*
- [ ] 4.3 State pension: forecast-as-fact, qualifying-years derivation, SPA,
  deferral, uprating assumption — *forecast wins; the derivation refuses
  pre-2016 NI records (forecast required, §5.1); protected payments uprate
  by CPI only.*
- [ ] 4.4 Real/nominal reporting layer — *real default; nominal available;
  one CPI path per run.*
- [ ] 4.5 End-to-end golden scenario — *"35-year-old, DC + ISA, retires at
  60" produces a reviewed, checked-in expected output.* Land after 4.6 so
  the golden output is written once against corrected partial-period
  behaviour.
- [ ] 4.6 Partial first/last period pro-rating — *a mid-period `today`
  pro-rates flows (income, contributions, spending need) by whole months
  per §4.1; the growth/fee partial-period convention is decided and
  recorded in §5.2; the run never models time before `today`.*

### Phase 5 — Decumulation

- [ ] 5.1 `WithdrawalStrategy` protocol + fixed-real + fixed-% —
  *strategies respect access ages and wrapper ordering.*
- [ ] 5.2 Tax-free cash strategy: PCLS vs UFPLS vs FAD + LSA tracking —
  *25%/LSA cap enforced; UFPLS payments split 25/75; MPAA triggers fire;
  starting `lsa_used` / `mpaa_triggered_on` / `crystallised_balance` facts
  respected (§5.1).*
- [ ] 5.3 Guardrails + natural-yield strategies — *band crossings adjust
  spending per configured rules.*
- [ ] 5.4 One-off planned outflows — *outflows hit the chosen period,
  tax-aware.*
- [ ] 5.5 Annuity purchase — *level/escalating/inflation-linked,
  single/joint, partial annuitisation mid-drawdown priced from the
  annuity-rate assumption table.*

### Phase 6 — Scenarios and persistence

- [ ] 6.1 `Scenario`/`Override` model + resolution — *base ⊕ overrides with
  `SCENARIO_OVERRIDE` provenance; entity-id targeting; orphaned targets
  flag the scenario invalid without breaking file load (§4.3).*
- [ ] 6.2 `.glidepath.json` schema v1 + canonical reader/writer —
  *round-trip property tests; deterministic byte-identical output (§4.5).*
- [ ] 6.3 Scenario comparison report — *per-period metric diffs across
  scenarios.*
- [ ] 6.4 Schema migration harness — *versioned upgraders; v1→v1 no-op
  wired.*

### Phase 7 — Monte Carlo

- [ ] 7.1 `RandomSource` protocol + seeded impl — *reproducibility property
  test: same inputs + seed → identical result (§4.6).*
- [ ] 7.2 Stochastic `ReturnModel`: lognormal + correlations (Decimal
  Cholesky) — *includes a performance measurement task with recorded
  numbers.*
- [ ] 7.3 Path runner + success metrics — *probability of ruin, sustainable
  income, ending-pot percentiles over paths.*
- [ ] 7.4 Sequence-of-returns fixtures — *same returns, different order →
  demonstrably different outcome.*

### Phase 8 — GUI (PySide6)

- [ ] 8.1 `make deps` PySide6; app shell + **disclaimer screen** —
  *disclaimer on first run (§1).*
- [ ] 8.2 Facts entry forms — *every fact in §5.1 enterable with `as_of`
  dates.*
- [ ] 8.3 Assumptions inspector — *the "stated vs assumed" surface rendered
  from `ProjectionResult.provenance`; defaults overridable in place.*
- [ ] 8.4 Projection charts — *real-terms default, nominal toggle.*
- [ ] 8.5 Scenario manager + diff view — *scenarios as override lists;
  comparison report visualised.*

### Phase 9 — Extensions

- [ ] 9.1 Scottish bands activation — *`tax_residency = SCOTLAND` uses the
  Scottish table already shipped in data.*
- [ ] 9.2 LISA/GIA/cash wrappers — *LISA bonus/charge/ages; GIA brings
  dividend/savings taxation (2026/27 dividend data already verified in §6).*
- [ ] 9.3 New tax-year data file after each Budget — *recurring; process in
  §5.3.*
- [ ] 9.4 Couples activation spike — *survivor benefits, marriage allowance,
  joint annuities scoped; new decision record in §4 before any code.*
- [ ] 9.5 AA carry-forward — *3-year rule per gov.uk guidance.*
- [ ] 9.6 DB active accrual — *accrual rate, pensionable salary and service
  projection for active DB membership (v1 is deferred/accrued only, §2).*

## 9. Open questions

Carried from the 2026-08-01 research pass:

1. **FCA COBS inflation figure** — a handbook mirror showed 2.00% in COBS
   13 Annex 2 2.5R (long-standing value 2.5%); canonical page blocked
   extraction. Re-verify before citing COBS for inflation (the 2/5/8 and
   1.5/4.5/7.5 return maxima were confirmed twice).
2. **NMPA enacting statute** — 2028 date + protections confirmed on the
   gov.uk policy paper; the statute (likely Finance Act 2022) not confirmed
   on a fetched primary page.
3. **AA carry-forward mechanics** — 3-year headline verified; ordering and
   membership rules to re-verify at implementation
   ([guidance](https://www.gov.uk/guidance/check-if-you-have-unused-annual-allowances-on-your-pension-savings)).
4. **Third SPA review deadline** (reported March 2029) — secondary sources
   only; no legislated SPA change as of retrieval.
5. **ONS exact cohort values** — use the 2024-based cohort life tables
   dataset when implementing the longevity default (calculator itself is
   interactive-only).
6. **OBR 50-year determinants** — medium-term EFO figures verified; the
   long-term determinants are in Fiscal risks & sustainability July 2026
   (PDF) — worth extracting for >30-year horizons.
7. **Member relief basic amount** — *resolved 2026-08-02*: £3,600 gross
   (£2,880 net), available via relief at source only, verified on
   [gov.uk pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief)
   and [FA 2004 s190](https://www.legislation.gov.uk/ukpga/2004/12/section/190)
   ("the basic amount is £3,600"). Shipped as
   `pension.member_relief_basic_amount` in the tax-year data file (§6).
8. **Alternative annual allowance** — *resolved 2026-08-02*: the DB-side
   allowance after an MPAA trigger is the (possibly tapered) annual
   allowance minus the MPAA — £50,000 standard, nil at maximum taper —
   computed, never stored as an independent figure; carry-forward may top
   up the alternative AA but never the MPAA. Verified on
   [HS345 (2026)](https://www.gov.uk/government/publications/pensions-tax-charges-on-any-excess-over-the-lifetime-allowance-annual-allowance-special-annual-allowance-and-on-unauthorised-payments-hs345-self/hs345-pension-savings-tax-charges-2026)
   (§6).
