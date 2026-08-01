# Architecture decision records

> Status: living index · Last updated 2026-08-01 · Back to [planning](../planning.md)

## Format

Each ADR has: **Context** (the forces), **Decision** (what we choose),
**Rationale**, **Alternatives considered** (and why rejected),
**Consequences** (costs we accept), **Status**.

Statuses: `Proposed` → `Accepted` → (`Superseded by ADR-NNNN`).

## Lifecycle rules

- ADRs are immutable once **Accepted**: changing a decision requires a new
  superseding ADR, not an edit.
- `Proposed` ADRs await explicit approval from the project owner.
- Design docs under [`docs/design/`](../design/) record the *resulting
  specification*; ADRs record the *decision and why*. Don't duplicate.

## Index

| ADR | Title | Status |
| --- | ----- | ------ |
| [0001](0001-time-step-and-calendar.md) | Time step and calendar | Proposed |
| [0002](0002-core-region-boundary.md) | Core/region boundary | Proposed |
| [0003](0003-scenario-model.md) | Scenario model | Proposed |
| [0004](0004-household-and-couples.md) | Household and couples | Proposed |
| [0005](0005-persistence-format.md) | Persistence format | Proposed |
| [0006](0006-engine-purity-and-reproducibility.md) | Engine purity and reproducibility | Proposed |
