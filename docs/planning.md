# Glidepath planning

> Status: master planning document — implementation issues are raised from
> §8 · Last updated 2026-08-04 · All UK figures verified against primary
> sources on 2026-08-01, with later re-verifications dated per row (§6).

Contents: [1 Vision](#1-vision-and-product-principles) ·
[2 Scope](#2-scope) · [3 Architecture](#3-architecture) ·
[4 Decisions](#4-decision-records) ·
[5 Design](#5-design) · [6 Verified figures](#6-verified-uk-policy-figures-202627) ·
[7 Default assumptions](#7-default-assumptions) ·
[8 Roadmap](#8-phased-roadmap--issue-basis) ·
[9 Open questions](#9-open-questions)

---

## 1. Vision and product principles

Glidepath is a desktop retirement/investment planner: model a person moving
through life stages — accumulation, de-risking glide path (the namesake),
decumulation — under explicit, inspectable inputs.

1. **Facts vs assumptions is the product.** Every number is either a
   **fact** the user stated (DOB, balances, contributions, accrued DB
   entitlement, state pension forecast) or an **assumption** the app
   defaulted or
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
| **v1** | Single person, UK (rUK + Scottish tax). Wrappers: workplace DC, SIPP, S&S ISA; LISA, GIA and cash with dividend/savings taxation (9.2). DB pensions: deferred/accrued entitlements, plus active membership with CARE-style accrual (accrual rate, pensionable salary, service projection — 9.6). State pension incl. deferral. Deterministic and Monte Carlo annual projection (one step function, two return models — §5.2). Withdrawal strategies: fixed real, fixed %, guardrails, natural yield. Annuity purchases incl. partial annuitisation. "When can I retire?" solver: earliest retirement age meeting a replacement-rate target, deterministic or Monte Carlo (§5.2, 9.14). Scenarios + comparison. JSON persistence. |
| **Deferred (phased)** | Couples activation in increments per the §4.11 decision record (spike run 2026-08-11; roadmap 9.29–9.34 — optional partner, pooled decumulation, marriage allowance, deterministic survivor modelling, joint-life annuities); announced future rules shipping as data in their year's files (2027 cash-ISA reform and savings rates, 2029 salary-sacrifice NICs); final-salary linkage and split deferment/in-payment revaluation bases for DB schemes (9.6 ships CARE-style accrual on the single basis). |
| **Out of scope** | Advice or recommendations; live market data; non-UK regions (architecture allows later); web UI (v1 is desktop; the app layer keeps one possible later, §4.7); protected pension ages (noted in UI copy); capital gains tax — the GIA models dividend and savings *income* only (9.2), never disposals. |

## 3. Architecture

```
┌───────────────┐   ┌───────────────┐   ┌───────────────────┐   ┌──────────────────────────┐
│ GUI shell     │──▶│ App layer     │──▶│ Scenario layer    │──▶│ Core engine (pure,       │
│ (PySide6 v1;  │   │ view models,  │   │ base ⊕ overrides  │   │ Decimal, seeded RNG)     │
│ web possible  │   │ copy, format- │   │ (§4.3)            │   │ run(plan, assumptions,   │
│ later, §4.7)  │   │ ting (no Qt)  │   └───────────────────┘   │     region, config)      │
└───────────────┘   └───────────────┘                           └───────────┬──────────────┘
        ▲ "stated vs assumed" view                                          │ typed protocols
        │ provenance record                                                 ▼ (§4.2)
┌────────────────┐                                              ┌──────────────────────────┐
│ .glidepath.json│◀───────────────────────────────────────────  │ regions/uk ◀── TOML data │
│ (local only)   │        (§4.5)                                │ (tax years, age rules)   │
└────────────────┘                                              └──────────────────────────┘
```

## 4. Decision records

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

Other partial years (a run starting mid-year, an eligibility window
opening or closing mid-period) are pro-rated by whole months as a
`Decimal` fraction the same way; no sub-stepping.

- **Retirement is a whole-period gate**, not a pro-rated event: a
  person counts as retired for a period only when the target
  retirement age is attained by the period's first day, so employment
  income, contributions, and DB accrual run whole-period until then
  (someone born 7 April retiring "at 60" is modelled as employed for
  the whole tax year their birthday falls just inside). Recorded
  convention (#186): unlike the access gates, whose delay is
  conservative, delaying *retirement* models up to one extra year of
  salary, contributions, and accrual — a non-conservative direction
  for post-6-April birthdays, accepted for the same annual-granularity
  reasons; a user can state the earlier whole age to stay
  conservative.

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
(`Fact[T]` — balances, DOB, the DWP forecast, accrued DB) are **never**
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
record. *The spike has since run — §4.11 records the activation
decisions (roadmap 9.29–9.34). The single-person gate is retired: the
engine runs two persons as of 9.30 and the facts form enters the
optional partner as of 9.31, so the schema's 1..2 bound is the only
person-count rule left.*

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
to the seed argument. Normal draws come from `random()` uniforms (the one
generator sequence Python guarantees stable across versions) transformed
by Marsaglia's polar method computed in `Decimal` — never the libm-backed
float distribution methods (`gauss` et al.), whose last-bit rounding
varies by platform — so identical seeds give bit-identical draws across
Python versions and operating systems (this checkout runs on both Windows
and WSL). Reproducibility is defined over a **run manifest**
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
Measured 2026-08-03 (roadmap 7.2; Windows 11, Python 3.14.6, via
`scripts/measure_mc_performance.py`): one stochastic `returns_for` draw
≈ 146 µs; one 60-period engine pass ≈ 28 ms; projected per-path cost
≈ 36 ms → ≈ 4 s per 100 paths, ≈ 36 s per 1,000 paths. Re-measured
end-to-end 2026-08-03 with the 7.3 path runner (`run_paths`, same
persona): ≈ 38 ms per path — ≈ 3.8 s per 100 paths, ≈ 38 s per 1,000
paths, matching the 7.2 projection. Within the accepted envelope; any
optimisation revisit starts from these recorded numbers.

That revisit shipped as roadmap 9.15, measured before and after
2026-08-05 (same machine and script). Profiling showed ≈ 60% of a
Monte Carlo run was redundant tax-year synthesis — every lookup past
the last shipped file re-derived the same few dozen future years — not
the stochastic draws, so the fix was memoization plus process-level
parallelism, never floats: the Decimal-money invariant and the
one-step-function rule stand. After caching `extend_tax_year` (with a
meta-only `TaxYearFile` hash so cache lookups stop walking the figure
tree): engine pass ≈ 16.5 ms, serial per-path ≈ 30 ms on the golden
persona (≈ 31 ms on the heavier launch-example plan, down from
≈ 56 ms). With the chunked `run_paths` process pool (19 workers here;
the app layer uses every core but one, spawn-and-warm ≈ 0.5 s):
≈ 3.1 ms per path steady — ≈ 0.8 s per 100 paths, ≈ 3.6 s per 1,000
paths, ≈ 32 s per 10,000 paths including spawn, and the 9.14 search's
20,000 path-projection budget lands in ≈ 1 minute, down from ≈ 13.
Results are bit-identical to the serial run (paths are pure functions
of `(seed, i)`; chunks recombine in path order — pinned by test).

### 4.7 UI architecture: thin desktop shell over a UI-agnostic app layer

**Decision.** v1 ships a PySide6 desktop GUI, but a web UI is a plausible
future, so presentation logic is split in two:

- `glidepath.app` — the **application layer**: view models, app state,
  user-facing copy (including the §1 disclaimer text), and formatting
  (Decimal→display, real/nominal presentation). Pure typed Python over the
  scenario layer and engine; **no Qt imports allowed** (guard test, same
  pattern as the core/region boundary test). Everything a UI needs to
  render — labels, table rows, chart series, validation messages — is
  produced here as plain dataclasses/strings.
- `glidepath.gui` — the **PySide6 shell**: widgets, layouts, signals.
  Thin by policy: it binds view models to widgets and forwards user
  actions back; it contains no domain logic, no formatting, no copy.

A future web UI would be a second shell over the same `glidepath.app`
layer; nothing in `app` may assume a desktop (no file dialogs, no
blocking prompts — shells own interaction mechanics).

Table-valued assumptions are overridden in place as validated text —
one `key = value` line per figure, dotted keys for nested tables — and
the app layer vets a parsed table through the same policy parsers the
engine reads it with (`glide_path_from_shape`,
`StatePensionUprating`/`FutureYearsPolicy`/`AnnuityRateTable`
`.from_assumption_value`) before it can enter the assumption set.
**Rejected:** a bespoke structured editor per table — four editors'
worth of shell surface for the same outcome; a richer shell can still
add one later over the same override transition.

**Why.** The same boundary that keeps the core region-agnostic keeps the
product UI-agnostic; view models are plain objects, so the ≥90% coverage
bar is met headless (no Qt event loop in tests), and the Qt layer stays
small enough to justify its thinner test coverage. **Rejected:** logic in
widgets (locks the product to Qt, untestable without a display); building
web-first now (v1 is a local, private-by-construction desktop tool, §1;
a server stack contradicts "nothing is transmitted" until designed
properly). **Accepted cost:** some ceremony — every screen needs a view
model even when trivial; Qt-specific glue still needs a few smoke tests
under an offscreen platform (`QT_QPA_PLATFORM=offscreen`).

**App-layer/region coupling (issue #134).** The app layer imports
`glidepath.regions.uk` directly (defaults, assumption metadata, the
returns history, form vocabularies) rather than going through a
region-selection seam. **Decision:** accept that coupling for the
UK-first shell. The isolation promise of §4.2 protects the *core* —
the engine, and everything the maths depends on, stays region-agnostic
and guard-tested — while the app layer is presentation plumbing that a
second region would rework anyway (its copy, forms, and defaults are
region-shaped by nature). What *is* pinned by a guard test alongside
the §4.2 guards: only the app layer may import region code above the
core — the gui shell must keep reaching region data through
`glidepath.app`, so when a second region becomes real, the seam has
exactly one layer to cut into. **Rejected:** a region-selection
Protocol in the app layer now — an abstraction with one implementation
and no second customer to shape it; revisit when a second region is
actually planned.

### 4.8 Wrapper balance roll-forward from the statement date

**Decision.** A wrapper balance is a fact dated `as_of` (its statement
date), but the run starts at `config.today`. The engine seeds each
opening ledger by rolling the stated value forward over the **whole
months** from `as_of` to `today` at the wrapper's **expected nominal
return net of its fee drag** — the deterministic composition of the
real-return assumptions with CPI (`(1 + real)(1 + cpi) - 1`), weighted
by the allocation the wrapper opens the first period with (its own
stated allocation, else the glide path at the run-start
years-to-retirement), then netted against the wrapper's annual fee
rate as `(1 + nominal)(1 - fees) - 1` — fees before growth, exactly
as every modelled period charges them (§5.2 step 6; issue #111) —
compounded by the same integer-exponent-plus-linear-remainder
arithmetic the DB statement-date convention uses
(`revaluation_factor_for_months`, §5.1, §4.6). The fee rate is the
wrapper's own schedule, else the default fee assumptions unless the
region exempts the kind (§5.1; issue #118). Each sub-balance fact
(`balance`, `crystallised_balance`) rolls by its own `as_of`. A
balance dated in the future is an engine error, mirroring the DB
statement-date check; so is a fee-adjusted expected nominal return of
-100% per year or worse — an expectation must keep a positive gross
return (the stochastic model's existing invariant, applied to the
deterministic composition), which keeps the roll-forward factor
strictly positive. The expected (deterministic)
rate applies in every run mode: the pre-`today` span is not
path-modelled, exactly as CPI stays deterministic across Monte Carlo
paths. The stated fact is never altered — the rolled-forward figure is
an engine-derived *estimate layered on the fact*, and every non-zero
adjustment is reported in `RunProvenance.balance_roll_forwards`
(stated value, `as_of`, months, factor, opening value) and rendered by
the inspector, so the deviation from the stated-fact principle (§1) is
visible, attributable, and assumption-driven rather than silent.

The state pension forecast follows the same convention (issue #117):
its weekly rates roll forward from `forecast_as_of` — the main slice
at the `policy.state_pension.uprating` assumption's annual rate, the
protected slice by CPI only, both floored at zero like every statutory
uprating step — with the adjustments reported through the same
provenance record and a future-dated forecast likewise an engine
error.

**Why.** Silently treating a statement value as today's value was
wrong for any stale statement (issue #72); DB entitlements already
roll forward from their statement date, so this extends one documented
convention rather than inventing a second. **Rejected:** modelling the
span period-by-period (the run never models time before `today` —
§5.2 partial-period convention: elapsed months live in the facts, not
the model); treating the balance as current (the bug being fixed);
erroring on any stale balance (statements are routinely weeks old — a
usability failure). **Accepted costs:** contributions in the
gap are *flows*, and only *level revaluations* — the growth rate and
the percentage fee drag — compound over the unmodelled span (the DB
precedent) — a long-stale statement therefore understates by the
missed contributions, and restating a fresh balance is always better
than relying on the roll-forward; the opening allocation stands in
for the whole span; a span under one whole month rolls by nothing
(the §4.1 whole-month convention), which keeps the common
freshly-stated case an exact no-op.

### 4.9 Launch example and form clearing

**Decision.** The facts form opens pre-filled with a shipped example
plan (`app/example.py` — the §4.5 golden persona extended with a
workplace DC, an ISA, a state pension forecast, and a spending need)
and the shell submits it immediately, so the first launch shows a real
projection instead of a blank screen. The status line says it is an
example and not the user's data; one clear button empties the form and
resets the session to no plan. The example is nothing but raw form
text driven through the ordinary `parse_facts_form` path — a test
guarantees it parses and projects, so the opening screen can never
show an error. **Why.** An empty form hides the product — §1's
inspectability means nothing with nothing to inspect — and routing the
example through the normal submission path preserves the facts
principle: the app still never silently invents *user* facts; the
example is labelled, replaceable, and clearable. **Rejected:** seeding
the session with a pre-built `Household` (bypasses the form pipeline
and can drift from what a user could actually type); a separate demo
mode (a second surface to maintain for no extra information).

### 4.10 Release process

**Decision.** Releases are SemVer 0.x tags on `main` plus a GitHub
Release whose notes come verbatim from a curated `CHANGELOG.md` (Keep
a Changelog format), plus an sdist/wheel published to PyPI — no other
built artifacts. The version lives in
`[project] version` in `pyproject.toml`; minor bumps carry features
and behaviour changes (plan-file schema steps ride the §4.5 migration
harness), patch bumps carry fixes only; 1.0.0 is deferred until the
product is stable enough for outside users. Cut flow: on an
up-to-date `dev`, `make bump V=X.Y.Z` sets the version (uv.lock
embeds the project version, so the target performs the one sanctioned
bare `uv lock` — minimal, no `--upgrade`, the `exclude-newer` cutoff
still applying, `check_dep_age.py` re-verifying after); the release
PR moves the Unreleased changelog items into a dated `## [X.Y.Z]`
section (drafted from the merged PR titles since the last release);
after the merge, the merge commit is tagged `vX.Y.Z` and the tag
pushed. `release.yml` then refuses to publish unless the tagged
commit is on `main`, the tag matches the pyproject version
(`scripts/release_notes.py`), and the changelog has a section for it
— a mistyped or misplaced tag fails loudly instead of shipping. After
those gates pass, an unprivileged build job runs `uv build` and
smoke-tests the wheel (clean-venv install; import; the metadata
version must match the tag) so no artifact reaches PyPI untested. The
artifacts then flow to a publish job that checks out nothing and runs
no project code: `pypa/gh-action-pypi-publish` exchanges the job's
OIDC token (repo `williajm/glidepath`, workflow `release.yml`,
environment `pypi` — the trust contract registered on PyPI) for a
short-lived upload token, so no PyPI credential is stored in the
repo, and generates PEP 740 attestations so every published artifact
carries verifiable build provenance. The `pypi` environment requires
manual approval and only `v*` tags may deploy to it (defence in depth
behind the trusted-publishing contract). The GitHub Release is
created last, only after PyPI publication succeeds, with the
published sdist/wheel attached — a failed upload never leaves a
public release advertising a package that is not on PyPI. Before
attaching them, the release job (which likewise checks out nothing
and runs no project code) attests signed build provenance for the
artifacts via `actions/attest-build-provenance` — the GitHub-side
mirror of the PEP 740 attestations, so a file downloaded from the
release page verifies with
`gh attestation verify <file> -R williajm/glidepath` instead of
trusting the download channel. The signed Sigstore bundle itself is
also attached to the release
(`glidepath-X.Y.Z-provenance.intoto.jsonl`): the attestation store
and PyPI already hold the same provenance, but an attached bundle is
visible next to the artifacts, verifiable offline
(`gh attestation verify --bundle`), and recognised by the OpenSSF
Scorecard Signed-Releases check, which inspects only release asset
filenames. No checksum files are published:
digests without provenance add nothing an attacker who could swap
the artifact could not also swap, and SHA-256 digests already ship
in PyPI's own file metadata.
**Runtime pin.** `uv tool install glidepath` resolves dependencies
fresh — `uv.lock` and the `exclude-newer` cooldown do not apply to
end users — so the runtime dependency is pinned exactly
(`pyside6==X.Y.Z`) and end users install the PySide6 the release was
tested against. The pin moves only via `make deps`; the accepted
tradeoff is that users pick up PySide6 fixes only with a new
glidepath release, which suits an application (not a library)
distribution.
**Why PyPI.** Reserving the `glidepath` name (first-come-first-served,
claimed only by an actual upload) and giving technical users a real
install channel — `uv tool install glidepath` /
`pipx install glidepath` — without the signing costs of binary
artifacts. It is an app distribution channel, not a library: nothing
under `glidepath.*` becomes a public API, and the 0.x line still
promises no import-level stability (this supersedes the earlier
rejection of PyPI on those grounds — publication implies no such
promise as long as the README states the product is the CLI/GUI entry
point).
**Why no binary artifacts.** The natural desktop artifact — a PyInstaller
Windows build — would ship unsigned: SmartScreen interposes a
"Windows protected your PC" warning on every new release's binary
(reputation resets per binary) and PyInstaller output is a known
antivirus false-positive trigger, while code signing costs a
recurring OV-certificate fee (Azure Trusted Signing is currently
org-only). At 0.x with a run-from-source audience, that cost buys
little; packaging (and signing) can be added to `release.yml` later
without changing the tag/changelog process. **Rejected:** unsigned
`.exe` zips now (SmartScreen/AV friction documented above); CalVer
(clashes with the existing 0.1.0 and with SemVer-style
schema-migration discipline); long-lived PyPI API tokens in repo
secrets (trusted publishing removes the stored credential entirely).

### 4.11 Couples activation (the 9.4 spike's decision record)

Spike run 2026-08-11 (#45); policy facts live-verified the same day
(§6 "Couples"). The single-person audit found the schema, persistence
(the JSON already carries `persons` as an array — no migration),
scenario overrides (entity-id addressed), Monte Carlo, reporting,
comparison and exports **already two-person generic**; the work
concentrates in the engine's per-person run state, the facts form, and
the decisions recorded here. Couples ship in increments (roadmap
9.29–9.34), each independently mergeable behind the existing gates:
the engine accepted two persons as of 9.30, while `parse_facts_form`
kept validating one person until 9.31 activated the partner entry, so
a partially activated build refused a two-person plan rather than
mis-modelling it.

**Scope.** A household is one or two adults planning together; the
partner is **strictly optional everywhere** — a one-person household
stays the default and behaves exactly as today. The tax mechanics that
require marriage/civil partnership (marriage allowance, death
transfers at spouse exemption, ISA APS) assume the two persons qualify;
the UI says so in copy rather than modelling cohabitation separately
(a cohabiting couple simply leaves the marriage allowance unclaimed
and loses the death-transfer treatment — a labelled limitation, not a
modelling target). Mixed rUK/Scottish residency already works —
`tax_residency` is per-person.

**Pooled decumulation — the central engine decision.** One withdrawal
step per period for the whole household, not two person-scoped steps.
`WithdrawalSource` gains a `person_id`; the single
`tax_free_cash_headroom` becomes per-person (the LSA is an individual
cap); the tax-treatment ordering (taxable-growth → tax-free →
crystallised → uncrystallised, §5.2) is unchanged. Within a
tax-bearing treatment group the engine drains the household need
greedily by marginal cost: it prices the next tranche from each
person's frontier source through the existing incremental-tax
machinery and draws from whichever person's tranche is cheaper — so
both personal allowances and both basic bands fill naturally, with no
optimizer and no new tax model. Tax-free groups keep drawing in
wrapper order. Aggregate-pot strategies (fixed-%, guardrails) read the
household pot — a documented meaning change from "my pot" to "our
pot". **Rejected:** splitting the household need per person up front
(any split ratio is arbitrary and wastes the second personal allowance
— the main financial win of couples modelling); a cross-person
optimizer over the whole horizon (advice-shaped, untestable against a
published rule). Engine shape: extract the per-person mutable state
of `_Projection` (balances, income ladders, AA/LSA/MPAA ledgers, DB/
state-pension/annuity streams) into a `_PersonProjection`; the run
advances shared factors (CPI, returns) once and steps each person,
then pools the withdrawal step.

**Horizon.** The run ends at the *latest* of the persons'
`horizon.planning_age` dates (per-person ages, one household end).
Both persons are alive to the horizon unless a death age says
otherwise (below). Chart categories label both ages (`2032 · 60/58`).

**Survivor modelling — deterministic and optional.** Each person gains
`death_age: Decision[int] | None`, default `None` (alive to horizon).
It is a Decision, so it is scenario-overridable: "what if I die at 75"
becomes an ordinary scenario diff — the one decision addressable even
when the base plan leaves it unset (a `None` base synthesizes the
resolved decision, borrowing `target_retirement_age`'s timestamp so
resolution stays clock-free). Death takes effect at the first period
whose start the death age has been attained by — the §4.1 access-gate
convention, so the period containing the death date still models the
person alive and the survivor rules run from the next period boundary.
From that period, verified rules (§6 "Couples"):

- **DC pensions** pass to the survivor as beneficiary drawdown: the
  pot merges into a survivor-held crystallised sub-balance flagged
  income-tax-free when death precedes age 75, taxed at the survivor's
  marginal rate otherwise. Inherited funds carry no NMPA gate, earn no
  new tax-free cash, and consume no survivor LSA. Death lump sums are
  never modelled (beneficiary drawdown only), so no LSDBA test arises
  — a recorded simplification.
- **ISAs/LISAs** merge into the survivor's ISA via the additional
  permitted subscription (value passes with tax-free status; the APS
  is additional to the survivor's own allowance, so no allowance
  interaction needs modelling).
- **GIA/cash** merge into the survivor's equivalents (spouse
  exemption; CGT is out of scope anyway).
- **DB pensions** continue at the scheme's survivor fraction — a new
  per-scheme `survivor_fraction` fact defaulting to the
  `db.survivor_fraction` assumption (50%, §6 basis) when unstated.
- **State pension**: the deceased's stream stops. Nothing is
  inherited in v-couples — the new state pension passes on only 50%
  of a protected payment under narrow pre-2016-marriage conditions
  (§6), and protected payments are not modelled.
- **Annuities**: single-life streams stop; joint-life streams
  continue at the purchase's survivor fraction (9.34).
- **Marriage allowance** lapses from the tax year after death.
- **Spending**: the household spending plan scales by a
  `spending.survivor_multiplier` assumption (default 0.70; pinned
  2026-08-12 against the PLSA single-vs-couple budget ratios —
  0.62/0.72/0.72 across the three living standards, §7 — resolving
  §9 open question 9).

Shipped conventions (9.33): inherited holdings stay reported under
their original wrapper ids but join the *survivor's* ledgers, results,
and tax picture — every draw prices through the survivor's own
assessment, inherited pension pots are fully crystallised on transfer
(no new tax-free cash) with their beneficiary-drawdown taxation read
from the region's data-driven age-75 boundary, and no inherited source
is ever gated or triggers the survivor's MPAA. Death ends the
deceased's employment income, contributions (an inherited wrapper's
schedule dies with them), DB accrual, and pending annuity purchases;
the DB commutation lump sum dies too (a survivor pension pays income
only). Planned outflows are household decisions and keep funding
whoever's age dates them. With no survivor — a one-person death, or
both gates fired — nothing further is spent, drawn, or taxed: the
estate stays invested to the horizon, out of scope. The 9.32 marriage
allowance lapses from the death-effect period, which is exactly the
tax year after the one containing the death date.

**Rejected:** stochastic mortality in Monte Carlo (death ages are
deterministic across paths, exactly as CPI is — §5.2; longevity *risk*
stays expressed through the planning-age horizon); modelling IHT on
death transfers (the FA 2026 pensions-into-IHT change taxes deaths
from 6 April 2027, but the spouse exemption is explicitly maintained
(§6), so transfers between partners — the only death this model has —
stay IHT-free; non-spouse bequests remain out of scope).

**Marriage allowance.** A household-level `Decision[bool]` ("claim
marriage allowance when eligible", default claimed): each tax year the
engine checks eligibility — transferor's taxable income below their
personal allowance; recipient liable at no more than basic rate (rUK)
or intermediate rate (Scotland) — picks the direction automatically,
and applies it as ITA 2007 s55B specifies: a **tax reducer** of the
basic-rate percentage of the transferable amount (£1,260 → up to
£252/yr), capped at the recipient's liability, with the transferor
re-assessed under a personal allowance reduced by the transferable
amount (s55B(6)) so a donor whose income sits inside the transferable
band bears their real cost (GOV.UK's example: £252 off the recipient,
£38 due from a donor on £11,500 — £214 net).
`TaxSystem.assess` stays strictly per-person; the region
gains a small household adjustment step that runs after both
assessments — the one deliberate crack in the person-isolated tax
contract, kept at the region layer. Figures ship as data
(`marriage_allowance` keys in the tax-year files), never hardcoded.

**Transfers between living partners — deferred.** No modelled
inter-spousal asset moves or third-party pension/ISA contributions in
the activation increments: the user can restate facts under either
person (CGT out of scope makes GIA moves cost-free anyway), and
contribution routing to the lower-taxed partner is advice-shaped
optimization. The verified third-party-contribution rules (§6) are
recorded for any future increment.

**Facts form.** The form gains an optional second person: form data
carries `persons: tuple` (1–2), every repeatable row (wrappers, DB
pensions, annuities) gains an owner key, and the copy reads "About
you" / "About your partner". Adding a partner is one explicit action;
removing one deletes their rows after confirmation. With no partner
the form renders and parses exactly as today. `form_cannot_represent`
drops its extra-person and joint-life refusals as the increments land
and keeps refusing only what remains unrepresentable.

**Solver and cards framing.** The retirement solver (9.14) varies one
selected person's retirement age with the partner's decision held
fixed, and measures the replacement-rate target against household
employment income; the outlook/drawdown cards speak at household
level. Per-person chart series stay optional future work — household
aggregation remains the default presentation.

### 4.12 Facts-form usability and the retirement-income choice (Phase 10)

**Decision: progressive disclosure, not a "simple mode".** A
simple/advanced mode toggle is a second surface to maintain — the §4.9
objection to a demo mode applies unchanged — so the form simplifies by
disclosure instead: each section's rarely needed fields
(`FieldSpec.advanced`) render behind a per-section "More options"
toggle, extending the pattern the optional partner established (9.31).
Two invariants keep disclosure honest: a field holding a value is
always revealed (hidden data would still submit), and a field carrying
a submission error is always revealed (a hidden error reads as the app
ignoring the save). No field may be both required and advanced — a
guard test enforces it.

**Decision: required markers and inline errors.** Required fields
carry a `*` on their labels (`FieldSpec.required`, previously parsed
but never rendered); submission errors render inline under their
fields from the structured `FormError` list
(`FactsSubmissionOutcome`), with the first error scrolled into view
and focused. The status line keeps the full formatted list — the two
presentations share one error source.

**Decision: the income preference is a disclosure control; the
purchases are the preference.** The Retirement income section's
drawdown-vs-annuity dropdown decides only whether the annuity purchase
sections are offered; the plan's stored annuity preference *is* its
purchase records (wholly decisions, §5.1), so the dropdown re-derives
from whether purchases exist on load and stores nothing itself.
Switching back to drawdown-only with purchases on the form confirms
and deletes them — the remove-partner rule. The purchase sections
render directly beneath the preference that reveals them — disclosure
next to its control, not at the bottom of the form. Rejected:
persisting the preference as its own decision (redundant with the
purchases, and a second source of truth to keep consistent).

**Decision: percentages at the form boundary, fractions in the
domain.** Users think in percentages, so every pot-share entry on the
form is a percent — the equity allocation, the fixed-percentage
withdrawal rate, and the annuity purchase's share of pot
(`percent_of_pot`, over 0 up to 100; 100 annuitises the whole pot) —
converted at parse time to the domain's `Decimal` fractions, which
persistence, scenarios, and the engine keep unchanged. The scenario
editor and inspector edit/show the raw decision values (fractions), as
they do for every decision.

**Decision: the withdrawal strategy is a household-level decision.**
`Household.withdrawal_strategy: Decision[WithdrawalRule] | None`
(schema v9; `None` = fixed real spending, the engine default) closes
the §2 scope gap — the four shipped strategies existed in core but
were never surfaced. `WithdrawalRule` is a closed value record (kind +
the fixed-percentage rate) rather than a strategy instance, so it
persists, compares, and displays; run layers construct the strategy
via `WithdrawalRule.strategy()` (`plan_run_config`), and the choice
rides the base run, scenario runs, Monte Carlo, and the backtest.
Recorded limitations: like the marriage-allowance claim (9.32) the
household has no `EntityId`, so the choice is not
scenario-addressable; and the "When can I retire?" / "How much can I
draw down?" cards deliberately stay on fixed real spending — each
answers a fixed-real-income question by construction (9.14, 9.25),
and gross-defined strategies (fixed %, natural yield) make their
success criterion ill-defined.

**Decision: the outlook card reads the deterministic path before
Monte Carlo.** With a base projection held but no (or a stale) Monte
Carlo run, the card summarises the single deterministic path — same
reading, deflator, annuity quote, and State Pension machinery as the
percentile card — with a basis line inviting the Monte Carlo run that
adds the likely range. The card is populated from first launch
instead of opening with an empty-state instruction.

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
`commuted_fraction`, state pension deferral, the household's
`withdrawal_strategy` — a `Decision[WithdrawalRule]`, §4.12) or forming
whole decision records (`AnnuityPurchase`). They are exactly the
scenario what-if whitelist (§4.3; the household-level decisions are the
recorded exception — no `EntityId` to address) and surface in the UI as
"your choices" — a third column beside stated facts and assumptions.

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
Phase 6/8 inspector rather than as individual provenance rows (the
inspector's "Plan structure" section).
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
    death_age: Decision[int] | None  # None: alive to horizon (4.11, 9.33)
    wrappers: tuple[Wrapper, ...]
    db_pensions: tuple[DBPension, ...]
    annuity_purchases: tuple[AnnuityPurchase, ...]  # decision records (5.5)
    state_pension: StatePensionRecord | None  # None: not modelled
    glide_path: GlidePathConfig | None  # None: default-shape assumption applies


# Wrapper kinds are OPAQUE region-defined ids ("uk.workplace_dc",
# "uk.sipp", "uk.isa", "uk.lisa", "uk.gia", "uk.cash") — core never
# enumerates them; the region's WrapperRuleset maps id -> rules, so
# no UK account type leaks into the core model (4.2).
WrapperKindId = NewType("WrapperKindId", str)
TaxResidencyId = NewType("TaxResidencyId", str)  # same pattern


@dataclass(frozen=True)
class Wrapper:
    id: EntityId  # stable; override targets (4.3) need it
    kind: WrapperKindId
    label: str | None  # the user's own name ("Aviva SIPP"); display copy
    # only, preferred by every naming surface; None derives from kind (9.28)
    balance: Fact[Money]  # pension kinds: uncrystallised value
    crystallised_balance: Fact[Money] | None  # pension kinds: already in drawdown
    allocation: AssetAllocation | None  # None: the glide path supplies it
    fees: FeeSchedule | None  # platform + fund, annual %; None: fee
    # assumptions, unless the region exempts the kind (uk.cash) — a
    # bare savings account prices no platform/fund charges (#118)
    contributions: ContributionSchedule | None


@dataclass(frozen=True)
class ContributionSchedule:
    employee_amount: Decision[Money]  # per year; % of salary variant too
    employer_amount: Fact[Money] | None  # employment terms incl. match rules
    relief_mechanic: ReliefMechanic  # RELIEF_AT_SOURCE | NET_PAY (region)
    escalation: AssumptionRef | None  # e.g. grows with earnings assumption


@dataclass(frozen=True)
class DBActiveMembership:  # CARE-style active accrual (roadmap 9.6)
    accrual_rate: Fact[Decimal]  # fraction of pensionable salary per year
    pensionable_salary: Fact[Money]  # annual; often below total pay
    active_until_age: Decision[int] | None  # leave/defer; None = until taken


@dataclass(frozen=True)
class DBPension:
    id: EntityId
    accrued_annual_pension: Fact[Money]  # at date of leaving / statement
    statement_date: date
    normal_pension_age: Fact[int]  # scheme fact
    revaluation_basis: RevaluationBasis  # scheme fact (e.g. CPI capped 5%)
    early_late_factors: FactorTable  # scheme facts, user-entered
    commutation_factor: Fact[Decimal] | None  # £ lump sum per £1 pension
    taken_at_age: Decision[int] | None
    commuted_fraction: Decision[Decimal]
    survivor_fraction: Fact[Decimal] | None  # None = db.survivor_fraction (4.11)
    active_membership: DBActiveMembership | None  # None = deferred (9.6)


@dataclass(frozen=True)
class StatePensionRecord:
    forecast_weekly_amount: Fact[Money] | None  # the official forecast IS the fact
    #   (None only so pre-#97 plans load; a region refuses to project without one)
    protected_payment: Fact[Money] | None  # pre-2016 transition; CPI-only
    deferral_years: Decision[Decimal]
    # SPA derives from DOB via region AgeRules; uprating is an assumption key.


@dataclass(frozen=True)
class SpendingPlan:
    annual_spending_real: Fact[Money]  # today's money
    stage_multipliers: Mapping[LifeStage, Decimal] | None
    #   retirement keys only: go-go/slow-go/no-go sub-stages, with
    #   DECUMULATION the whole-retirement fallback (issue #114);
    #   spending is modelled only in retirement, so accumulation keys
    #   are rejected rather than silently ignored


@dataclass(frozen=True)
class PlannedOutflow:  # mortgage payoff, gift, purchase — a decision
    id: EntityId
    label: str
    amount_real: Decision[Money]
    at_age_of: tuple[EntityId, int]  # person + age it occurs


@dataclass(frozen=True)
class AnnuityPurchase:  # wholly a decision record (5.1)
    id: EntityId
    at_age: Decision[int]
    fraction_of_pot: Decision[Decimal]  # partial annuitisation supported
    annuity_type: AnnuityType  # LEVEL | ESCALATING | INFLATION_LINKED
    basis: AnnuityBasis  # SINGLE | JOINT
    survivor_fraction: Decision[Decimal] | None  # JOINT only: 50/66/100% (§6)
    # rate comes from the annuity-rate assumption table by age/type
```

DB scheme parameters (revaluation basis, NPA, early/late factors,
commutation factor) are user-entered **facts** — schemes vary too much to
ship as data. v1 modelling conventions (roadmap 4.2): the scheme's one
`RevaluationBasis` (CPI optionally capped, fixed, or none; CPI-linked
revaluation floored at zero) governs both revaluation in deferment and
increases in payment — splitting the two bases stays a deferred
extension (§2).
Within the run, revaluation advances with each period's CPI under the
§5.2 linear whole-month convention; the span from the statement date to
`today` — which the run never models period-by-period — compounds the
assumed CPI over whole months (integer-exponent whole years plus a
linear remainder, exact `Decimal` per §4.6). Commutation trades pension
for `pension given up x commutation factor` of tax-free cash in the
period benefits start — tax-free up to the remaining lump-sum
allowance headroom, the excess taxed as income (§5.2 tax-free cash
conventions); a start date before `today` means the pension is
already in payment and the lump sum already lives in the stated
balances. In decumulation, net-of-tax DB/state-pension income and any
commutation lump sum meet the net spending need before wrappers are
drawn; income beyond the need banks into the person's first uncapped
taxable wrapper (GIA/cash, roadmap 9.2) and is spent only when they
hold none. Before decumulation the same offset meets the period's
planned outflows: retirement income already in payment ahead of the
target retirement age (an early DB start, a purchased annuity, the
state pension alongside work) — net of the marginal tax it adds on
top of employment income — funds them first, and the remainder banks
per the 9.2 sweep. Employment income itself never offsets or banks:
net pay funds working-life spending, which the model does not track.

**Active DB membership (roadmap 9.6).** `active_membership` on a
`DBPension` turns the entitlement from a frozen deferred amount into a
projected one: CARE-style accrual credits
`accrual_rate x pensionable_salary` per year of service on top of the
revalued accrued entitlement, each credit revaluing on the scheme's
single basis from the period it is earned. Conventions: the span from
the statement date to `today` credits whole months of service at the
stated salary, un-revalued (the §4.1/§4.6 linear whole-month
convention — the revaluation the mid-span credits would earn is below
annual resolution); within the run, the salary escalates with the
earnings-growth assumption exactly like employment income, and each
period's credit joins the entitlement at the period open. Service ends
at the earliest of `active_until_age` (the leave-and-defer decision;
`None` means service continues to the benefits start), the benefits
start itself, and retirement — accrual is gated by the same §5.2
period-open convention that stops employment income and contributions
at the target retirement age, and the pre-run span is clamped at the
exact target-retirement date (whole months — the span is never
modelled period-by-period, so the period-open gate cannot apply; the
date clamp is the conservative counterpart). The final active period
pro-rates its credit by whole months of service; an `active_until_age`
after the taken-at age is a construction error. Final-salary linkage is not
modelled (§2): a final-salary scheme is approximated by CARE accrual
at the stated salary. Member DB contributions are likewise not
modelled — a known v1 limitation: schemes that deduct member
contributions from pay under net pay reduce taxable income in reality
but not in the model, so tax is overstated for active members who
enter their full pay as employment income. For the annual allowance,
an active arrangement's **pension input amount** is
`closing value - opening value` per tax year, each value
`annual pension x pension.db_valuation_factor` (16, shipped as data —
FA 2004 s234) with the opening value uprated by CPI (s235; the run's
CPI path stands in for the September-CPI appropriate percentage) and
the result floored at nil (PTM053301) — a deferred arrangement whose
revaluation never outruns CPI generates nil by construction, matching
the deferred-member carve-out. The region function ships with 9.6 and
the engine reads it each period through the annual-allowance
measurement (#116, §5.2 step 5): every DB stream not yet in payment
at the period open supplies its pre-credit opening entitlement and
its credited-and-revalued closing entitlement. In the commencement
year the closing entitlement revalues only to the benefits start —
HMRC's closing-value adjustment (PTM054500, #188), so the final
year's accrual is measured; a stream already in payment at the
period open generates no further input amounts.

State pension: the official DWP forecast **is the fact and the only
route to an amount** (#97). It is authoritative, free, and instant to
obtain from gov.uk/check-state-pension, so the model never re-computes
what DWP has already computed: there is no qualifying-years derivation,
and a record without a forecast is refused with a clear demand for one
(the facts form requires the forecast whenever the section is filled
in; pre-#97 plans that stored qualifying years migrate by dropping
those fields and fail projection with the same demand). Any protected
payment is recorded separately because it uprates by CPI only, not the
full uprating policy. Conventions (roadmap 4.3): the forecast weekly
amount is the DWP total, of which `protected_payment` is the CPI-only
slice; amounts are annualised at 52 weeks and uprated by the engine
from the run start. The forecast is statement-dated (`forecast_as_of`),
so a stale forecast first rolls forward from its `as_of` to `today`
exactly like a stale balance fact (§4.8, issue #117): the main slice
at the uprating assumption's annual rate, the protected slice by CPI
only, over whole months with every adjustment reported in the run's
provenance; a future-dated forecast is an engine error. Because
upratings take effect whole each 6 April —
exactly a UK period boundary — the state pension stream steps by a
**full annual uprating at every period boundary**, never scaled by a
partial period's active fraction (a deliberate deviation from the §5.2
linear convention, which models continuously growing price and
earnings levels); uprating is never negative — a deflationary CPI
freezes the rate, matching statute. Deferral shifts the start past SPA
in whole months and earns one ninth of 1% per whole week deferred,
payable only from nine weeks (~5.8%/52 weeks; shipped as data); the
uplift fraction applies to the rate payable **at claim** — upratings
earned during deferment included — and the resulting increment uprates
by CPI only from then on (§6).

Annuity purchases are decision records on the person (roadmap 5.5).
Conventions: a purchase fires in the period containing the date the
person attains `at_age` — inside the run window only, and an age
already attained at `today` is an engine error, since a past purchase
cannot be priced from a modelled pot (an annuity already in payment
belongs in stated income, which v1 does not yet model). The chosen
fraction applies to every pension wrapper's sub-balances as they stand
at the period's open (before that period's contributions):
crystallised funds annuitise whole; uncrystallised funds crystallise
on the way, delivering the region's tax-free fraction as cash — capped
at the remaining lump-sum-allowance headroom, the excess simply buying
more annuity — which joins the period's income offset exactly like a
DB commutation lump sum. Crystallising a pot whose access gate has not
opened is an engine error (§4.1). Buying a lifetime annuity never
marks flexible access (no MPAA trigger — UK rule and model
convention). Pricing is assumption-driven (§7): the single-life-at-65
base rate for the type, times the `annuity.age_adjustment` table's
per-age multiplier (whole-year knots, linear interpolation between,
no extrapolation outside), times its joint-life factor on a joint
basis — the one factor whatever survivor fraction the purchase
carries, a labelled v1 limitation of the §7 table (real quotes price
the 50/66/100% options differently). On the buyer's death a
joint-life stream continues to the surviving partner at the purchased
fraction, escalating as before (§4.11, roadmap 9.34); a single-life
stream dies with the buyer. A joint-life purchase executed with no
living modelled partner — none in the plan, or the partner already
dead at the purchase date — still prices at the joint factor and buys
a survivor income the model can never pay: a recorded convention (the
purchase is the user's stated decision, and the engine executes it as
priced). The facts form refuses entering a joint-life purchase with
no partner on the form, so the convention is reachable only through a
death predating the purchase or a plan built outside the form.
Income is wholly taxable, starts at the exact purchase date
(pro-rated in its first period, §4.1), and escalates per product:
level holds nominal; escalating compounds the table's fixed rate;
inflation-linked tracks the run's CPI path exactly — annuity
contracts, unlike statutory upratings, are not floored at zero.
Escalation accrues from the exact start date, not the period
boundary: the purchase period's boundary advance scales by the
entitlement's share of that period (§4.1 linear whole-month
convention), so a mid-period purchase never collects a full period
of escalation. When
an `UP_FRONT_LUMP_SUM` crystallisation event lands in the same
period, the purchase — a step-2 income event — resolves first, so its
uncrystallised slice still carries tax-free cash (§5.2).

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

Wrapper balances are facts dated by their statement (`as_of`); the
engine rolls each one forward to `today` at the wrapper's expected
nominal return net of its fee drag over whole months, reporting every
non-zero adjustment in the run's provenance (decision record §4.8).
The state pension forecast's weekly rates follow the same convention
(above), so every statement-dated amount enters the run at today's
level.

Pre-existing pension access is likewise a set of facts:
`crystallised_balance` (funds already designated to drawdown), `lsa_used`,
and `mpaa_triggered_on`. They make an already-in-drawdown user modellable
— no fresh tax-free cash on crystallised funds, MPAA from day one, LSA
headroom reduced — and they carry the NMPA 2028 transition correctly:
benefits already in payment continue below 57, while new crystallisations
and UFPLS are gated by the NMPA schedule (§4.1). In-run tracking
(roadmap 5.2): the run seeds a per-person tax-free-cash ledger from
`lsa_used` and its flexible-access state from `mpaa_triggered_on`; each
period's snapshot reports the cumulative tax-free cash used and the
MPAA trigger date in effect at period end, so the UI and the AA
machinery (roadmap 9.5, wired by #116) read them straight off the
result.

**Life stages and glide path.** A person is not a snapshot: the projection
moves them through `EARLY_ACCUMULATION → MID_ACCUMULATION → PRE_RETIREMENT
(de-risking) → GO_GO → SLOW_GO → NO_GO`. Stage is *derived* each period
from years-to-target-retirement, not stored. The glide path maps
years-to-retirement → asset allocation by interpolating a factor table;
the default shape is an assumption (`glidepath.default_shape`),
overridable per person. Stage boundaries (3.5): retirement — the target
retirement age attained by the period's first day (years-to-retirement
≤ 0, the §4.1 gate convention) — splits into the go-go/slow-go/no-go
sub-stages one and two decades in (issue #114): for typical retirement
ages that lands on the 75/85 boundaries the retirement-smile literature
uses, and like the accumulation split only the spending multipliers
bind to the result, so a simple decade rule suffices (`DECUMULATION`
remains the sub-stages' umbrella: the whole-retirement spending
multiplier key, never derived itself); `PRE_RETIREMENT` inside the
table's de-risking window — the years at which the allocation starts
changing (the lowest knot of the top constant-allocation plateau); the
`EARLY`/`MID` accumulation split falls at twice that window — the
split is presentational (only the allocation is mechanical), so a
simple doubling rule suffices. A constant-allocation table has a zero
window and never de-risks, so `PRE_RETIREMENT` is unreachable there.

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
3. **Contributions** — employee + employer, relief mechanics (region
   ruleset); the AA/taper/MPAA measurement of what landed follows in
   step 5, once the period's full income picture exists.
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
   The year's pension inputs are then measured against the region's
   allowances and any chargeable excess is priced and appended to the
   assessment (the annual-allowance conventions below).
6. **Fees** — platform + fund on average balances (the mean of the
   opening and post-flow balances; the fee never exceeds what the
   account holds).
7. **Growth** — apply the period's returns to each wrapper's allocation.
8. **Close period** — quantize ledger, emit `PeriodSnapshot`.

Income/contributions before tax (tax needs the full picture); fees before
growth approximates intra-year accrual acceptably at annual resolution.
`PeriodSnapshot` records per person/wrapper: opening/closing balances,
flows by category, tax with breakdown, ages, stage, allocation.

**Annual-allowance wiring (decided — #116, roadmap 3.3/9.5).** Each
period the engine builds a region-agnostic measurement of the year's
pension inputs — member gross (provider relief included) plus employer
contributions into pension wrappers, and each not-yet-in-payment DB
stream's opening/closing entitlement for the region to value (§6) —
alongside the income measures the taper needs: total taxable income
*before* member pension deductions (net-pay amounts added back,
portfolio income included), the net-pay and relief-at-source member
amounts, and the period's CPI. The region ruleset applies its taper,
MPAA and carry-forward machinery and returns the chargeable excess
plus the rolled carry-forward pool. Conventions: the measurement takes
the MPAA trigger *standing when the contributions were made*, so
inputs paid at step 3 before a step-4 in-period trigger are measured
pre-trigger (HS345's pre/post-trigger split at the model's own event
order), while a pre-plan trigger fact governs every period; the
carry-forward pool starts empty at the run start — pre-run years'
unused allowance is unknown, so none is assumed (§4.1 conservative) —
and rolls forward with each period's outcome. A DB stream whose
benefits commence *within* the period still generates an input amount
per HMRC's closing-value adjustment (PTM054500, worked example
PTM053710, #188): the closing entitlement is the credited value
revalued only to the commencement date (§4.1 whole-month share), so
the final year's accrual is measured rather than discarded; only a
stream already in payment at the period's first day generates
nothing, its accrual having been measured in the crystallisation
year. Recorded simplification: the add-back values the uncommuted,
unadjusted entitlement at commencement — an early-retirement
actuarial reduction, which PTM054500 would reflect via the actual
amounts crystallised, is not netted off the closing value. The
excess is priced by the region's tax system as
top-slice lines at the taxpayer's own schedule rates with the
relief-extended limits (FA 2004 s227B, s192(4)) — a charge, not
income, so it never feeds back through `assess` (the personal
allowance and its taper cannot move) and never disturbs the
offset/gross-up/wrapper-charge decomposition; the lines are appended
to the period's final assessment, so the snapshot's `tax_due` is the
period's whole liability. Unlike the rest of an accumulation period's
assessed tax (employment tax settles outside the model), the charge
is funded from modelled balances — the funding decision below (#124).
Whole-year convention: a partial period's pro-rated inputs meet the
full year's allowances, and a DB opening value takes the full year's
CPI uplift.

**Annual-allowance charge funding (decided — #124, roadmap 9.21).**
The priced charge is deducted from modelled balances rather than left
reported-only, so a sustained AA breach degrades the balance path the
roadmap-7.3 success metrics read. The region ruleset splits the charge
between routes; the UK follows Scheme Pays (FA 2004 s237A–E,
PTM056410): when the year's total charge exceeds the scheme-pays
minimum (£2,000, shipped as data) and some pension wrapper's own
money-purchase input exceeds the **standard** annual allowance — the
s228 amount, taper and MPAA ignored, exactly the mandatory-scheme-pays
test — the whole charge is debited from that wrapper (the largest
qualifying input when several qualify), uncrystallised funds first.
The debit is a scheme-administrator payment, not a member withdrawal:
no tax lines, no MPAA trigger, no lump-sum-allowance use, and no
`withdrawal_*` flow. Otherwise — including a charge generated by a DB
stream's input (no modelled pot to debit; an actuarial benefit debit
is out of scope) — the charge is paid in cash: deducted from the bare
taxable wrappers (the GIA/cash kind of the 9.2 banking sweep) in plan
order, each capped at its balance at allocation. Either way the
deduction settles at period close **after fees and growth**, exactly
the portfolio-income tax convention: the charge is assessed on the
year's inputs and paid after the year end (self-assessment and
scheme-pays deadlines both run past it), so it never reduces the
period's fee or growth base; at close it is capped at what the
wrapper then holds, and any unfunded remainder — a drained wrapper,
or no taxable account to pay from — joins the person's shortfall so
the ruin signal sees it (§4.1 conservative). Stated simplifications:
the whole charge takes the scheme-pays route when the mandatory
conditions hold, although mandatory scheme pays strictly covers only
the standard-AA-referenced slice (voluntary scheme pays covers the
rest in practice, and the £2,000 test reads the modelled charge); and
the pot debit stands in for the scheme's actuarial benefit
adjustment.

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
end. **Fees and expected growth scale the annual rate linearly by the
same fraction** rather than compounding by a fractional exponent: linear
scaling is exact `Decimal` arithmetic (multiplication and division only,
fully reproducible per §4.6), matches §4.1's linear whole-month
convention, and its error against fractional-exponent compounding is
second-order small and confined to at most two periods per run.
Fractional-exponent compounding was rejected because `Decimal` powers
with non-integer exponents are only "almost always correctly rounded"
(Python `decimal` docs), which is not the byte-identical reproducibility
§4.6 demands. **A stochastic return's deviation from the model's
expectation scales by the square root of the fraction** (#115): the
period return splits at the expected rate — the same Fisher
composition the deterministic model returns and the mean the lognormal
draws are matched to — with the expected component scaled linearly
(keeping the mean on the deterministic path) and the shock scaled by
`sqrt(fraction)`, so a partial period's return standard deviation is
σ·√f rather than the understated σ·f. `Decimal.sqrt` *is* correctly
rounded (unlike non-integer powers), so §4.6 reproducibility holds; in
a deterministic run the deviation is exactly zero and the linear
scaling is bit-for-bit unchanged. The cumulative CPI and nominal escalation factors advance
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
also encode wrapper ordering (tax-aware and **fixed** — no alternative
ordering is exposed anywhere (#192), a deliberate simplification
described to the user in the help guide; a cross-horizon optimiser
stays rejected as advice-shaped, §4.11. The order is
GIA/cash → ISA → pension — taxable-growth accounts first, since every
pound left in them keeps accruing income tax, then wholly tax-free
sub-balances, then crystallised and finally uncrystallised pension
funds); the tax-free cash strategy
(PCLS up front vs UFPLS-style phased vs phased flexi-access drawdown)
is a separate, orthogonal decision on `RunConfig` — any combination of
the two is valid (see below). Conventions (roadmap 5.1): the
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
survives strategies that ignore the need — and an over-draw banks into
the first uncapped taxable wrapper, spent only when the person holds
none (9.2).

**Guardrails and natural yield (decided — roadmap 5.3).** Two post-v1
strategies behind the same protocol. *Guardrails*
(Guyton–Klinger-style) is net-defined: the engine's need — the
inflated spending decision net of pension income — is the baseline,
and the strategy annualises the withdrawal rate it implies (need over
the period's active fraction, over the accessible pot) against two
configured guardrails, cutting the target by a configured fraction
above the upper rail (capital preservation) and raising it below the
lower rail (prosperity); defaults are the conventional 6%/4% rails
with 10% adjustments. The protocol is pure — same state and need,
same plan — so each period is judged afresh and adjustments do not
compound across periods; a cut's unspent remainder is reported as
shortfall, exactly like a gross-defined under-draw, and a rise is
genuinely spent — the engine treats the adjusted target as the
period's net need, so the 9.2 sweep banks only delivery beyond it,
never the rise itself. *Natural yield*
is gross-defined: each accessible source is drawn by exactly the
income its balance throws off — the wrapper allocation weighted over
the `yield.*` assumptions (§7), scaled by the period's active
fraction. The engine prices those yields only for a strategy that
declares it spends portfolio income (`uses_natural_yield`, an
optional class-level marker rather than a protocol member — an
absent marker means false, so strategies implementing only
`withdraw` keep working), so the
yield keys enter provenance exactly when they enter the result; a
yield drawn from an uncrystallised pension pot resolves through the
normal payment machinery (an income draw is a withdrawal, taxed by
the wrapper's rules).

**Tax-free cash strategy (decided — roadmap 5.2).** How pension
tax-free cash is taken is its own decision record on `RunConfig`,
orthogonal to the withdrawal strategy. Three modes, named generically
in the core (the region's tax treatment supplies the fraction and the
cap; UK equivalents in brackets):

- `SPLIT_EACH_PAYMENT` (UK: UFPLS) — the default and the pre-5.2
  behaviour: every draw from an uncrystallised pension pot carries the
  region's tax-free fraction, the remainder arriving as taxable
  income.
- `LUMP_SUM_AS_NEEDED` (UK: phased flexi-access drawdown) — a draw on
  an uncrystallised pot delivers tax-free cash first: the pot
  crystallises `1/fraction` times the cash delivered, designating the
  remainder to the wrapper's crystallised (drawdown) sub-balance,
  which stays invested; taxable drawdown income is drawn only once
  tax-free cash cannot meet the remaining need (headroom or pot
  exhausted).
- `UP_FRONT_LUMP_SUM` (UK: full PCLS at designation) — in the first
  decumulation period whose access gate is open, each uncrystallised
  pension pot crystallises whole: the capped tax-free lump sum joins
  that period's income offset (the DB commutation convention) and the
  rest moves to the crystallised sub-balance, drawn thereafter as
  taxable income.

Conventions: the engine tracks cumulative tax-free cash per person
across the run, seeded from the `lsa_used` fact; the region supplies
the lifetime cap per period (`WrapperRuleset.lump_sum_allowance`;
`None` means no cap), and a payment's tax-free element is capped at
the remaining headroom with the excess arriving as taxable income —
crystallised funds never yield fresh tax-free cash (§5.1). An in-run
DB commencement (commutation) lump sum consumes the same headroom —
landing in the income step, ahead of the period's wrapper draws — and
its excess over headroom is taxed as income (the UK's pension
commencement excess lump sum); it never marks flexible access. The
strategy-facing state reports the remaining headroom as the
withdrawal step opens (`WithdrawalState.tax_free_cash_headroom`),
while a source's `tax_free_fraction` stays the region's nominal
fraction — the cap is an absolute amount, not a share. A phased
draw's income leg is limited to the residue it designated within that
draw: pre-existing drawdown funds answer only to their own
crystallised source id, so a plan's source targeting is honoured
exactly. The first draw with a taxable element from a
partially-tax-free (pension) wrapper — either sub-balance — is
flexible access and records the MPAA trigger date as the later of the
period's first day and `today`; tax-free-only draws (PCLS-only
crystallisations, the up-front lump sum) never trigger, and a
pre-existing `mpaa_triggered_on` fact wins. Gross-defined plans
resolve every mode as `SPLIT_EACH_PAYMENT`: an exact gross amount is
a payment instruction, not a designation.
Outside decumulation (planned-outflow funding), `UP_FRONT_LUMP_SUM`
also resolves draws as split payments — the up-front designation is a
retirement event. An up-front lump sum beyond the period's need banks
with the rest of the period's surplus income (the 9.2 sweep below);
only a person holding no uncapped taxable wrapper still spends it —
the pre-9.2 accepted cost, now confined to that case.

**Taxable wrappers and surplus banking (roadmap 9.2).** The LISA, GIA
and cash kinds activate the deferred wrapper mechanics:

- *LISA*: TEE like an ISA, plus the data file's 25% government bonus
  on member contributions — a contribution bonus, not tax relief (it
  never extends tax bands, never consumes the caps) — inside the
  exact 18-to-50 contribution window (`age_rules.toml`): the region's
  contribution terms carry the window as *dates*, and the engine
  scales scheduled amounts by the whole-month share of the period
  inside the single intersection of run window, period, and
  contribution window — one overlap, never a product of separately
  measured fractions (a run starting after the window closes
  contributes nothing); the £4,000 LISA allowance is a sub-cap
  consumed alongside the overall ISA allowance through shared
  *allowance groups* on the region's contribution terms. Access is a
  §4.1 age gate at 60: pre-60 funds are gate-closed (a draw is an
  engine error, the §5.2 execution rule), so the 25% withdrawal
  charge ships as data but no modelled draw ever bears it — the
  engine never volunteers a charged withdrawal. A crystallised
  balance is a pension concept — only partially-tax-free kinds may
  carry one (engine error otherwise, and the facts form rejects it):
  crystallised sub-balances are never re-gated, so accepting one on
  an age-gated kind would bypass its gate.
- *GIA/cash*: bare accounts — paid from taxed income, withdrawals
  tax-free, growth taxable as it arises. Each period the opening
  balance prices portfolio income through the allocation-weighted
  `yield.*` assumptions (the natural-yield machinery's model): the
  equity slice arrives as dividends, the bond and cash slices as
  interest, feeding the §6 savings/dividend tax layers. The income
  stays invested (the balance path is untouched); the tax
  attributable — the final assessment less one without the portfolio
  layers, PA-taper and band interactions included — is charged to
  the wrapper at period close, pro rata to income across taxable
  wrappers: the real-world drag of paying tax out of taxable
  savings. The decomposition is exact and nothing is charged twice:
  the withdrawal gross-up prices draws on the *no-portfolio* picture
  (a draw that pushes the portfolio layers up a band — a PSA tier
  drop, dividends into a higher rate — leaves that interaction to
  the wrapper charge), so offset + gross-ups + wrapper charges sum
  to the final full assessment. Tax a drained wrapper cannot fund
  (the charge is capped at what the account holds at close) joins
  the period's shortfall — the ledger reconciles and the roadmap-7.3
  ruin signal sees it, never a silent drop. The app enters cash
  accounts with a fixed 100%-cash allocation (a cash account holds
  cash, never the glide path); GIAs default to the glide path like
  any invested wrapper. Capital gains tax is out of scope (§2):
  income only, never disposals.
- *Sweep*: income and gross draws beyond the period's need bank into
  the first wrapper in plan order whose treatment is a bare taxable
  account (GIA or cash) — banking is not a contribution (no caps,
  relief, or bonus). The need a net-defined strategy adjusted (a
  guardrails rise) counts as spending, so only delivery beyond the
  adjusted target is swept. Before decumulation the sweep takes
  non-employment income net of its marginal tax beyond the period's
  planned outflows — employment income never banks. The spending
  need never pays a wrapper's portfolio-income tax: the income
  offset is assessed without the portfolio layers.

**Return model and Monte Carlo.** `ReturnModel.returns_for(period, path)`:
deterministic impl = expected real returns + CPI → nominal, same every
path; stochastic impl (MC phase) = lognormal draws with assumed
volatilities and correlation matrix (Cholesky in `Decimal`; performance
measured before optimising), randomness only from the injected seeded
source. The mode lives in `RunConfig`: `RunMode.MONTE_CARLO` requires a
seed and resolves the stochastic model inside `run()`, with
`RunConfig.path` naming the substream — the path runner (roadmap 7.3,
`run_paths`) projects path *i* under `replace(config, path=i)` and
reduces each path to its success signals (ruin period, per-period
household closing balances for the chart bands, ending balance),
dropping the period ledgers; the result's provenance is the union of
the paths' reads in first-read order, since a balance-dependent read
(natural-yield pricing) can fire on some paths only. A test double
enters through `run()`'s `return_model_factory` instead (built from the
run's tracked assumption view, so provenance stays exhaustive); the
seed requirement binds before the injection — a `MONTE_CARLO` result
must be reproducible from its manifest whatever produced its returns.
Success metrics over paths (`MonteCarloResult`): **probability of
ruin** — the fraction of paths with any period's need unmet, read from
the engine's `shortfall` signal (which survives gross-defined
strategies and covers planned outflows); **sustainable income**
(highest starting withdrawal meeting a target success rate: a
descending scan over the search bracket finds the highest succeeding
scan point, then bisection refines upward within the scan cell above it
— exact to tolerance for strategies whose success is monotone in the
spending level (the default fixed-real), and never below the best scan
point for adjustment-trigger strategies (guardrails), whose success
islands narrower than one scan step need a finer `scan_steps`; every
probe reuses the run's seed, i.e. common random numbers, and the
returned level is always one actually probed — since 9.25 the same
search also accepts a deterministic config, each probe then a single
run judged by the ruin signal, no seed involved); **ending-pot
percentiles** (linear interpolation between order statistics of the
nominal ending balances; CPI is deterministic across paths, so nominal
and real rank identically). Sequence-of-returns risk is demonstrated by
fixtures (roadmap 7.4): same returns, different order → different
outcome — order-independent without withdrawals, materially different
endings with them, and ruin in the bad-returns-first order at a
spending level the good-first order survives.

**Earliest retirement age.** `earliest_retirement_age` (roadmap 9.14)
answers "when can I retire?", mirroring the `sustainable_income`
search-over-runs shape. It probes candidate retirement ages in
ascending order; each probe replaces the retirement-age decision and
the spending plan with the target income — a whole-percent replacement
rate times stated employment income, treated as the net spending need
in today's money — and reuses one config, so probes share common
random numbers and the result is reproducible from the seed (§4.6).
Applying the rate to *gross* pay but enforcing the product as an
*after-tax* need is a recorded convention (#187): 66% of gross is
materially more demanding than 66% of take-home, so the answer errs
later, never earlier — conservative — and the card's copy labels the
target "a year after tax" so the basis is visible.
The age domain is a few dozen whole years, so the ascending scan
returns the exact earliest success even where success is not monotone
in age — cheaper and more robust than the spending search's
scan-plus-bisection over a continuum. Deterministic success is "no
period's need unmet" (the ruin signal above); in Monte Carlo mode the
same solver reads "success rate ≥ target" over the caller's seed and
path count, with the search bounded by a path-projection budget across
its candidate ages (20,000 by default) so an unsuccessful search
cannot multiply the per-run path cap. A candidate with no retired
period inside the projected horizon never tests the income and fails
rather than succeeding vacuously.

**Sustainable income at an age.** `sustainable_income_at_age` (roadmap
9.25) is the same question asked the other way — "how much can I draw
down if I retire at this age?": the retirement-age decision is fixed
at the chosen age and the spending level is searched by delegating to
the 7.3 `sustainable_income` scan-plus-bisection, under the same
exposure gate (an age with no retired period inside the horizon
answers `None` rather than succeeding vacuously — spending is modelled
only in retirement, so every level would pass untested) and the same
reproducibility: one config for every probe, so the answer follows
from the recorded inputs (and seed, under a Monte Carlo basis) alone.

### 5.3 UK region data files

Location: `src/glidepath/regions/uk/data/`, loaded via
`importlib.resources` + stdlib `tomllib`. One file per tax year
(`tax_year_2026_27.toml`), plus effective-dated `age_rules.toml`,
`assumptions_default.toml` (machine mirror of §7; a doc-sync test keeps
them aligned), and `returns_history.toml` (the 9.18 historical return
series: nominal annual rates per calendar year, contiguous, regenerated
from its upstream dataset by `scripts/build_returns_history.py`;
CC BY-NC-SA 4.0-licensed data, unlike the MIT code — see the file
header). Loader rules: money/rates are TOML **strings** parsed to
`Decimal` (bare floats in money positions are load errors); mandatory
`[meta]` with `verified_on` + `sources`; `schema_version`; strict
validation into frozen dataclasses, unknown keys error.

```toml
schema_version = 5

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
aa_carry_forward_years     = 3  # unused AA carries from the previous 3 tax years
member_relief_basic_amount = "3600"  # low/no-earner relief floor, RAS only
member_relief_max_age      = 75  # no relief on contributions from age 75
relief_at_source_rate      = "0.20"
tax_free_lump_sum_fraction = "0.25"
lump_sum_allowance         = "268275"
lump_sum_death_benefit_allowance = "1073100"
db_valuation_factor        = 16  # FA 2004 s234 relevant valuation factor (9.6)

[isa]
annual_allowance = "20000"
lisa_allowance   = "4000"  # counts within the overall ISA allowance
lisa_bonus_rate  = "0.25"
lisa_withdrawal_charge = "0.25"

# Nil rates consume band width (§6); savings above them are taxed at the
# rates below, aligned positionally with the rUK bands (#189). Equal to
# the main rates in 2026/27; the separate 22/42/47 rates (from 6 April
# 2027) land in the 2027/28 file.
[savings]
starting_rate_limit = "5000"
psa_basic      = "1000"
psa_higher     = "500"
psa_additional = "0"
rates = [
  { name = "basic", rate = "0.20" },
  { name = "higher", rate = "0.40" },
  { name = "additional", rate = "0.45" },
]

[dividend]
allowance = "500"  # a nil rate, not a deduction
rates = [  # aligned positionally with the rUK bands (dividends are UK-wide)
  { name = "ordinary", rate = "0.1075" },
  { name = "upper", rate = "0.3575" },
  { name = "additional", rate = "0.3935" },
]

# Marriage allowance (§4.11, roadmap 9.32): the s55B transferable
# amount, and per-schedule gates naming the highest band a recipient
# may be liable at (every band at or below it qualifies).
[marriage_allowance]
transferable_amount         = "1260"  # 10% of the PA, rounded up to the nearest £10
recipient_top_band_ruk      = "basic"
recipient_top_band_scotland = "intermediate"
```

There is no `[state_pension]` table: the state pension amount is the
user's stated DWP forecast, never a shipped rate (#97, §5.1).

`age_rules.toml` holds the durable, effective-dated policy parameters that
are not re-set each tax year: NMPA (55; 57 from 2028-04-06), the SPA
DOB-band table (§6), LISA ages (open 18–39, contribute to 50, access 60),
and the state pension deferral increment (1% per 9 weeks).

**Future years:** past the last shipped file, the region extends the final
year per the `policy.tax.future_years` assumption (scenario-flippable):
`frozen` (indefinitely) vs `frozen_then_cpi_indexed` (the shipped default:
the legislated freeze end, then CPI-indexed). The freeze end governs only
the rUK/reserved figures — the rUK schedule, pension/ISA allowances, and
in both schedules the personal allowance and its taper (reserved to
Westminster). The devolved Scottish band uppers are set annually by the
Scottish Parliament (§6), so `frozen_then_cpi_indexed` carries a mandatory
`scotland` sub-table with separate freeze ends for the lower bands (below
the higher rate; shipped default: held only through the last shipped year,
then CPI-indexed as the uprating proxy) and the Higher/Advanced/Top group
(shipped default: the announced 2028/29 commitment). There is deliberately
no index-immediately mode — a legislated freeze end is a fact (§6), and a
mode without one could synthesize years contradicting known legislation;
a freeze end at or before the last shipped year already degrades to pure
CPI indexation. Legislated future changes (freeze end, pre-announced
rates) ship as data in the relevant year's file, so legislated data
always beats extrapolation.
Extension conventions: indexation compounds assumed CPI once from the last
shipped file (a target year never depends on intermediate synthesized
years) and scales the money figures of the income-tax schedules, the
pension/ISA allowances, and the savings/dividend nil-rate amounts (the
starting-rate limit is legislated frozen with the rUK schedule, §6; the
PSA and dividend allowance follow the same reserved policy), quantized
to whole pounds (half-even); band, taper and dividend *rates* never
extrapolate. The marriage-allowance transferable amount is derived,
not indexed: each synthesized year computes 10% of its own reserved
PA, rounded up to the nearest £10 (HMRC PAYE100060 — §6), and the
recipient band gates never move. **Recurring task** after each Budget: copy previous
year's file, re-verify every figure, update `verified_on`/`sources`,
update §6.

### 5.4 Plan document format (`.glidepath.json` schema v7)

Implements the §4.5 decision (`glidepath/persistence/`, roadmap 6.2/6.4;
region-agnostic like the core — the region's shipped defaults and data
version are function inputs, never imports). Top level:

```json
{
  "schema_version": 7,
  "region": "uk",
  "assumptions_resolved_against": "<region data_version at last save>",
  "household": { "persons": [...], "spending": ..., "planned_outflows": [...],
                 "claim_marriage_allowance": ... },
  "assumption_overrides": [ { "key", "value", "source", "recorded_on" } ],
  "scenarios": [ { "name", "note", "overrides": [...] } ]
}
```

Conventions: canonical output is `json.dumps(sort_keys=True, indent=2)`
plus one trailing LF, written with newline translation disabled — a
given document always serializes to the same bytes and a load→save
round trip is byte-stable (a golden file pins the format). Value
representations are preserved exactly, never normalized: a `Decimal`
keeps the exponent the user stated (`1.0` and `1.00` compare equal but
keep their spellings) and a datetime keeps its offset. The writer never
produces a file the reader rejects — non-finite decimals, booleans in
whole-number fields, and empty entity ids are refused at save, and
serialization completes before the target file is opened so a failed
save cannot truncate the last valid file. `Decimal`
and `Money` are strings, never JSON floats (the reader rejects any JSON
float); datetimes are ISO-8601 with offset; dates ISO-8601. Every entity
field is present in full shape (`null` for absent optionals) and decoded
strictly by field context — missing or unknown keys fail loudly with a
document path. Enums persist as stable lowercase tokens that must never
change meaning (same rule as assumption keys). Polymorphic values
(assumption/scenario override values) use a closed tagged vocabulary —
`{"kind": "int" | "text" | "decimal" | "money" | "table" |
"annuity_type" | "annuity_basis", "value": ...}` — so exact runtime
types survive the round trip. Only assumption *overrides* are stored;
`resolve_assumptions` rebuilds the effective set on load from the
current shipped defaults (overrides re-stamped `USER_OVERRIDE`, keeping
the shipped default value and description; a stored value whose shape no
longer matches the shipped default fails loudly, the §4.3 rule).
Migration (roadmap 6.4): upgraders keyed by the schema version they
read, each stepping exactly one version, applied in sequence on load
before strict decoding; a current-version file passes through untouched,
and a newer-than-current file errors with an "upgrade glidepath"
message. Registered upgraders: v1→v2 (roadmap 9.6) adds
`"active_membership": null` to every DB pension — a v1 file's pensions
all load deferred; v2→v3 (#97) drops the state pension
qualifying-years fields (`ni_record_start`, `qualifying_years`,
`planned_extra_years`) — a migrated record keeps its deferral choice,
and one without a forecast fails projection with a clear demand for
one (§5.1); v3→v4 (#129) drops the accumulation-stage spending
multiplier keys (`early_accumulation`, `mid_accumulation`,
`pre_retirement`) that older builds accepted and wrote — spending is
modelled only in retirement, so they never scaled anything and the
drop loses no behaviour (#114 retired the tokens from the
`SpendingPlan` invariant without a migration, which left genuine
v1-era files unloadable until this step); v4→v5 (roadmap 9.28) adds
`"label": null` to every wrapper — a v4 file's wrappers all load
unnamed; v5→v6 (roadmap 9.32) adds
`"claim_marriage_allowance": null` to the household — a v5 file keeps
the default claim-when-eligible (§4.11); v6→v7 (roadmap 9.33) adds
`"death_age": null` to every person and `"survivor_fraction": null` to
every DB pension — a v6 file's persons all load alive to the horizon
and its schemes keep the `db.survivor_fraction` assumption default
(§4.11); v7→v8 (roadmap 9.34) adds `"survivor_fraction"` to every
annuity purchase — `null` for single-life, while a v7 joint-life
purchase gains the 50% decision its priced joint factor was always
quoting (the §7 table's joint factor is the joint-life 50% product's
relativity, so this records what the price already meant rather than
guessing among the §6 options).

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

### Income tax (Scotland — non-savings/non-dividend income)

| Band | Rate | Taxable income above PA | Source |
| --- | --- | --- | --- |
| Starter | 19% | £0–£3,967 | [gov.uk/scottish-income-tax](https://www.gov.uk/scottish-income-tax); [employer rates 2026–27](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027) |
| Basic | 20% | £3,968–£16,956 | same |
| Intermediate | 21% | £16,957–£31,092 | same |
| Higher | 42% | £31,093–£62,430 | same |
| Advanced | 45% | £62,431–£125,140 | same |
| Top | 48% | above £125,140 | same |

Scottish thresholds are set annually by the Scottish Parliament — the rUK
freeze to 2030/31 never governs them (the PA and its taper are reserved
and do follow it). Budget 2026-27 uprated the Basic and Intermediate
thresholds by 7.4% and maintains the Higher, Advanced and Top thresholds
at current levels until 2028-29 — a stated commitment, not legislation
(verified 2026-08-04:
[Scottish Budget 2026-27](https://www.gov.scot/publications/scottish-budget-2026-2027/pages/4/)).
Future-year extrapolation therefore treats the Scottish band groups
separately (§5.3, §7).

### Announced future-dated changes (model as data, not code)

| Change | Effective | Source |
| --- | --- | --- |
| Dividend rates +2ppt: ordinary 10.75%, upper 35.75% (additional 39.35% unchanged) | **2026/27 (in force)** | [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Savings income rates 22% / 42% / 47% | 6 April 2027 | same |
| Separate property income rates 22% / 42% / 47% | 6 April 2027 | same |
| Cash ISA limit £12,000 for under-65s (overall £20,000 unchanged) | 6 April 2027 | [ISA reform factsheet](https://www.gov.uk/government/publications/fiscal-events-2026-factsheets/isa-reform-2027-anti-circumvention-rules-factsheet) |
| NICs on salary-sacrificed pension contributions above £2,000/yr | April 2029 | [Employer Bulletin Dec 2025](https://www.gov.uk/government/publications/employer-bulletin-december-2025/december-2025-issue-of-the-employer-bulletin) |

### Savings and dividend taxation (2026/27)

Verified **2026-08-04** from live-fetched primary pages (roadmap 9.2 —
the GIA/cash wrappers bring these into the model).

| Figure | Value | Source |
| --- | --- | --- |
| Dividend allowance | £500/yr — a nil *rate*, not a deduction: nil-rated dividends still consume band width (HMRC pseudocode reduces the remaining band capacity by zero-rated allocations) | [gov.uk/tax-on-dividends](https://www.gov.uk/tax-on-dividends); [HMRC tax logic guide — tax calculation](https://developer.service.hmrc.gov.uk/guides/tax-logic-service-guide/documentation/tax-calculation.html) |
| Dividend rates | ordinary 10.75% (basic band), upper 35.75% (higher), additional 39.35% — the Budget 2025 +2ppt change, in force 2026/27 (see announced changes above) | [gov.uk/tax-on-dividends](https://www.gov.uk/tax-on-dividends) |
| Personal savings allowance | £1,000 basic / £500 higher / £0 additional rate — likewise a nil rate consuming band width; the tier follows the band the taxpayer's income reaches | [gov.uk/apply-tax-free-interest-on-savings](https://www.gov.uk/apply-tax-free-interest-on-savings); HMRC tax logic guide |
| Starting rate for savings | 0% on up to £5,000 of savings income; reduced £1 per £1 of non-savings income above the personal allowance (nil from £17,570); limit legislated 2026/27–2030/31 (§ income tax rUK above) | [gov.uk/apply-tax-free-interest-on-savings](https://www.gov.uk/apply-tax-free-interest-on-savings); [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Savings rates 2026/27 | savings income above the nil rates is taxed at the file's `savings.rates` schedule, aligned positionally with the rUK bands (#189) — equal to the main rates (20/40/45) in 2026/27; the separate 22/42/47 rates take effect 6 April 2027 and ship as data in the 2027/28 file | [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Income layer ordering | non-savings → savings → dividends up one ladder; savings and dividend income of Scottish taxpayers uses the rUK bands (Scottish rates cover non-savings/non-dividend income only) | HMRC tax logic guide; [gov.uk/scottish-income-tax](https://www.gov.uk/scottish-income-tax) |

### Pensions

| Figure | Value | Source |
| --- | --- | --- |
| Annual allowance | £60,000 — measures total *pension input amounts* incl. employer contributions and DB accrual; excess charged via the AA charge. Distinct from the member relief limit below | [pension scheme rates](https://www.gov.uk/government/publications/rates-and-allowances-pension-schemes/pension-schemes-rates); [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance) |
| DB pension input amount | per arrangement per tax year: closing value − opening value, each value = annual pension × **16** (the FA 2004 s234 relevant valuation factor; no separate lump sum is modelled — commutation is not a separate entitlement), opening value uprated by the 12-month CPI increase to the previous September (s235 "appropriate percentage"; the run's CPI path stands in), negative results nil (verified 2026-08-04) | [PTM053301](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm053301); [FA 2004 s234](https://www.legislation.gov.uk/ukpga/2004/12/section/234) |
| Member relief limit | tax relief on *member* contributions limited to 100% of relevant UK earnings; low/no earners keep the **£3,600 gross (£2,880 net)** basic amount, available via relief at source only. The limit is a per-person aggregate across all schemes and mechanics; contributions from **age 75** are never relievable (FA 2004 s188(3)(a)) (verified 2026-08-02) | [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance); [pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief); [FA 2004 s190](https://www.legislation.gov.uk/ukpga/2004/12/section/190); [PTM044100](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm044100) |
| AA taper | threshold income £200,000; adjusted income £260,000; −£1 per £2 (reduction rounded down to the whole £, PTM057100); floor £10,000. Adjusted income includes all employer-funded pension input (for DB: input amount net of member contributions). Known v1 limitation: the post-8-July-2015 salary-sacrifice add-back to threshold income is not modelled (no salary-sacrifice concept in v1) | rates page; [tapered AA guidance](https://www.gov.uk/guidance/pension-schemes-work-out-your-tapered-annual-allowance); [PTM057100](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm057100) |
| MPAA | £10,000; triggered by first FAD income payment, first UFPLS, etc. (not by PCLS-only or standard lifetime annuity); when triggered, DB accrual keeps an *alternative* annual allowance = AA − MPAA (£50,000; computed, not an independent figure — nil at maximum taper; carry-forward may top up the alternative AA but never the MPAA; verified 2026-08-02) | rates page; [PTM056520](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm056520); [HS345 (2026)](https://www.gov.uk/government/publications/pensions-tax-charges-on-any-excess-over-the-lifetime-allowance-annual-allowance-special-annual-allowance-and-on-unauthorised-payments-hs345-self/hs345-pension-savings-tax-charges-2026) |
| AA carry-forward | unused AA from the previous 3 tax years, drawn earliest year first and only to the extent an excess needs it (the rest survives within its window); only tax years with membership of a registered pension scheme (or qualifying overseas scheme) generate it; unused MPAA headroom never carries — in a year whose money-purchase inputs exceed the MPAA only unused *alternative* AA does, while within the MPAA the normal AA basis applies despite the trigger (PTM056510) — and carry-forward never tops up the MPAA (verified 2026-08-04) | [check unused annual allowances](https://www.gov.uk/guidance/check-if-you-have-unused-annual-allowances-on-your-pension-savings); [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance); [PTM056510](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm056510); [HS345 (2026)](https://www.gov.uk/government/publications/pensions-tax-charges-on-any-excess-over-the-lifetime-allowance-annual-allowance-special-annual-allowance-and-on-unauthorised-payments-hs345-self/hs345-pension-savings-tax-charges-2026) |
| Scheme Pays | mandatory when the year's total AA charge exceeds **£2,000** and the pension input amount to *that scheme* exceeds the **standard** AA (the s228 amount — the tapered AA and MPAA are ignored for the test); the scheme pays the charge and makes a consequential reduction to the member's benefits; a charge arising solely from the MPAA is excluded unless the standard-AA-basis charge would itself exceed £2,000; outside the conditions schemes may pay voluntarily (liability stays with the member) (verified 2026-08-06) | [PTM056410](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm056410); [FA 2004 s237B](https://www.legislation.gov.uk/ukpga/2004/12/section/237B) |
| Relief at source | provider adds 20% basic-rate relief (25% top-up on net); higher/additional via assessment — the basic rate limit and every limit above it are extended by the gross contribution, never the Scottish starter limit; starter-rate payers keep the 20% top-up (verified 2026-08-04) | [pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief); [SI 2018/459 note](https://www.legislation.gov.uk/uksi/2018/459/note/made) |
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
| Qualifying years | 35 full (pre-2016 contracted-out caveats); 10 minimum — context only: the model never derives an amount from qualifying years (#97, §5.1) | [what you'll get](https://www.gov.uk/new-state-pension/what-youll-get); [new state pension](https://www.gov.uk/new-state-pension) |
| Deferral | +1% per 9 weeks (~5.8%/yr); increments CPI-uprated | [deferring (post-2016)](https://www.gov.uk/deferring-state-pension/if-you-reach-state-pension-age-on-or-after-6-april-2016) |
| SPA 66→67 | phased Apr 2026–Mar 2028: DOB 1960-04-06–1960-05-05 → 66y 1m, +1 month per DOB month to 1961-02-06–1961-03-05 → 66y 11m; DOB 1961-03-06–1977-04-05 → **67** | [SPA timetable](https://www.gov.uk/government/publications/state-pension-age-timetable/state-pension-age-timetable) |
| SPA 67→68 | legislated 2044–2046: DOB 1977-04-06–1978-04-05 phased; DOB ≥ 1978-04-06 → 68 | same |
| SPA review | third review launched July 2025, ongoing; no change legislated as of 2026-08-01 | [third SPA review](https://www.gov.uk/government/collections/third-state-pension-age-review) |
| Triple lock | committed "for this parliament" (~2029); nothing legislated beyond | [Budget 2025 fact sheet](https://www.gov.uk/government/news/budget-2025-fact-sheet-cutting-the-cost-of-living) |

### Couples

Verified **2026-08-11** from live-fetched primary pages (the 9.4 spike —
these figures back the §4.11 decision record and become data keys as the
9.29–9.34 increments ship).

| Figure | Value | Source |
| --- | --- | --- |
| Marriage allowance mechanics | A **tax reducer**, not a PA transfer: the recipient's tax is reduced by the basic-rate percentage of the transferable amount (£1,260, statutorily 10% of the PA **rounded up to the nearest £10** — 2018/19's PA of £11,850 gave £1,190), max £252/yr. Transferor's income must be below their PA; recipient liable at no more than basic rate (rUK) or the starter/basic/intermediate rates (Scotland) | [gov.uk/marriage-allowance](https://www.gov.uk/marriage-allowance); [ITA 2007 s55B](https://www.legislation.gov.uk/ukpga/2007/3/section/55B); [PAYE100060](https://www.gov.uk/hmrc-internal-manuals/paye-manual/paye100060) (rounding, verified 2026-08-12) |
| New state pension inheritance | A survivor inherits **half the deceased's protected payment** only if the marriage/CP began before 6 April 2016 and the deceased reached SPA (and died) on/after that date; inherited additional state pension and deferral-increment inheritance attach only to pre-2016 SPA cases; remarriage before the survivor's own SPA disqualifies. Net: a post-2016 couple with no protected payment passes on **nothing** | [inheriting state pension](https://www.gov.uk/new-state-pension/inheriting-or-increasing-state-pension-from-a-spouse-or-civil-partner) |
| DC death benefits | Death **before 75**: beneficiary drawdown income tax-free (funds first designated post-April 2015); lump sums tax-free up to the deceased's LSDBA (£1,073,100). Death **at/after 75**: income and lump sums taxed at the beneficiary's marginal rate. Lump sums paid >2 years after notification taxed regardless | [tax on pension death benefits](https://www.gov.uk/tax-on-pension-death-benefits); [individual lump sum allowances](https://www.gov.uk/guidance/find-out-the-rules-around-individual-lump-sum-allowances) |
| Pensions into IHT | **Enacted** (FA 2026, Royal Assent 18 March 2026): unused pension funds and death benefits join the estate for deaths on/after **6 April 2027**; death-in-service and DB dependants' scheme pensions excluded; the **spouse/civil-partner exemption is maintained**, so partner-to-partner transfers stay IHT-free (§4.11 keeps IHT out of scope on that basis) | [technical note (upd. 29 May 2026)](https://www.gov.uk/government/publications/inheritance-tax-on-pensions-technical-note/technical-note-inheritance-tax-on-pensions); [policy paper](https://www.gov.uk/government/publications/inheritance-tax-unused-pension-funds-and-death-benefits/inheritance-tax-unused-pension-funds-and-death-benefits) |
| ISA additional permitted subscription | Surviving spouse/CP gets a one-off extra ISA allowance equal to the deceased's ISA value at death (or closure, deaths on/after 6 April 2018), **in addition to** their own annual allowance, independent of who inherits the assets; spouses/CPs only | [inheriting an ISA](https://www.gov.uk/individual-savings-accounts/inheriting-an-isa-from-your-spouse-civil-partner); [if you die](https://www.gov.uk/individual-savings-accounts/if-you-die) |
| DB survivor pensions | **No statutory fraction for private schemes** — scheme rules govern (PPF). Public-service context: Civil Service Classic, NHS 1995, Teachers' NPA60, Police 1987, Firefighters 1992, AFPS 1975 all pay 50% of the member's pension; LGPS accrues survivor pension at 1/160th of service. Ships as the `db.survivor_fraction` **assumption** (default 50%), overridable per scheme | [PPF on DB beliefs](https://www.ppf.co.uk/blog-posts/defined-benefit-pension-beliefs); [DWP survivor-benefits annex (PDF)](https://assets.publishing.service.gov.uk/media/5a7ee054e5274a2e8ab48c2b/survivor-benefits-in-occupational-pension-schemes-annex-a.pdf) |
| Joint-life annuities | Survivor income settable at **100%, 66% or 50%** of the initial rate; starting income lower than the single-life equivalent (the §7 `annuity.age_adjustment` joint factor 0.92 prices this) | [FCA annuities review (PDF, option structure)](https://www.fca.org.uk/publication/research/annuities-consumer-behaviour-review.pdf); [MoneyHelper annuities](https://www.moneyhelper.org.uk/en/pensions-and-retirement/taking-your-pension/guaranteed-retirement-income-annuities-explained) |
| Spousal transfers (living) | Gifts between spouses/CPs: **no CGT** (no gain/no loss, base cost carries) unless separated all year; **no IHT** (unlimited exemption). **No sharing of allowances between living spouses**: pension AA, LSA/LSDBA and the ISA allowance are strictly per person (verified by absence of any transfer mechanism on the primary pages; marriage allowance is the sole PA-related spousal mechanism) | [CGT gifts](https://www.gov.uk/capital-gains-tax/gifts); [IHT](https://www.gov.uk/inheritance-tax); [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance); [lump sum allowances](https://www.gov.uk/guidance/find-out-the-rules-around-individual-lump-sum-allowances); [ISAs](https://www.gov.uk/individual-savings-accounts) |
| Third-party pension contributions | Anyone may pay into another person's pension; relief goes to the **member** at the member's rate (relief at source, higher rates via assessment); non-earners keep the £3,600 gross (£2,880 net) basic amount. Recorded for a future increment — not modelled in 9.29–9.34 (§4.11) | [pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief) |

## 7. Default assumptions

Every row is a shipped default the user can override; each carries its
basis. Recorded 2026-08-01 (the `yield.*` rows and the annuity
age-adjustment table 2026-08-03; the couples survivor rows
2026-08-12). This table is the human-readable
mirror of `regions/uk/data/assumptions_default.toml` (doc-sync test in
Phase 2). Announced-policy items in §6 are *facts*; these are estimates.

### Economic

| Key | Default | Basis |
| --- | --- | --- |
| `inflation.cpi` | 2.0%/yr | OBR EFO March 2026: CPI at target from 2027 ([obr.uk EFO](https://obr.uk/efo/economic-and-fiscal-outlook-march-2026/)) |
| `earnings.growth.real` | 0.5%/yr | OBR EFO March 2026 medium-term real earnings growth |
| `returns.equity.real` | 4.0%/yr | Below long-run global equity history (~5% real); above FCA intermediate (5% nominal − 2% CPI = 3% real) as conservative cross-check ([COBS 13 Annex 2](https://www.handbook.fca.org.uk/handbook/COBS/13/Annex2.html): 2/5/8% nominal maxima, tax-advantaged) |
| `returns.bonds.real` | 0.5%/yr | Consistent with current gilt real-yield ballpark and FCA lower rate |
| `returns.cash.real` | −0.5%/yr | Cash trails inflation over long horizons; cash-kind accounts bear no default fees, so this is their whole return — a cash slice inside a platform wrapper bears that wrapper's fees on top |
| `volatility.equity` | 18%/yr | Long-run global equity annual volatility (commonly cited 15–20%) |
| `volatility.bonds` | 7%/yr | Long-run gilt/IG portfolio volatility |
| `volatility.cash` | 1%/yr | Near-riskless nominal |
| `correlation.equity_bonds` | 0.2 | Long-run average; regime-dependent (label prominently) |
| `correlation.equity_cash` | 0.0 | Near-zero long-run historical correlation |
| `correlation.bonds_cash` | 0.2 | Modest positive long-run historical correlation (short rates feed both) |
| `fees.platform` | 0.25%/yr | Typical UK platform fee |
| `fees.fund` | 0.15%/yr | Typical index-tracker OCF |
| `yield.equity` | 2.0%/yr | Long-run global equity dividend yield (~2%); consistent with the shipped 4% real return default as income plus real capital growth |
| `yield.bonds` | 2.5%/yr | Bond income ≈ the nominal return for portfolios held near par: the shipped 0.5% real return default plus 2% CPI |
| `yield.cash` | 1.5%/yr | Cash return is wholly income: the shipped −0.5% real return default plus 2% CPI |

### Longevity, policy futures, annuities

| Key | Default | Basis |
| --- | --- | --- |
| `horizon.planning_age` | 95 | ~1-in-4 longevity risk at 65 per ONS cohort life expectancy ([ONS calculator](https://www.ons.gov.uk/peoplepopulationandcommunity/healthandsocialcare/healthandlifeexpectancies/articles/lifeexpectancycalculator/2019-06-07); exact values from 2024-based cohort tables at implementation) |
| `glidepath.default_shape` | 80/20 equity/bonds until 15 years to retirement, then linear de-risk to 40/60 at retirement, held through drawdown | Typical UK target-date/lifestyling shape; a starting point only — per-person glide paths override it |
| `policy.state_pension.uprating` | `triple_lock` (proxied as max(CPI + 0.5%, floor); the margin stands in for the long-run earnings premium) | Alternative scenario: `cpi`. The proxy applies in every run mode — CPI is deterministic across Monte Carlo paths by design and no earnings series is modelled, so each path uprates identically; protected payments always uprate by CPI only (§5.1). Triple lock committed only to ~2029 (§6) |
| `policy.tax.future_years` | rUK + reserved figures (PA/taper both regimes, pension/ISA allowances) frozen to 2030/31 (legislated), then CPI-indexed; Scottish lower-band uppers CPI-indexed past the shipped year (set annually — uprating proxy), Higher/Advanced/Top uppers frozen to 2028/29 (announced), then CPI-indexed | rUK/reserved freeze is fact (§6); Scottish freeze horizons are announced policy, not legislation (§6); post-freeze indexation is assumption. Alternative: frozen indefinitely |
| `annuity.level.single.65` | 7.75%/yr per £ purchase | Which? market table, snapshot 2026-07-27, retrieved 2026-08-01 ([which.co.uk](https://www.which.co.uk/money/pensions-and-retirement/accessing-your-pensions/annuities/annuity-rates-aQGfH6W5n2rm)); best rate 7.946% — volatile market snapshot, refresh before relying on |
| `annuity.escalating3.single.65` | 5.47%/yr | same snapshot |
| `annuity.inflation_linked.single.65` | 5.5%/yr | Indicative only — secondary source ([IFA Magazine](https://ifamagazine.com/annuity-rates-hit-7-75-as-retirement-incomes-reach-18-year-high/)); weakest-sourced default here |
| `annuity.age_adjustment` | Per-age/type multipliers on the single-65 base rates (knots at 55–75, linear interpolation between, no extrapolation outside); joint-life factor 0.92 — the one factor whatever the purchase's survivor fraction, a **v1 limitation** (real quotes price the 50/66/100% options differently; the HL snapshot prices a 50% survivor product); escalating products increase 3%/yr | Relativities from the Hargreaves Lansdown best-buy annuity tables (single/joint life; level, RPI-linked, 3% escalation), snapshot 2026-07-31, retrieved 2026-08-03 ([hl.co.uk](https://www.hl.co.uk/retirement/annuities/best-buy-rates)); the inflation-linked curve uses the RPI-linked product's relativities |

*The `yield.*` rows price the natural-yield withdrawal strategy
(roadmap 5.3) and the portfolio income of taxable-growth wrappers
(roadmap 9.2); they are read — and recorded in provenance — only when
that strategy runs or the plan holds a GIA/cash wrapper.*

### Couples

| Key | Default | Basis |
| --- | --- | --- |
| `db.survivor_fraction` | 50% | No statutory fraction for private DB schemes — scheme rules govern (§6, PPF); the major public-service legacy schemes all pay 50%. Overridable per scheme via the DB pension's `survivor_fraction` fact ([PPF on DB beliefs](https://www.ppf.co.uk/blog-posts/defined-benefit-pension-beliefs)) |
| `spending.survivor_multiplier` | 0.70 × household spending | PLSA/Pensions UK Retirement Living Standards single-vs-couple budget ratios — £13,900/£22,500 = 0.62 (Minimum), £32,700/£45,400 = 0.72 (Moderate), £45,400/£62,700 = 0.72 (Comfortable), retrieved 2026-08-12 ([retirementlivingstandards.org.uk](https://www.retirementlivingstandards.org.uk/)); 0.70 sits just under the Moderate/Comfortable ratio (§9 open question 9, resolved) |

## 8. Phased roadmap — issue basis

Each item becomes one GitHub issue (~½–2 days); a tick means the issue
shipped to `main`. Format: item — *acceptance criterion*. Items within a
phase are mostly parallelisable; phases are dependency-ordered. Labels:
`core`, `region:uk`, `data-files`, `docs`, `gui`, `needs-verification`.

### Phase 1 — Core primitives (no UK anything)

- [x] 1.1 `Money`/`Rate` value types + rounding policy — *quantization rules
  of §5.2 enforced and property-tested (Hypothesis).*
- [x] 1.2 `Period` + `FiscalCalendar` protocol + generic annual calendar —
  *periods iterate an arbitrary horizon; birthday-in-period helpers tested
  at boundaries (§4.1).*
- [x] 1.3 `Fact[T]`, `Assumption[T]`, `Decision[T]`, `AssumptionKey`,
  `AssumptionSet` with read-tracking — *engine-side reads recorded;
  provenance enum round-trips.*
- [x] 1.4 `Household`/`Person` skeleton — *1–2 persons representable with
  stable `EntityId`s; v1 validator rejects 2 (§4.4).*
- [x] 1.5 Boundary guard tests — *test fails if `core` imports `regions.*`;
  grep test fails on policy-figure literals outside `regions/uk/data/`.*

### Phase 2 — UK region data and tax

- [x] 2.1 Promote `regions/uk.py` → package; TOML loader with strict
  validation — *unknown keys, missing meta, or float-typed money are load
  errors (§5.3).*
- [x] 2.2 `tax_year_2026_27.toml` + `age_rules.toml` +
  `assumptions_default.toml` from §6/§7 — *loader tests pass; `verified_on`
  + `sources` present; doc-sync test keeps §7 aligned with the defaults
  file.*
- [x] 2.3 rUK income tax assessment (bands + PA taper) — *golden tests match
  hand-worked HMRC examples incl. the £100k–£125,140 zone.*
- [x] 2.4 `AgeRules`: SPA from DOB (banded), NMPA schedule, LISA ages —
  *boundary tests either side of every band edge, the 2028-04-06 step, and
  the §4.1 access-gate / pro-rated-income convention.*
- [x] 2.5 Future-year extension policy — *`policy.tax.future_years` drives
  extrapolation past the last data file (§5.3).*

### Phase 3 — Wrappers and accumulation

- [x] 3.1 Wrapper model (DC/SIPP/ISA) + region `WrapperRuleset` — *tax
  treatment in/during/out resolved per wrapper kind.*
- [x] 3.2 Contribution schedules: employee/employer, relief at source vs net
  pay — *mechanics match gov.uk worked examples; higher-rate relief via tax
  assessment.*
- [x] 3.3 Annual allowance + taper + MPAA — *taper arithmetic golden-tested;
  AA measures pension input amounts (incl. employer/DB) separately from the
  member relief limit (§6); MPAA flips on first flexible access, persists,
  and leaves the alternative allowance for DB accrual (§9). Engine-wired
  (#116): each period's inputs are measured through the region ruleset at
  §5.2 step 5 and the chargeable excess is priced as top-slice tax lines
  (FA 2004 s227B) appended to the period's assessment.*
- [x] 3.4 Fees and growth application — *applied per §5.2 operation order.*
- [x] 3.5 Glide-path / life-stage allocation — *allocation interpolates the
  years-to-retirement table; stage derived, not stored.*

### Phase 4 — Deterministic projection (first end-to-end result)

- [x] 4.1 Engine step loop with specified operation order + `PeriodSnapshot`
  / `ProjectionResult` incl. provenance — *order-of-operations test fixes
  the spec; net-need withdrawals gross up against the tax system (§5.2
  step 4); provenance lists every assumption read.*
- [x] 4.2 DB pension: revaluation in deferment, NPA, early/late factors,
  commutation — *scheme facts drive results; commutation trades pension for
  lump sum at the stated factor.*
- [x] 4.3 State pension: forecast-as-fact, SPA, deferral, uprating
  assumption — *protected payments uprate by CPI only. Originally also
  shipped a ÷35 qualifying-years derivation; removed by 9.17 — the DWP
  forecast is the only route (§5.1).*
- [x] 4.4 Real/nominal reporting layer — *real default; nominal available;
  one CPI path per run.*
- [x] 4.5 End-to-end golden scenario — *"35-year-old, DC + ISA, retires at
  60" produces a reviewed, checked-in expected output.* Land after 4.6 so
  the golden output is written once against corrected partial-period
  behaviour.
- [x] 4.6 Partial first/last period pro-rating — *a mid-period `today`
  pro-rates flows (income, contributions, spending need) by whole months
  per §4.1; the growth/fee partial-period convention is decided and
  recorded in §5.2; the run never models time before `today`.*

### Phase 5 — Decumulation

- [x] 5.1 `WithdrawalStrategy` protocol + fixed-real + fixed-% —
  *strategies respect access ages and wrapper ordering.*
- [x] 5.2 Tax-free cash strategy: PCLS vs UFPLS vs FAD + LSA tracking —
  *25%/LSA cap enforced; UFPLS payments split 25/75; MPAA triggers fire;
  starting `lsa_used` / `mpaa_triggered_on` / `crystallised_balance` facts
  respected (§5.1).*
- [x] 5.3 Guardrails + natural-yield strategies — *band crossings adjust
  spending per configured rules.*
- [x] 5.4 One-off planned outflows — *outflows hit the chosen period,
  tax-aware.*
- [x] 5.5 Annuity purchase — *level/escalating/inflation-linked,
  single/joint, partial annuitisation mid-drawdown priced from the
  annuity-rate assumption table.*

### Phase 6 — Scenarios and persistence

- [x] 6.1 `Scenario`/`Override` model + resolution — *base ⊕ overrides with
  `SCENARIO_OVERRIDE` provenance; entity-id targeting; orphaned targets
  flag the scenario invalid without breaking file load (§4.3).*
- [x] 6.2 `.glidepath.json` schema v1 + canonical reader/writer —
  *round-trip property tests; deterministic byte-identical output (§4.5).*
- [x] 6.3 Scenario comparison report — *per-period metric diffs across
  scenarios.*
- [x] 6.4 Schema migration harness — *versioned upgraders; v1→v1 no-op
  wired.*

### Phase 7 — Monte Carlo

- [x] 7.1 `RandomSource` protocol + seeded impl — *reproducibility property
  test: same inputs + seed → identical result (§4.6).*
- [x] 7.2 Stochastic `ReturnModel`: lognormal + correlations (Decimal
  Cholesky) — *includes a performance measurement task with recorded
  numbers.*
- [x] 7.3 Path runner + success metrics — *probability of ruin, sustainable
  income, ending-pot percentiles over paths.*
- [x] 7.4 Sequence-of-returns fixtures — *same returns, different order →
  demonstrably different outcome.*

### Phase 8 — GUI (PySide6 shell over the app layer)

All Phase 8 work follows §4.7: view models, copy, and formatting live in
the UI-agnostic `glidepath.app` layer (guard-tested Qt-free); PySide6
widgets in `glidepath.gui` stay thin so a web shell can be added later.

- [x] 8.1 `make deps` PySide6; app shell + **disclaimer screen** —
  *disclaimer on first run (§1); §4.7 layering in place with the
  Qt-import guard test.*
- [x] 8.2 Facts entry forms — *every fact in §5.1 enterable; `as_of`
  fields offered only for statement-dated facts (wrapper balances, the
  DWP forecast, the DB statement date) — every other
  fact is dated the day it is entered, since its `as_of` carries no
  modelling meaning and the extra date inputs only cluttered the form
  (originally every fact had one). On resubmission the parse carries
  those facts' stored dates forward from the plan being replaced
  wherever the value is unchanged, so a load → edit → save cycle never
  silently rewrites persisted `as_of` provenance. Every repeatable row
  (wrapper, DB pension, annuity purchase) carries its entity id
  opaquely through the form (`ENTITY_ID_KEY`; blank means a new
  entity), so identity — and any scenario override targeting it by
  stable id (§4.3) — survives edits, reordering, and row deletion
  alike; shells seed freshly minted ids back into their rows after a
  successful save so the next resubmission edits the same entities.
  Notes on facts and decisions have no form field yet, so a plan
  carrying one is refused at open (`form_cannot_represent`) rather
  than silently stripped on the next save. Date-valued fields are marked
  `FieldKind.DATE`: the raw value stays ISO text and blank keeps its
  meaning (today / none), so shells render typed entry first-class with
  a calendar as an assist — never a spinner-style date widget that
  cannot be blank.*
- [x] 8.3 Assumptions inspector — *the "stated vs assumed" surface rendered
  from `ProjectionResult.provenance`; defaults overridable in place. The
  surface reads for humans: assumption ids show as display names (the
  dotted id stays in tooltips and override targeting), the shipped
  default renders only when an override makes it differ, and the run
  manifest (digests, policy parameters) sits behind a human summary
  line as its tooltip rather than on the page.*
- [x] 8.4 Projection charts — *real-terms default, nominal toggle.*
- [x] 8.5 Scenario manager + diff view — *scenarios as override lists;
  comparison report visualised.*

### Phase 9 — Extensions

- [x] 9.1 Scottish bands activation — *`tax_residency = SCOTLAND` uses the
  Scottish table already shipped in data.*
- [x] 9.2 LISA/GIA/cash wrappers — *LISA bonus/charge/ages; GIA brings
  dividend/savings taxation (2026/27 dividend data already verified in §6).
  Shipped: TEE LISA with the 25% bonus inside the 18–50 window, the £4k
  sub-allowance inside the overall ISA allowance, and the age-60 gate
  (the withdrawal charge ships as data; gated funds are never drawn);
  GIA/cash with yield-priced dividend/savings income taxed per §6 and
  charged to the wrapper; the full GIA/cash → ISA → pension ordering;
  decumulation surplus banking (§5.2). CGT stays out of scope (§2).*
- [ ] 9.3 New tax-year data file after each Budget — *recurring; process in
  §5.3.*
- [x] 9.4 Couples activation spike (#45) — *survivor benefits, marriage
  allowance, joint annuities and transfers scoped; decision record
  §4.11, verified figures §6 "Couples", implementation raised as
  9.29–9.34. Headline decisions: partner strictly optional; pooled
  household decumulation with a greedy marginal-cost split; horizon at
  the later planning age; deterministic optional death ages driving
  survivor transfers (beneficiary drawdown, ISA APS, DB survivor
  fraction, joint-life continuation); marriage allowance as the s55B
  tax reducer via a region-level household adjustment step; living
  transfers and IHT deferred with rationale.*
- [x] 9.5 AA carry-forward — *3-year rule per gov.uk guidance. Shipped:
  `aa_carry_forward_years` (3) in the tax-year data; the assessment
  records its inputs and exposes the year's carry-forward-able unused
  allowance; `apply_carry_forward` sets the pool against an assessed
  excess earliest year first, drawing only what reduces the charge and
  topping up both s227ZA computations but never the MPAA;
  `carry_forward_generated` gates generation on scheme membership;
  `roll_carry_forward` advances the pool, expiring the oldest year.
  Engine-wired with the AA charge (#116): the pool starts empty at the
  run start (§4.1 conservative — pre-run years' unused allowance is
  unknown) and rolls forward with each period's assessment (§5.2).*
- [x] 9.6 DB active accrual — *accrual rate, pensionable salary and service
  projection for active DB membership. Shipped: `DBActiveMembership`
  (accrual rate, pensionable salary, optional leave-and-defer age) on
  `DBPension`; CARE-style accrual per §5.1 — statement→today span at the
  stated salary, in-run credits at the period open escalating with
  earnings growth, service gated by the retirement/leave/taken
  boundaries; `pension.db_valuation_factor` (16) in the tax-year data
  and the region's `db_pension_input_amount` per PTM053301, read by the
  engine each period through the annual-allowance measurement (#116;
  §5.2 step 5). Final-salary linkage,
  split revaluation bases and member DB contributions stay out (§2,
  §5.1).*
- [x] 9.7 Launch example + shell theme — *the facts form opens with the
  §4.9 example plan projected and a clear button resetting the session;
  a Fusion-based theme in `gui/style.py` (presentation only, no copy or
  policy) built around the brand green, with the icon and About/README
  wordmark shipped as packaged assets under `gui/assets/`.*
- [x] 9.8 Plan save/load in the shell — *the §4.5 persistence layer wired
  to a File menu (Open/Save/Save As over `.glidepath.json`, stored
  wherever the user chooses). Shipped: `app/files.py` folds save/load
  into status messages, records the shipped data version at save, and
  notes on load when shipped defaults have moved since;
  `facts_form_data_from_household` inverts `parse_facts_form` so a
  loaded plan repopulates the facts form (round-trip pinned by test,
  entity ids preserved via the resubmission path so scenario overrides
  survive); the per-user settings file additionally remembers the last
  plan path and the next launch reopens it, falling back to the launch
  example. Data-safety guarantees: saves are atomic (sibling temp file
  + rename, so a mid-write failure never truncates the last saved
  plan); a plan the v1 form cannot faithfully edit
  (`form_cannot_represent` — extra persons, planned outflows, joint-life
  annuity purchases (since 9.12), personal glide paths, whole-retirement
  spending multipliers (the sub-stage multipliers gained form fields
  with issue #114), wrapper allocations/fees, independently dated fact
  pairs, notes on facts or decisions) is refused at open
  rather than silently reduced on the next save; stored table
  overrides (base and per-scenario) are vetted by their policy parsers
  at load so a defective table fails the open, never a run mid-flight;
  clearing the form detaches the session's plan file so Save re-asks.*
- [x] 9.9 In-app help + About layout — *Help → "How to use glidepath"
  guide dialog: copy lives in the app layer per §4.7 (one card per
  shell surface — example plan, Facts, Charts, Scenarios, stated vs
  assumed, save/load — closing by repeating the §1 disclaimer); the
  About box rebuilt as a dialog with the wordmark above full-width
  wrapped copy, replacing the `QMessageBox` that squeezed the
  disclaimer into a narrow column beside the wordmark.*
- [x] 9.10 Facts & inspector readability — *the facts form keeps `as_of`
  inputs only for statement-dated facts and renders every date field as
  a typed-first date entry with a calendar assist (`FieldKind.DATE`);
  the stated-vs-assumed surface restacks so no table is squeezed into a
  horizontal scrollbar and reads for humans — assumption display names,
  manifest behind a summary tooltip, defaults shown only when they
  differ (details under roadmap 8.2/8.3).*
- [x] 9.11 Age on chart axes — *chart categories and bar tooltips carry
  the person's age at period start alongside the year (§4.7): every
  category label reads `year · age` (e.g. `2032 · 60`) in both money
  bases, and the tooltips inherit it as the category copy;
  single-person labelling until couples activate (9.4) — a two-person
  period falls back to the year alone.*
- [x] 9.12 Annuity purchase entry in the facts form — *purchase age, pot
  fraction, and product type (level / escalating / inflation-linked)
  enterable in a repeatable form section; every field a decision (§5.1),
  ids carried per row on resubmission so scenario overrides survive (§4.3);
  round-tripped through `facts_form_data_from_household`, so the income
  chart's existing annuity series is now reachable from the shell. The
  single-person form writes single-life purchases only:
  `form_cannot_represent` now refuses just joint-life purchases
  (couples, 9.4) rather than all annuity purchases.*
- [x] 9.13 Monte Carlo in the GUI — *the Phase 7 core surfaced per §4.7:
  run-mode control (deterministic | Monte Carlo with paths + seed),
  success-metrics readout (success rate, probability of ruin, ending-pot
  percentiles), percentile bands on the balances chart in either basis
  (re-homed by 9.24 — the bands now draw as the fan chart's own tab);
  same seed + inputs reproduce identical results (§4.6). The run is an
  explicit action (default 100 paths ≈ 4 s at the §4.6 measurements;
  path count capped at 10,000) and executes on a worker thread — the
  shell stays responsive, the run button disables while one is in
  flight, and a result whose input state was replaced mid-run is
  discarded rather than adopted. The transition re-anchors the base
  projection (and scenario runs) to the same `today` before the paths
  run, so the bands and the chart they overlay always share one
  valuation date. Each path now also reduces to its per-period
  household closing balances so the 10/50/90 bands chart from
  `MonteCarloResult.balance_percentile`, and every plan-changing
  transition drops the held result so a stale Monte Carlo surface can
  never show against a changed plan. CPI is deterministic across paths
  (§5.2), so the bands and ending pots deflate to today's money by the
  deterministic report's own deflators.*
- [x] 9.14 "When can I retire?" solver — *earliest target retirement age
  meeting a replacement-rate target (default 66% of current employment
  income, user-adjustable), mirroring the roadmap 7.3
  `sustainable_income` search over runs. `earliest_retirement_age`
  probes candidate ages in ascending order — the age domain is a few
  dozen whole years, so the scan returns the exact earliest success
  even where success is not monotone in age, cheaper and more robust
  than the spending search's scan-plus-bisection over a continuum.
  Each probe replaces the retirement-age decision and the spending
  plan (with the target income: the whole-percent rate times stated
  employment income, treated as the net spending need in today's
  money) and reuses one config — common random numbers, reproducible
  from the seed (§4.6). Deterministic success is "no period's need
  unmet" (the §5.2 ruin signal); under the charts screen's Monte Carlo
  mode the same solver reads "success rate ≥ target" over the panel's
  seed and path count. The charts-screen card asks for the rate
  (default 66%) plus a success target (default 90%, Monte Carlo mode
  only), searches from the person's current age to the planning age
  minus one, runs on the worker thread with the 9.13 staleness and
  re-anchoring rules, and reports the earliest age — or that none in
  range meets — with the target income and the basis it was computed
  on. A candidate with no retired period inside the projected horizon
  never tests the income and fails rather than succeeding vacuously; a
  Monte Carlo search is additionally bounded to 20,000
  path-projections across its candidate ages (the per-run path cap
  alone would let an unsuccessful search multiply to hundreds of
  thousands); and the Monte Carlo run and the search share one
  in-flight guard, since a second slow run launched mid-flight could
  only ever be discarded as stale.*
- [x] 9.15 Monte Carlo performance: cached tax-year synthesis + parallel
  paths — *the §4.6 optimisation revisit, gated on its measurements
  (before/after numbers recorded there). `extend_tax_year` is memoized
  (pure over immutable inputs; `TaxYearFile` hashes by meta alone so
  the cache key never walks the figure tree), which alone roughly
  halves every run sharing the tax-year lookup path — Monte Carlo,
  deterministic, and the 9.14 solver. `run_paths` optionally spreads
  its paths over a `PathParallelism` executor in contiguous chunks
  (one argument pickle per worker, only reduced outcomes shipped
  back), bit-identical to the serial run by construction and by test;
  `sustainable_income` and `earliest_retirement_age` pass one executor
  through every probe. The app layer owns the policy (`path_pool`):
  runs of at least 100 path-projections get a process pool sized to
  every core but one (the GUI keeps a core; capped at 61, the Windows
  wait-handle ceiling `ProcessPoolExecutor` enforces), anything
  smaller stays serial, the transitions fold *any* run failure into
  state so a process-boundary error can never strand the shell's
  in-flight guard, and the 9.13 worker-thread + staleness rules are
  untouched.
  Structured shipped defaults now ride in a picklable `FrozenTable`
  instead of `MappingProxyType` so the assumption set survives the
  trip to worker processes (guard-tested).*
- [x] 9.16 Busy indicator for the slow runs — *an indeterminate
  progress bar plus a status line on the charts tab, visible from run
  start until the result is adopted, rejected, or discarded as stale
  (the 9.13 discard path must never leave a spinner running — pinned
  by test). The status copy comes from the app layer per §4.7:
  `monte_carlo_running_status` names the path count ("Running Monte
  Carlo — 1,000 paths…") when the raw text parses, falling back to the
  plain running message, and the retirement search reuses its existing
  running copy; the status bar shows the same line. Disabled buttons
  alone were easy to miss — a minutes-long Monte Carlo retirement
  search looked like a hang.*
- [x] 9.17 State pension: DWP forecast only (#97) — *the ÷35
  qualifying-years derivation and its gates are deleted; the official
  forecast (gov.uk/check-state-pension) is the only route to an
  amount (§5.1). The record and facts form lose the qualifying-years,
  NI-record-start and planned-extra-years fields; the tax-year files
  lose the `[state_pension]` table and `age_rules.toml` the
  `[new_state_pension]` system-start gate (nothing reads them; the
  data-file schema version steps to 2 for the changed shape);
  document schema v3 migrates saved plans by dropping the retired
  fields, and a migrated record without a forecast keeps its deferral
  choice but fails projection with a clear demand for a forecast —
  the facts form requires one on the next save.*
- [x] 9.18 Historical backtesting (#103) — *run the plan over every
  rolling historical window of an annual real-return series, as a
  complement to Monte Carlo: rolling windows preserve the
  sequence-of-returns and regime behaviour that independent lognormal
  draws miss. The series ships as `returns_history.toml` (§5.3
  provenance pattern): per year 1900–2020, nominal world equity total
  returns in GBP terms, UK long-gilt total returns, UK bills, and UK
  CPI inflation, derived from the JST Macrohistory Database R6 by
  `scripts/build_returns_history.py` — the world equity series is the
  GDP-weighted average of the 16-to-18 JST countries' local returns
  converted into GBP (currency effects included), the JST papers' own
  world-index convention; the JST `gdp` column is in country-specific
  units (millions/billions/trillions of local currency), so the script
  normalises it to a common unit before forming USD weights (#108
  fixed weights that had effectively excluded the US and Japan); the
  file is CC BY-NC-SA 4.0 with attribution
  (unlike the MIT code — noted in the file header and README), a
  licence-clean source where MSCI/DMS/Barclays data could not be
  redistributed. Sanity anchors at derivation: 1900–2020 geometric
  real means of +6.4% equity / +1.3% gilts / +0.5% bills, with 1974,
  2002, 1990, 1931, 1920 the worst real equity years. Engine:
  `run_windows` projects window *w* through the ordinary deterministic
  `run` with a `HistoricalWindowModel` factory — the same one step
  function, no seed, fully reproducible; each observed year's real
  return (nominal deflated by that year's CPI) is recomposed with the
  run's assumed CPI, keeping the one-inflation-truth rule and making
  windows comparable in today's money (the accepted cost — frozen tax
  bands meet assumed, not historical, inflation — is exactly Monte
  Carlo's). An M-year series over an N-period horizon runs M−N+1
  windows; a horizon the series cannot cover fails with a pointed
  error. `BacktestResult` mirrors the Monte Carlo metrics: success
  rate over windows, ending-pot/balance percentiles by the same
  interpolation, plus `worst_window` (ruined windows before survivors,
  earliest shortfall first, then lowest ending pot, ties to the
  earliest start year). GUI per §4.7: a charts-screen card next to the
  Monte Carlo panel (Run backtest, success rate, window span, worst
  starting year with the plan-calendar year the money ran out,
  ending-pot percentiles) under the 9.13 worker-thread + staleness +
  re-anchoring rules and the 9.16 busy indicator; a held backtest
  draws the worst and best starting years' *actual balance
  trajectories* over the balances chart in either run mode, each line
  labelled with its year, plus whichever starting year the card's
  picker names ("show me 1973") — real window paths rather than
  pointwise percentile bands, because a window is a genuine
  historical outcome where a Monte Carlo extreme is sampling noise;
  the 10/50/90 ending-pot percentiles stay on the card, and the mean
  is deliberately omitted (right-skewed pots read optimistic against
  the median). The picker is presentation state like the basis and
  mode selections — no run happens; a miss names the valid span. A
  held backtest can never coexist with a held Monte Carlo
  result — each slow-run
  transition re-anchors and drops the other — and its metrics join the
  9.19 PDF report. The run is serial by design: a full backtest is
  ≈ 1 s of deterministic passes, under the 9.15 pool threshold.
  Provenance: `BacktestResult` carries the series it replayed —
  `run_windows` accepts any series, so the §4.6 manifest must name the
  actual input — while the region data version deliberately excludes
  the series file: that string doubles as the saved-plan
  `assumptions_resolved_against` fingerprint, and the series prices no
  assumption and no base projection, so a history-only refresh must
  not flag every saved plan's defaults as changed (guard-tested). The
  generator takes the `verified_on` date as an explicit argument — an
  unreviewed regeneration can never stamp itself verified — the
  package metadata declares `MIT AND CC-BY-NC-SA-4.0` with the data
  licence scoped in `LICENSE-DATA`, and a guard test sweeps every
  figure string in the series file out of source code.*
- [x] 9.19 Exports and reports (#104) — *get the plan out of the app:
  File → "Export cash flow (CSV)" serialises the active run's report
  model exactly (header block: plan, run, scenario, money basis,
  disclaimer; then one row per person per period with every amount as
  the report's exact decimal, pinned by a round-trip test), and
  File → "Export report (PDF)" prints inputs with their
  facts/assumptions/decisions provenance, the three charts, Monte
  Carlo metrics when held, and the scenario comparison when scenarios
  exist. Generation is app-layer (§4.7) — the shell contributes the
  dialogs, the chart raster, and the QPdfWriter device (no new
  runtime dependency). Every export carries the §1 disclaimer.*

- [x] 9.20 Allocation transparency and per-wrapper allocation entry —
  *the modelled asset mix must never be a silent assumption (a user
  holding 100% equity was being projected on the default 80/20→40/60
  glide path with nothing on screen saying so). Two parts. **Facts
  form:** each savings wrapper gains an optional "Equity allocation,
  %" field — one number, the remainder in bonds, blank follows the
  glide path — parsed to the engine's existing `Wrapper.allocation`
  (which already persisted; only the form refused it). Cash accounts
  stay pinned all-cash and reject an entry rather than ignore it;
  `form_cannot_represent` now refuses only cash-bearing allocations
  (hand-edited files), not equity/bond splits. **Charts:** an
  "Invested as" line on the charts tab (and the 9.19 PDF report)
  states what each wrapper actually ran — a stated split as
  "100% equity (stated)", pinned cash, or the glide path summarised
  with its provenance ("80% equity de-risking to 40% over the 15
  years before retirement (shipped default)" vs "(your override)") —
  so every projection surface names its allocation. Percent copy
  trims trailing zeros (`format_share`); a stated "62.5" round-trips
  through the form exactly.*
- [x] 9.21 Fund the annual-allowance charge (Scheme Pays / cash) — *the
  charge the #116 wiring reports is now deducted from modelled
  balances at period close, after fees and growth like the
  portfolio-income tax charge: Scheme Pays debits the pension wrapper
  when the mandatory conditions hold (charge over £2,000 — shipped as
  data — and that wrapper's own input over the standard AA,
  PTM056410/FA 2004 s237B), the cash route debits the bare taxable
  wrappers otherwise, and any unfunded remainder joins the person's
  shortfall so the roadmap-7.3 ruin metrics see a sustained breach —
  decision record in §5.2.*
- [x] 9.22 Release process — *SemVer 0.x tag-driven GitHub Releases
  with a curated `CHANGELOG.md`, `make bump` for the version, and a
  validating `release.yml`; tag-only for now (no built artifacts) —
  decision record in §4.10.*
- [x] 9.23 Overlay-line tooltips (#145) — *hovering any chart overlay
  line — a backtest trajectory or the fan chart's median (9.24) —
  pops the same app-layer tooltip copy the bar segments carry (§4.7):
  the line's label with the period category and the exact `Decimal`
  amount, never the float plot coordinate. Line hovers arrive as
  plot-space points rather than category indices, so the shell snaps
  to the nearest whole x; a point off the categories hides rather
  than misreports, and leaving the line dismisses the tooltip. The
  fan chart's interval fills answer hover with their range copy —
  label, category, and the low-to-high amounts.*
- [x] 9.24 Monte Carlo fan chart tab (#146) — *the Monte Carlo
  presentation moves off the balances chart onto its own "Monte
  Carlo" sub-tab, so neither surface crowds the other (the balances
  chart was stacking per-wrapper bars under three percentile lines,
  plus trajectories when a backtest was held). The tab draws a
  probability fan: nested inter-percentile fills — 5th-95th,
  15th-85th, 25th-75th, 35th-65th — in the theme's single brand-green
  hue at stepped alphas (depth by lightness alone keeps the fan
  colour-vision-safe), the overlap deepening toward a median line in
  the darkest band ink, so central probability mass reads as colour
  depth. Each fill is a genuine interval statement ("90% of paths
  closed inside this region"), not a cosmetic gradient.
  `MonteCarloResult.balance_percentile` already interpolates any
  percentile, so the core is untouched; the fills deflate to today's
  money by the deterministic report's own deflators and follow the
  9.13 alignment guard — a held result whose period count differs
  from the projection's draws no fan tab rather than a fan against
  the wrong periods. The balances chart keeps the backtest
  trajectories (each a genuine historical outcome, suited to the
  deterministic context) and drops the Monte Carlo overlays; the
  ending-pot 10/50/90 metrics stay on the card, and the fan tab joins
  the 9.19 PDF report like every chart.*
- [x] 9.25 "How much can I draw down?" card (#149) — *the drawdown
  dual of the 9.14 card: `sustainable_income_at_age` fixes the
  retirement-age decision at a chosen age and searches the spending
  level through the 7.3 `sustainable_income` scan-plus-bisection,
  which gained a deterministic basis (one run per probe, judged by
  the §5.2 ruin signal) so the card answers under either run mode
  like its 9.14 sibling. The same exposure gate applies: an age with
  no retired period inside the horizon answers nothing rather than
  succeeding vacuously. Surfaced per §4.7 as a card beside "When can
  I retire?": a retirement-age input defaulting to the plan's stated
  decision, a success target under the Monte Carlo basis, an explicit
  find action off the GUI thread with the shared in-flight guard and
  staleness discard, and an answer naming the income (today's money,
  the highest net annual level the plan sustains), the age assumed,
  the £1,000,000 search ceiling, and the basis. The detail also
  restates the income as a starting withdrawal rate of the
  household's total wrapper balances recorded on the answer at solve
  time — derived, never asserted: the product ships no "safe
  withdrawal rate" figure, the line only lets users compare the
  computed answer against rules of thumb they know. A Monte Carlo
  search is bounded by the same 20,000 path-projection budget as
  9.14, multiplied over the search's probe bound rather than
  candidate ages. Answers reproduce from the recorded inputs (and
  seed) per §4.6.*
- [x] 9.26 Chart data as tables (#156) — *every chart sub-tab pairs
  the drawn chart with its numbers: a Chart | Table page pair inside
  the sub-tab, the table one row per period with one column per
  stacked series, fan fill, and overlay line, in the order the
  chart's legend reads. Cells are app-layer copy (§4.7) —
  money-formatted from the same exact `Decimal` amounts the chart
  draws, a fan fill cell stating its low-to-high interval like the
  fill tooltip — so the table and the chart can never disagree, and
  the table follows the money-basis toggle like the chart it mirrors.
  The page choice survives a refresh alongside the selected sub-tab.*
- [x] 9.27 Retirement outlook card (#163) — *a held Monte Carlo run
  summarised as plain sentences on a read-only card above the solver
  cards: the likely pot range at retirement in today's money (the
  middle half of paths, 25th-75th percentiles) with the 1-in-20 tails
  stated rather than hidden (5th/95th), the pension slice an annuity
  could be bought with, what a whole-pot purchase would deliver under
  the engine's own 5.5 conventions — the tax-free cash paid out first
  (the region's fraction over the reading snapshot's uncrystallised
  share, capped at the lump-sum-allowance headroom the snapshot's
  `lsa_used` implies) with the remainder buying income at the shipped
  level single-life rates (base rate × age multiplier, never
  extrapolated past the table) — and the State Pension forecast
  stacked on top with the combined total, from a start age quoted at
  the timetable's month-level precision ("age 66 and 9 months") and
  at the rate the run opened with (a stale forecast quotes its §4.8
  roll-forward disclosure, so the card and the projection uprate the
  same way). Pots are read at the tax-year end immediately
  before the retirement age is attained — the last close withdrawals
  cannot have touched — from a new pension/household split each
  `PathOutcome` retains (each wrapper result now carries the region's
  pension marker), deflated by the base projection's own CPI path
  (one inflation truth, §5.2). Purely a view over held results — no
  run of its own, so it can never disagree with the fan chart — with
  the fan's staleness rule: a run misaligned with the projection
  reads as no run. Each sentence appears only when true: the pension
  slice only beside other savings, the annuity only with pension
  money and a covered purchase age, the State Pension only with an
  official forecast; a target age already attained on the run date
  anchors at "the end of this tax year". The basis sentence names the
  run's paths and seed per §4.6.*
- [x] 9.28 Wrapper naming — *each savings wrapper takes an optional
  user label ("Aviva SIPP"): pure display copy on the core `Wrapper`
  (keyword-only, so its arrival shifts no positional caller), entered
  through a new first field on the facts form (blank means unnamed;
  a name repeated across wrappers is rejected at entry) and preferred
  by every naming surface — the inspector and scenario manager
  (`entity_names`), the balances-chart legend, the allocation note,
  and the cash-flow export's balance columns
  (`wrapper_display_labels`). Unnamed wrappers keep deriving their
  names from the kind. Final names are always unique: every surface
  numbers repeats in first-seen order (`labels.numbered_unique`), so
  one named and one unnamed ISA read "Aviva ISA" and "ISA", a
  wrapper named "ISA" beside an unnamed ISA reads "ISA 1" and
  "ISA 2", and duplicate names in a hand-edited plan file are
  numbered apart rather than colliding in legends and CSV headings.
  Persistence schema v5: every wrapper carries a `label` key, `null`
  for unnamed; the v4→v5 migration adds it on load.*
- [x] 9.29 Couples: per-person engine state extraction — *pure refactor,
  no behaviour change: the per-person mutable state of `_Projection`
  (wrapper ledgers, income ladders, relief, AA carry-forward/charge,
  LSA/MPAA ledgers, DB/state-pension/annuity streams) moves into a
  `_PersonProjection`; `_Projection` keeps plan, region, config,
  return model, shared CPI/nominal factors and a one-element person
  list; `run()` still validates one person. Golden scenario and full
  suite unchanged — this is the low-risk half of the §4.11 engine
  work, isolated so 9.30's diff is reviewable.*
- [x] 9.30 Couples: two-person engine activation — *`run()` accepts two
  persons (`validate_household_v1` retired from the engine; the form
  keeps its gate until 9.31). Pooled decumulation per §4.11:
  `WithdrawalSource` gains `person_id`, per-person tax-free-cash
  headroom, greedy marginal-cost draws within tax-bearing treatment
  groups via the existing incremental-tax pricing; aggregate-pot
  strategies read the household pot; household spending and planned
  outflows funded from the pooled step; horizon at the latest
  planning-age date; per-person AA/MPAA/LSA, DB, state pension and
  contribution machinery runs per person unchanged. Monte Carlo,
  reporting, comparison and exports flow through (already
  household-generic).*
- [x] 9.31 Couples: partner in the facts form — *optional second person
  per §4.11: form data `persons: tuple` (1–2) of per-person section
  values, owner key on every repeatable row, "About you"/"About your
  partner" copy, explicit add/remove-partner actions (removal
  confirms and deletes the partner's rows); `parse_facts_form` and
  `form_cannot_represent` dropped their extra-person refusals
  (`validate_household_v1` retired — the schema's 1..2 bound is the
  only person-count rule) and two-person plan files open instead of
  being refused, entity ids preserved per person; `entity_names`
  distinguishes the persons ("You"/"Your partner", owned entities
  "Your …"/"Partner's …"); the outlook card reads the household's
  pots at the later retirement date with per-person annuity slices
  and State Pensions, the retirement/drawdown cards gained a
  whose-age selector — the solvers vary one selected person's age
  with the partner's decision held fixed, the replacement-rate target
  measured against household employment income — and chart categories
  label both ages (`2032 · 60/58`). A partnerless form renders and
  parses exactly as before.*
- [x] 9.32 Couples: marriage allowance (#173) — *the §4.11
  household-level claim Decision (default claimed-when-eligible,
  `Household.claim_marriage_allowance`, plan document schema v6):
  per-tax-year eligibility check (transferor below PA; recipient ≤
  basic rate rUK / ≤ intermediate Scotland, read off the assessment's
  own band lines), automatic direction, applied as the ITA 2007 s55B
  tax reducer (rUK basic-rate % of the transferable amount, capped at
  the recipient's pre-AA-charge liability, never refundable) — a
  negative no-income `TaxLine` on the recipient's final assessment;
  `[marriage_allowance]` keys (£1,260 + per-schedule recipient band
  gates, data schema v3) in the tax-year files; `TaxSystem.assess`
  stays per-person — the protocol gains `adjust_household`, called by
  the engine's step-5½ between two-person assessments and close. The
  reducer is flat and capped so the gross-up/income-offset marginal
  pricing is untouched; the reduction lands in the reported
  assessment, not the period's cash flows (recorded simplification).
  The transferor side is modelled per s55B(6) (#190): the donor is
  re-assessed with their PA reduced by the transferable amount, so a
  donor with income inside the transferable band bears their cost in
  the reported household tax. The claim enters via the
  partner form section and is not scenario-addressable (the household
  has no EntityId) — both recorded limitations.*
- [x] 9.33 Couples: survivor modelling (#174) — *optional per-person
  `death_age: Decision[int]` per §4.11 (scenario-overridable even over
  an unset base — the "what if I die at 75" headline; death takes
  effect at the first period whose start attains the age, the §4.1
  gate convention; plan document schema v7): a household death step
  runs before each period's steps and moves the deceased's holdings
  onto the survivor's ledgers — DC pots as fully-crystallised
  beneficiary drawdown (income-tax-free below age-75 deaths, survivor
  marginal rate at/after; no NMPA gate, no new tax-free cash, no LSA
  consumption, no MPAA trigger; the boundary ships in
  `age_rules.toml` `[death_benefits]`, data schema v4, behind a new
  `WrapperRuleset.death_benefits_income_tax_free`), ISA/LISA as
  ungated ISA money (APS), GIA/cash as they stand; DB streams
  continue at the new per-scheme `survivor_fraction` fact defaulting
  to the `db.survivor_fraction` assumption (50%), accrual and
  commutation lump sum ended; the deceased's state pension and
  annuity streams stop (nothing inherited — §6; joint-life
  continuation is 9.34); marriage allowance lapses from the
  death-effect period (the tax year after death); spending scales by
  `spending.survivor_multiplier` (0.70, pinned against the PLSA
  single-vs-couple ratios 0.62/0.72/0.72 — §9 open question 9
  resolved). Both assumption keys land in §7 + the defaults file
  (doc-sync); the facts form enters the death age and per-scheme
  survivor fraction. No-death plans are bit-identical (goldens
  unchanged); death is deterministic across Monte Carlo paths; with
  no survivor the estate stays invested with no further modelled
  flows (§4.11 shipped conventions).*
- [x] 9.34 Couples: joint-life annuities end-to-end (#175) — *the
  purchase gains a survivor-fraction decision (50/66/100%, §6),
  validated against the basis (joint-life requires one, single-life
  forbids it); on the buyer's death (9.33) the stream passes to the
  survivor at that fraction, escalating as before; the single joint
  pricing factor (0.92) applies whatever the fraction — a labelled v1
  limitation of the §7 age-adjustment table; the facts form's
  survivor-income choice implies the basis, so
  `form_cannot_represent` drops its last couples refusal — while a
  joint-life purchase with no partner is refused on entry and on
  open (the §5 convention records that the engine still prices the
  joint factor with no one to pay); the survivor fraction joins the
  scenario whitelist (synthesized over a single-life base like
  `death_age`; a basis flip to single entails dropping it), and the
  scenario editor pairs the two overrides so the joint-life what-if
  is reachable one edit at a time; schema v8 adds `survivor_fraction`
  to every annuity purchase — `null` for single-life, the 50% the
  joint factor was always quoting for a v7 joint-life purchase.*

### Phase 10 — Usability (design record §4.12)

- [x] 10.1 Facts form: progressive disclosure — *rarely needed fields
  (`FieldSpec.advanced`: MPAA/LSA/death age, spending stage
  multipliers, protected payment and deferral, crystallised balance
  and escalation, the DB scheme minutiae beyond the four core facts,
  the survivor-income and marriage-allowance choices) render behind a
  per-section "More options" toggle; a populated or errored advanced
  field is always revealed, no field is both required and advanced
  (guard test), and clearing a section tucks the disclosure away.*
- [x] 10.2 Facts form: required markers and inline errors — *the
  previously unrendered `FieldSpec.required` flags render as `*` on
  labels (legend in the intro copy); submissions return a
  `FactsSubmissionOutcome` whose structured `FormError` list renders
  inline under each addressed field — repeatable rows routed by
  index, section-wide errors on a section label — with the first
  error scrolled into view and focused; the status line keeps the
  full formatted list.*
- [x] 10.3 Retirement income: preference dropdown and withdrawal
  strategy — *a household Retirement income section with the
  drawdown-vs-annuity preference (a disclosure control — the annuity
  purchase sections render directly beneath it, only while it says
  annuity or rows exist;
  switching back confirms and deletes the rows; the stored preference
  is the purchases themselves; the pot share enters as a percent,
  `percent_of_pot`, stored as the domain fraction) and the
  withdrawal-strategy choice
  surfacing the §2 strategy set: `Household.withdrawal_strategy`
  (`Decision[WithdrawalRule]`, schema v9, `null` = fixed real), the
  fixed-percentage rate entered as a percent of the pot; wired
  through `plan_run_config` into the base run, scenario runs, Monte
  Carlo, and the backtest; shown in the inspector's choices; not
  scenario-addressable and the retirement/drawdown cards stay
  fixed-real (both recorded in §4.12).*
- [x] 10.4 Guidance: field tooltips and the pre-Monte-Carlo outlook —
  *every hinted field's guidance doubles as its tooltip so it
  survives typing; the outlook card falls back to a single-path
  deterministic summary (same reading/deflator/annuity/State Pension
  machinery, `DETERMINISTIC_BASIS_SENTENCE` naming what Monte Carlo
  would add) whenever a base projection is held without an aligned
  Monte Carlo run, so the card is populated from first launch.*

## 9. Open questions

Carried from the 2026-08-01 research pass:

1. **FCA COBS inflation figure** — a handbook mirror showed 2.00% in COBS
   13 Annex 2 2.5R (long-standing value 2.5%); canonical page blocked
   extraction. Re-verify before citing COBS for inflation (the 2/5/8 and
   1.5/4.5/7.5 return maxima were confirmed twice).
2. **NMPA enacting statute** — 2028 date + protections confirmed on the
   gov.uk policy paper; the statute (likely Finance Act 2022) not confirmed
   on a fetched primary page.
3. **AA carry-forward mechanics** — *resolved 2026-08-04*: unused
   allowance is drawn in order of earliest to most recent year; only tax
   years with membership of a registered pension scheme (or qualifying
   overseas scheme) generate carry-forward; partial use is allowed, the
   rest staying available within the 3-year window; unused MPAA never
   carries forward — only unused *alternative* AA does, and only in a
   year whose money-purchase inputs exceed the MPAA; within the MPAA
   the normal AA basis applies despite the trigger
   ([PTM056510](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm056510)).
   Verified on the
   [gov.uk guidance](https://www.gov.uk/guidance/check-if-you-have-unused-annual-allowances-on-your-pension-savings);
   shipped as `pension.aa_carry_forward_years` plus the 9.5 machinery
   (§6).
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
9. **Survivor spending multiplier basis** (from the 2026-08-11 couples
   spike) — *resolved 2026-08-12* (9.33): the current PLSA/Pensions UK
   Retirement Living Standards budgets give single-vs-couple ratios of
   £13,900/£22,500 = 0.62 (Minimum), £32,700/£45,400 = 0.72
   (Moderate) and £45,400/£62,700 = 0.72 (Comfortable)
   ([retirementlivingstandards.org.uk](https://www.retirementlivingstandards.org.uk/)),
   so the 0.70 default stands, sitting just under the
   Moderate/Comfortable ratio; shipped as the
   `spending.survivor_multiplier` key (§7 "Couples"). The per-standard
   ratios remain relevant to the PLSA benchmarks suggestion in #165.
