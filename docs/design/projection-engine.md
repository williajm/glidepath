# Projection engine

> Status: design spec (pre-implementation) · Last updated 2026-08-01 ·
> Back to [planning](../planning.md) · Terms: [glossary](../glossary.md)

## 1. Contract

```python
def run(
    plan: Plan, assumptions: AssumptionSet, region: Region, config: RunConfig
) -> ProjectionResult: ...
```

Pure and deterministic ([ADR-0006](../adr/0006-engine-purity-and-reproducibility.md)):
same inputs (including `config.seed` and the region data version) → identical
output, byte for byte. `config` carries: `today`, horizon end (default from
the longevity assumption), mode (deterministic | monte-carlo), seed, number
of paths, withdrawal strategy selection.

The **same step function** runs under both modes; only the `ReturnModel`
differs (§5). This is a design invariant, not an aspiration — Monte Carlo
must not fork the model.

## 2. Period loop and order of operations

The engine iterates periods from the region's `FiscalCalendar`
([ADR-0001](../adr/0001-time-step-and-calendar.md): annual, tax-year
aligned for the UK). **The order of operations within a period is part of
the specification** and gets its own tests:

1. **Open period** — resolve ages (birthday-in-period convention), life
   stage, glide-path allocation; apply age events (NMPA/SPA/LISA access
   becoming available, DB NPA reached).
2. **Income** — DB pension in payment (with revaluation/uprating), state
   pension (with uprating assumption), annuity income, employment income.
3. **Contributions** — employee + employer amounts, relief mechanics
   (region), allowance checks (AA/taper/MPAA via region ruleset).
4. **Withdrawals** — per the selected withdrawal strategy (§4), respecting
   wrapper access rules and the tax-free cash strategy.
5. **Tax** — one `TaxSystem.assess(period, TaxInput)` call per person with
   the period's full categorised income picture.
6. **Fees** — platform + fund fees on average balances.
7. **Growth** — apply the period's returns from the `ReturnModel` to each
   wrapper's allocation.
8. **Close period** — quantize ledger, emit `PeriodSnapshot`.

Rationale for the order: income and contributions must precede tax (tax
needs the full picture); fees before growth approximates intra-year accrual
acceptably at annual resolution; documented so results are explainable.

`PeriodSnapshot` records per person and per wrapper: opening/closing
balances, flows by category, tax paid with breakdown, ages, stage,
allocation. `ProjectionResult` = snapshots + summary metrics + the
provenance record ([domain-model.md §1](domain-model.md)).

## 3. Real vs nominal

The engine computes in **nominal** terms (tax bands are nominal objects).
The reporting layer deflates by the CPI assumption path to **real (today's
money), which is the default presentation**; nominal is available. Real
discounting uses the same CPI path the projection used — one inflation
truth per run.

## 4. Withdrawal strategies

A strategy protocol decides each period's withdrawal per wrapper:

```python
class WithdrawalStrategy(Protocol):
    def withdraw(self, state: PeriodState, need: Money) -> WithdrawalPlan: ...
```

v1 ships: **fixed real amount**, **fixed percentage**. Next: **guardrails**
(Guyton–Klinger-style bands), **natural yield**. Strategies also encode
withdrawal *ordering* across wrappers (default: GIA/cash → ISA → pension,
tax-aware; configurable) and the tax-free cash strategy (PCLS up front vs
UFPLS-style phased — mechanics from the region ruleset).

## 5. Return model and Monte Carlo

```python
class ReturnModel(Protocol):
    def returns_for(self, period: Period, path: int) -> AssetReturns: ...
```

- **Deterministic**: expected real return per asset class (from
  assumptions) + CPI → nominal returns; same every path.
- **Stochastic (Monte Carlo phase)**: lognormal draws per asset class with
  the assumed volatilities and correlation matrix (Cholesky, in `Decimal`;
  a performance measurement task precedes optimisation). Randomness comes
  only from the injected `RandomSource`; path `i` uses substream
  `(seed, i)` so paths are order-independent and individually re-runnable.

Success metrics over paths: **probability of ruin** (portfolio exhausted
before horizon), **sustainable income** (highest starting withdrawal meeting
a target success rate, found by bisection over deterministic-per-path runs),
**ending pot distribution** (percentiles). Sequence-of-returns risk is
demonstrated by fixtures: same return set, different order → different
outcome.

## 6. Decimal and rounding policy

- Money: `Decimal` quantized to pennies, `ROUND_HALF_EVEN`, at every ledger
  write (i.e. whenever a value lands in a snapshot or balance).
- Rates, factors, intermediate products: unquantized `Decimal`.
- No floats anywhere in the engine; region data files parse straight to
  `Decimal` ([uk-region.md](uk-region.md)).
