# ADR-0001: Time step and calendar

> Status: **Proposed** · 2026-08-01 · [Index](README.md)

## Context

The engine projects a person's finances over decades. UK tax is assessed per
tax year (6 April – 5 April); ages change on birthdays; users think in
calendar years. We must pick a step size and a single convention reconciling
the three calendars, and the choice shapes every state variable, test, and
result table.

## Decision

- **Annual time steps**, where each step is a *period* supplied by the
  region's `FiscalCalendar` protocol. For the UK region a period is a tax
  year; the core engine never knows what "6 April" is.
- **Age convention:** an age-triggered event (NMPA, SPA, LISA access, DB NPA)
  takes effect in the period *in which the relevant birthday falls*, from the
  start of that period's modelling of the event. One convention, documented
  in [projection-engine.md](../design/projection-engine.md), applied
  everywhere.
- **Partial periods** (starting work, retiring, dying mid-year) are pro-rated
  by whole months as a simple `Decimal` fraction; no sub-stepping.

## Rationale

All UK tax, allowances, and contributions are assessed per tax year, so an
annual tax-year step makes tax computation exact where it matters most and
keeps engine state small and auditable. Monthly stepping multiplies state
12×, forces intra-year tax accrual estimates, and adds precision the input
assumptions (long-run return guesses) cannot support. Pushing the calendar
behind a region protocol keeps the core reusable for regions with calendar
fiscal years.

## Alternatives considered

- **Monthly steps** — rejected: false precision, 12× state and runtime, tax
  must still be annualised, and every test fixture grows 12×.
- **Calendar-year steps** — rejected: permanent mismatch with UK tax
  assessment; every allowance would straddle two periods.
- **Birthday-anniversary years** — rejected: mismatches tax years *and*
  breaks for households where members have different birthdays.

## Consequences

- Intra-year sequence-of-returns effects are invisible in v1 (annual returns
  only). Accepted; Monte Carlo still captures year-order sequence risk. A
  future ADR could introduce monthly sub-steps if ever justified by evidence.
- Everything age-related needs boundary tests around the birthday-in-period
  convention.
