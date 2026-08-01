# ADR-0004: Household and couples

> Status: **Proposed** · 2026-08-01 · [Index](README.md)

## Context

v1 targets a single person, but real retirement planning is often a couple.
UK tax is individual; household spending is shared. A pure single-person
data model would make couples support a schema-and-engine rewrite later —
the corner we must not paint ourselves into.

## Decision

- The domain model, persistence schema, and engine signatures are written
  over `Household` from day one: `Household.persons: list[Person]`
  (1–2 members).
- Everything taxed or age-gated hangs off a `Person`: wrappers, DB pensions,
  NI record/state pension, tax assessment, age rules.
- Shared economics hang off the `Household`: `SpendingPlan`, planned one-off
  outflows, success metrics.
- **v1 enforces `len(persons) == 1`** at the application layer. No couples
  UI, no inter-person transfers, no survivor/death modelling in v1.
- Couples *activation* is a later spike with its own ADR (survivor benefits,
  marriage allowance, joint annuities, death modelling).

## Rationale

UK tax being individual means the computation layer is naturally per-person
anyway; the only real fork is where spending and goals live. Putting them at
household level costs nothing now, while retrofitting them later would touch
the persistence schema and every engine signature. Iterating a one-element
list is free; migrating every saved plan file is not. The genuinely hard
couples features are deferred, not half-designed.

## Alternatives considered

- **Pure single-person v1** — rejected: cheap now, expensive forever; the
  file-format migration alone justifies the list.
- **Full couples support in v1** — rejected: drags survivor pensions and
  death modelling into scope prematurely and doubles the v1 domain surface.

## Consequences

- Slight indirection everywhere (`household.persons[0]`) until couples land.
- Success metrics and spending are defined at household level from the
  start, so their semantics won't change when a second person appears.
- The v1 validator is the single place that flips when couples activate.
