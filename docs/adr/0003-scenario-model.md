# ADR-0003: Scenario model

> Status: **Proposed** · 2026-08-01 · [Index](README.md)

## Context

Users need a base case plus what-if scenarios ("retire at 58", "returns 1%
lower", "pay off the mortgage at 55") and a clear answer to "what is
different about this scenario?". The representation determines whether
scenario diffs are computed or simply *are* the data, and how edits to base
facts propagate.

## Decision

A user file contains **one base `Plan`** (facts) and **one base
`AssumptionSet`**, plus zero or more named `Scenario`s. A scenario is a
**typed override set**: a list of `Override(target, value, note)` records
where `target` is either

- an `AssumptionKey` (any assumption may be overridden), or
- one of a **whitelisted set of plan-level what-if fields** (target
  retirement age, contribution rates, planned one-off outflows, withdrawal
  strategy and parameters, annuitisation decisions).

Effective inputs for a scenario = base ⊕ overrides, resolved at run time.
An assumption overridden by a scenario carries provenance
`SCENARIO_OVERRIDE` (see [domain-model.md](../design/domain-model.md)).
Scenario results are compared via a `ScenarioComparison` report over key
per-period metrics.

## Rationale

Storing scenarios as deltas means "what's different?" is a direct read of
the data — no structural diffing — and files stay small and human-diffable
([ADR-0005](0005-persistence-format.md)). Edits to base facts automatically
propagate to every scenario, which is the behaviour users expect ("I
corrected my ISA balance; all what-ifs update"). It also composes with the
facts/assumptions principle: a scenario override is still an assumption with
full provenance.

## Alternatives considered

- **Deep-copied plan per scenario** — rejected: silent drift from base,
  no provenance, requires structural diffing to answer the core question.
- **Event-sourced command log** — rejected: heavy machinery, hard to keep
  human-readable, no user-facing benefit at this scale.
- **Scenario DSL / code** — rejected: unserialisable, untypeable, and a
  support burden.

## Consequences

- The what-if whitelist must grow deliberately; a what-if that can't be
  expressed as an override needs a model change (feature, not bug — it keeps
  scenarios tractable).
- Facts themselves are *not* scenario-overridable (a different balance is a
  different plan, not a scenario); the whitelist is the escape hatch for
  plan-shaped what-ifs.
