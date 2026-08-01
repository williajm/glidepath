# Glidepath planning

> Status: master plan — implementation issues are raised from this doc ·
> Last updated 2026-08-01

## Vision and product principles

Glidepath is a desktop retirement/investment planner: model a person moving
through life stages — accumulation, de-risking glide path, decumulation —
under explicit, inspectable inputs.

1. **Facts vs assumptions is the product.** Every number is either a fact
   the user stated or an assumption the app made — first-class in the data
   model, carried through every projection, always surfaceable in the UI.
   See [domain-model.md §1](design/domain-model.md).
2. **Not financial advice.** Glidepath is a personal modelling tool, not
   regulated financial advice. A disclaimer to this effect is a **product
   requirement**: it must appear in the UI (first run and About), in any
   exported output, and in the README.
3. **Private by construction.** All user data stays on the user's machine;
   nothing is transmitted ([ADR-0005](adr/0005-persistence-format.md)).
4. **Region-agnostic core.** UK specifics live in `regions/uk` behind typed
   protocols ([ADR-0002](adr/0002-core-region-boundary.md)); policy figures
   live in data files, never logic ([uk-region.md](design/uk-region.md)).

## Scope

| | Contents |
| --- | --- |
| **v1** | Single person, UK (rUK tax). Wrappers: workplace DC, SIPP, S&S ISA. DB pensions. State pension. Deterministic annual projection. Withdrawal strategies: fixed real, fixed %. Scenarios + comparison. JSON persistence. |
| **Deferred (phased)** | Monte Carlo; guardrails + natural yield; annuities (incl. partial annuitisation); LISA/GIA/cash wrappers; Scottish bands (designed-for now); dividend/savings taxation (needs GIA); AA carry-forward; couples activation ([ADR-0004](adr/0004-household-and-couples.md)); announced future rules (2027 cash-ISA reform, 2029 salary-sacrifice NICs). |
| **Out of scope** | Financial advice or recommendations; live market data; non-UK regions (architecture allows them later); protected pension ages (noted in UI copy). |

## Architecture overview

```
┌──────────────┐   ┌───────────────────┐   ┌──────────────────────────┐
│  GUI (PySide6)│──▶│ Scenario layer     │──▶│ Core engine (pure,       │
│  facts entry, │   │ base ⊕ overrides   │   │ Decimal, seeded RNG)     │
│  "stated vs   │   │ (ADR-0003)         │   │ run(plan, assumptions,   │
│  assumed" view│   └───────────────────┘   │     region, config)      │
└──────────────┘                            └───────────┬──────────────┘
        ▲                                               │ typed protocols
        │ provenance record                             ▼ (ADR-0002)
┌──────────────┐                            ┌──────────────────────────┐
│ .glidepath.json│◀──────────────────────── │ regions/uk  ◀── TOML data │
│ (local only)  │        ADR-0005           │ (tax years, age rules)   │
└──────────────┘                            └──────────────────────────┘
```

Decision records: [ADR index](adr/README.md). Specs:
[domain model](design/domain-model.md) ·
[projection engine](design/projection-engine.md) ·
[UK region](design/uk-region.md). Defaults and verified figures:
[assumptions register](assumptions.md). Terms: [glossary](glossary.md).

## Phased roadmap

Each unchecked item below becomes one GitHub issue (~½–2 days). Format:
item — *acceptance criterion*. Items within a phase are mostly
parallelisable; phases are ordered by dependency.

### Phase 1 — Core primitives (no UK anything)

- [ ] 1.1 `Money`/`Rate` value types + rounding policy — *quantization rules
  of [projection-engine.md §6](design/projection-engine.md) enforced and
  property-tested (Hypothesis).*
- [ ] 1.2 `Period` + `FiscalCalendar` protocol + a generic annual calendar —
  *periods iterate over an arbitrary horizon; birthday-in-period convention
  helpers tested at boundaries ([ADR-0001](adr/0001-time-step-and-calendar.md)).*
- [ ] 1.3 `Fact[T]`, `Assumption[T]`, `AssumptionKey`, `AssumptionSet` with
  read-tracking — *engine-side reads are recorded; provenance enum round-trips.*
- [ ] 1.4 `Household`/`Person` skeleton — *1–2 persons representable; v1
  validator rejects 2 ([ADR-0004](adr/0004-household-and-couples.md)).*
- [ ] 1.5 Boundary guard tests — *test fails if `core` imports `regions.*`;
  grep test fails on policy-figure literals outside `regions/uk/data/`.*

### Phase 2 — UK region data and tax

- [ ] 2.1 Promote `regions/uk.py` → package; TOML loader with strict
  validation — *unknown keys, missing meta, or float-typed money are load
  errors ([uk-region.md §2](design/uk-region.md)).*
- [ ] 2.2 `tax_year_2026_27.toml` + `age_rules.toml` populated from the
  verified figures in [assumptions.md](assumptions.md) — *loader tests pass;
  `verified_on` + `sources` present.*
- [ ] 2.3 rUK income tax assessment (bands + PA taper) — *golden tests match
  hand-worked HMRC examples incl. the £100k–£125,140 taper zone.*
- [ ] 2.4 `AgeRules`: SPA from DOB (banded), NMPA schedule, LISA ages —
  *boundary tests either side of every band edge and the 2028-04-06 NMPA step.*
- [ ] 2.5 Future-year extension policy — *`policy.tax.future_years` assumption
  (frozen vs CPI-indexed) drives band extrapolation past the last data file
  ([uk-region.md §3](design/uk-region.md)).*

### Phase 3 — Wrappers and accumulation

- [ ] 3.1 Wrapper model (DC/SIPP/ISA) + region `WrapperRuleset` descriptors —
  *tax treatment in/during/out resolved per wrapper kind.*
- [ ] 3.2 Contribution schedules: employee/employer, relief at source vs net
  pay — *RAS top-up and net-pay mechanics match gov.uk worked examples;
  higher-rate relief handled via tax assessment.*
- [ ] 3.3 Annual allowance + taper + MPAA — *taper arithmetic golden-tested;
  MPAA state flips on first flexible access and persists.*
- [ ] 3.4 Fees and growth application — *platform + fund fees applied per
  [projection-engine.md §2](design/projection-engine.md) order.*
- [ ] 3.5 Glide-path / life-stage allocation — *allocation interpolates the
  years-to-retirement table; stage derived, not stored.*

### Phase 4 — Deterministic projection (first end-to-end result)

- [ ] 4.1 Engine step loop with specified operation order + `PeriodSnapshot`
  / `ProjectionResult` incl. provenance — *order-of-operations test fixes the
  spec; provenance lists every assumption read.*
- [ ] 4.2 DB pension: revaluation in deferment, NPA, early/late factors,
  commutation — *user-entered scheme facts drive results; commutation trades
  pension for lump sum at the stated factor.*
- [ ] 4.3 State pension: forecast-as-fact, qualifying-years derivation, SPA,
  deferral, uprating assumption — *forecast wins over derivation when present.*
- [ ] 4.4 Real/nominal reporting layer — *real is default; nominal available;
  one CPI path per run.*
- [ ] 4.5 End-to-end golden scenario — *"35-year-old, DC + ISA, retires at
  60" produces a reviewed, checked-in expected output.*

### Phase 5 — Decumulation

- [ ] 5.1 `WithdrawalStrategy` protocol + fixed-real + fixed-% — *strategies
  respect access ages and wrapper ordering.*
- [ ] 5.2 Tax-free cash strategy: PCLS vs UFPLS vs FAD + LSA tracking —
  *25%/LSA cap enforced; UFPLS payments split 25/75; MPAA triggers fire.*
- [ ] 5.3 Guardrails + natural-yield strategies — *guardrail band crossings
  adjust spending per configured rules.*
- [ ] 5.4 One-off planned outflows — *outflows hit the chosen period, tax-aware.*
- [ ] 5.5 Annuity purchase — *level/escalating/inflation-linked, single/joint,
  partial annuitisation mid-drawdown priced from the annuity-rate assumption
  table.*

### Phase 6 — Scenarios and persistence

- [ ] 6.1 `Scenario`/`Override` model + resolution — *base ⊕ overrides with
  `SCENARIO_OVERRIDE` provenance ([ADR-0003](adr/0003-scenario-model.md)).*
- [ ] 6.2 `.glidepath.json` schema v1 + canonical reader/writer — *round-trip
  property tests; deterministic byte-identical output.*
- [ ] 6.3 Scenario comparison report — *per-period metric diffs across
  scenarios.*
- [ ] 6.4 Schema migration harness — *versioned upgraders; v1→v1 no-op wired.*

### Phase 7 — Monte Carlo

- [ ] 7.1 `RandomSource` protocol + seeded impl — *reproducibility property
  test: same inputs+seed → identical result ([ADR-0006](adr/0006-engine-purity-and-reproducibility.md)).*
- [ ] 7.2 Stochastic `ReturnModel`: lognormal + correlations (Decimal
  Cholesky) — *includes a performance measurement task with recorded numbers.*
- [ ] 7.3 Path runner + success metrics — *probability of ruin, sustainable
  income, ending-pot percentiles computed over paths.*
- [ ] 7.4 Sequence-of-returns fixtures — *same returns, different order →
  demonstrably different outcome.*

### Phase 8 — GUI (PySide6)

- [ ] 8.1 `make deps` PySide6; app shell + **disclaimer screen** — *disclaimer
  shown on first run; product requirement above.*
- [ ] 8.2 Facts entry forms — *every fact from
  [domain-model.md](design/domain-model.md) enterable with `as_of` dates.*
- [ ] 8.3 Assumptions inspector — *the "stated vs assumed" surface, rendered
  from `ProjectionResult.provenance`; defaults overridable in place.*
- [ ] 8.4 Projection charts — *real-terms default, nominal toggle.*
- [ ] 8.5 Scenario manager + diff view — *create/edit scenarios as override
  lists; comparison report visualised.*

### Phase 9 — Extensions

- [ ] 9.1 Scottish bands activation — *`tax_residency = SCOTLAND` uses the
  Scottish table already shipped in data.*
- [ ] 9.2 LISA/GIA/cash wrappers — *LISA bonus/charge/ages; GIA brings
  dividend/savings taxation (data already shipped for 2026/27).*
- [ ] 9.3 New tax-year data file after each Budget — *recurring; process in
  [uk-region.md §4](design/uk-region.md).*
- [ ] 9.4 Couples activation spike → new ADR — *survivor benefits, marriage
  allowance, joint annuities scoped before any code.*
- [ ] 9.5 AA carry-forward — *3-year rule per gov.uk guidance.*

Issue labels: `core`, `region:uk`, `data-files`, `docs`, `gui`,
`needs-verification`.

## UK figure verification status

All current-figure verification lives in the
[assumptions register](assumptions.md) with per-figure source URLs and
retrieval dates. Status at 2026-08-01:

| Group | Status |
| --- | --- |
| Income tax 2026/27 (PA, taper, rUK + Scottish bands, freeze to 2031) | Verified |
| Dividend/savings/property announced changes (2026/27, 2027/28) | Verified |
| Pension allowances (AA, taper, MPAA, LSA, LSDBA, relief mechanics) | Verified |
| Pension access (UFPLS/FAD mechanics, NMPA → 57 on 2028-04-06) | Verified |
| ISA/LISA (allowances, bonus, ages, charge; 2027 cash-ISA reform) | Verified |
| State pension (rate, uprating, qualifying years, deferral, SPA bands) | Verified |
| Assumption bases (OBR, FCA, ONS longevity, annuity benchmarks) | Recorded (see register's open questions) |

## Doc map

| Doc | Contents | Update when |
| --- | --- | --- |
| this file | Scope, roadmap, principles | Scope or phasing changes |
| [assumptions.md](assumptions.md) | Verified figures + default assumptions | Any figure verified/changed; new default |
| [adr/](adr/README.md) | Decisions | New decision; supersession |
| [design/domain-model.md](design/domain-model.md) | Entities, Fact/Assumption typing | Model changes |
| [design/projection-engine.md](design/projection-engine.md) | Engine spec | Engine semantics change |
| [design/uk-region.md](design/uk-region.md) | UK rules + data format | Rules/format change; new tax year |
| [glossary.md](glossary.md) | Terms | New term |
