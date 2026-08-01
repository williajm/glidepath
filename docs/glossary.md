# Glossary

> Status: living document · Last updated 2026-08-01 · Back to [planning](planning.md)

One-line definitions of domain terms used across the docs. Other docs link
here instead of re-defining.

## Wrappers and accounts

- **Wrapper** — a tax-privileged container for investments (pension, ISA);
  determines tax treatment on the way in, while invested, and on the way out.
- **DC pension** — defined contribution pension: a pot whose value depends on
  contributions and investment growth (workplace DC, SIPP).
- **SIPP** — self-invested personal pension; a DC pension the individual opens
  and controls directly.
- **DB pension** — defined benefit pension: an employer promise of an annual
  income, defined by salary/service formula rather than a pot.
- **ISA** — individual savings account; contributions from taxed income, no
  tax while invested or on withdrawal.
- **LISA** — Lifetime ISA; state bonus on contributions, restricted access
  (first home or from the access age).
- **GIA** — general investment account; no wrapper, fully taxable.

## Pension access and tax

- **Uncrystallised / crystallised** — pension money not yet / already
  designated for access (accessing it "crystallises" it).
- **PCLS / tax-free cash** — pension commencement lump sum: the tax-free lump
  sum available when crystallising pension benefits.
- **UFPLS** — uncrystallised funds pension lump sum: an ad-hoc lump sum taken
  directly from uncrystallised funds, each payment part tax-free, part taxed.
- **Flexi-access drawdown (FAD)** — crystallise funds (taking tax-free cash),
  leave the rest invested, draw taxable income flexibly.
- **LSA** — lump sum allowance: lifetime cap on tax-free lump sums.
- **LSDBA** — lump sum and death benefit allowance: lifetime cap including
  death benefits.
- **Annual allowance (AA)** — yearly cap on tax-relieved pension contributions,
  tapered for high incomes.
- **MPAA** — money purchase annual allowance: reduced AA triggered by flexibly
  accessing DC funds.
- **Relief at source / net pay** — the two mechanics for pension tax relief:
  provider adds basic-rate relief to net contributions vs contributions
  deducted before tax.
- **NMPA** — normal minimum pension age: earliest age private pensions can
  normally be accessed.
- **Commutation** — exchanging DB annual pension for a lump sum at a
  scheme-set factor.
- **Revaluation** — inflation-linked uplift of a deferred DB pension between
  leaving the scheme and drawing it.
- **NPA** — normal pension age of a DB scheme (unreduced benefits).

## State pension

- **SPA** — state pension age, set by legislation as a function of date of
  birth.
- **Qualifying year** — a tax year of NI contributions/credits counting toward
  state pension entitlement.
- **Triple lock** — uprating policy: highest of earnings growth, CPI, 2.5%.
- **Deferral** — postponing state pension in exchange for a higher rate.

## Modelling

- **Fact** — a value the user stated (DOB, balances, contributions, accrued DB,
  NI record). See [domain model](design/domain-model.md).
- **Assumption** — a value the app defaulted or estimated (returns, inflation,
  annuity rates, future tax rules, longevity); always carries value, source,
  date recorded, and default/overridden provenance.
- **Scenario** — a named set of overrides applied to the base plan for
  what-if comparison. See [ADR-0003](adr/0003-scenario-model.md).
- **Glide path** — planned shift of asset allocation (typically de-risking)
  as retirement approaches; the app's namesake.
- **Decumulation / drawdown** — the phase of spending down assets in
  retirement.
- **Real vs nominal** — inflation-adjusted (today's money) vs cash-of-the-day
  amounts; the app reports real by default.
- **Sequence-of-returns risk** — the risk that poor returns early in
  drawdown permanently impair a portfolio even if average returns are fine.
- **Guardrails** — a withdrawal strategy that adjusts spending up/down when
  the withdrawal rate crosses preset bands (Guyton–Klinger style).
- **Natural yield** — spending only the income (dividends/interest) a
  portfolio produces.
- **Probability of ruin** — share of simulated paths in which the portfolio
  is exhausted before the end of the planning horizon.
- **Sustainable income** — the highest starting withdrawal meeting a target
  success rate over the horizon.
