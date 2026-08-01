# ADR-0006: Engine purity and reproducibility

> Status: **Proposed** · 2026-08-01 · [Index](README.md)

## Context

The projection engine must be trustworthy (a user is planning their
retirement on it), testable to the repo's ≥90% coverage bar without mocking
gymnastics, and — once Monte Carlo lands — exactly reproducible so results
can be revisited and paths debugged. CLAUDE.md mandates `Decimal` for money.

## Decision

- The engine is a library of **pure typed functions over frozen
  dataclasses**: no I/O, no clock reads (`today` is an input), no global
  state, no module-level mutability.
- **`Decimal` end-to-end.** Money is quantized to pennies with
  `ROUND_HALF_EVEN` at every ledger write; rates and intermediate factors
  stay unquantized. The policy lives in
  [projection-engine.md](../design/projection-engine.md).
- **Randomness only by injection** through a `RandomSource` protocol. The
  production implementation wraps `random.Random(seed)`. The seed is part of
  `RunConfig` and is persisted with results.
- Monte Carlo paths draw from **substreams derived from `(seed,
  path_index)`**, so paths are order-independent and parallelisable later,
  and any single path can be re-run in isolation.
- Reproducibility is a **tested guarantee**: a Hypothesis property test
  asserts `(plan, assumptions, region data version, seed) → identical
  output`.

## Rationale

Purity is what makes facts/assumptions provenance trustworthy — a result can
only depend on declared inputs — and makes high coverage cheap (no mocks,
just values in/values out). Seeded injection makes MC runs reproducible and
debuggable ("re-run path 4711"). Decimal end-to-end honours the repo rule
and eliminates float drift between deterministic and Monte Carlo modes.

## Alternatives considered

- **numpy vectorised MC** — rejected: runtime dependency, float-based
  (breaks the Decimal rule), and complicates the "same model, both modes"
  requirement.
- **Module-level `random`** — rejected: irreproducible, untestable.
- **Floats internally, Decimal at the edges** — rejected: violates repo
  policy and reintroduces drift precisely where trust matters.

## Consequences

- Decimal Monte Carlo is slow relative to numpy. Accepted trade-off:
  annual steps and small state keep 10k paths × 60 years tractable; a
  performance measurement task is scheduled in the Monte Carlo phase
  ([planning](../planning.md)) before any supersession is considered.
- `today`-as-input means the UI layer owns the clock; tests pin dates
  trivially.
