# ADR-0002: Core/region boundary

> Status: **Proposed** · 2026-08-01 · [Index](README.md)

## Context

CLAUDE.md mandates that nothing UK-specific leaks into the core engine, so
other regions can be added later. The boundary must be exact — vague
"keep it separate" rules erode — and mechanically enforceable under
mypy `--strict` and tests.

## Decision

- `glidepath.core` defines **typed Protocols** that cross the boundary:
  - `FiscalCalendar` — maps dates to the region's period schedule.
  - `TaxSystem` — `assess(period, TaxInput) -> TaxResult`, where `TaxInput`
    is a *generic* categorised breakdown (earned income, pension income,
    savings income, withdrawals by wrapper kind) and `TaxResult` is tax due
    plus an explanation breakdown. No UK band names in core.
  - `WrapperRuleset` — contribution limits, relief mechanics, access-age
    checks, and in/during/out tax treatment per wrapper kind.
  - `StatePensionScheme` — entitlement from user facts plus an uprating
    assumption.
  - `AgeRules` — access ages (minimum pension age, state pension age, etc.)
    as functions of date of birth.
- Core also owns the region-agnostic value types: `Money`, `Rate`, `Period`,
  `Fact`, `Assumption` (see [domain-model.md](../design/domain-model.md)).
- `glidepath.regions.uk` is a **package** (promoted from `uk.py`)
  implementing the protocols, loading every policy figure from data files
  ([uk-region.md](../design/uk-region.md)).
- A `Region` aggregate (the protocol implementations + data-file version) is
  injected into the engine at run construction.
- **Dependency direction is region → core only**, enforced by a test that
  asserts `glidepath.core` never imports `glidepath.regions.*`, plus a guard
  test that no UK policy figure literal exists outside the region data files.

## Rationale

Protocol injection is the one structure mypy `--strict` fully verifies while
keeping core imports clean, and it makes the CLAUDE.md rule checkable by a
cheap test instead of code review vigilance. Generic `TaxInput`/`TaxResult`
shapes keep the interface honest for a hypothetical second region — if a
concept only exists in the UK, it belongs behind the protocol, not in it.

## Alternatives considered

- **Abstract base classes inherited by the engine** — rejected: couples
  engine internals to region types and invites region logic creeping into
  shared base methods.
- **Entry-point plugin discovery** — rejected: over-engineering for one
  in-tree region; revisit only if third-party regions ever matter.
- **Region enum + branches in core** — rejected outright; explicitly banned
  by repo policy.

## Consequences

- Some UK concepts (e.g. MPAA) surface in core as *generic* hooks (an
  "access event may reduce future contribution allowance" capability) —
  designing those hooks generically costs thought up front.
- A second region is cheap to add; until then the protocols have a single
  implementation, which mildly increases indirection.
