# Changelog

All notable changes to glidepath are documented in this file, most
recent release first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[SemVer](https://semver.org/spec/v2.0.0.html) (see the release
process in `docs/planning.md` §4.10). Each release's section is curated
in the release PR and becomes the GitHub Release notes verbatim.

## [Unreleased]

### Added

- `docs/getting-started.md`: a step-by-step user guide — installing
  on a machine with nothing on it, what to gather before you start,
  entering it on the Facts tab, reading the Charts tab and its cards,
  overriding assumptions (growth rate, inflation, fees, planning
  horizon, glide path) on the Stated vs assumed tab, scenarios, and
  saving/exporting.

### Changed

- README: the Run section is now a numbered Install walkthrough that
  starts from a machine without uv, and a User guide section links the
  new guide; the site's Get started section gains the uv install line
  and the same link.

## [1.0.1] - 2026-08-26

### Fixed

- Package metadata: the PyPI `Development Status` classifier still
  said `4 - Beta` on the 1.0.0 release; it now reads
  `5 - Production/Stable`, matching the 1.0 stability declaration
  (uploaded file metadata is immutable on PyPI, so the fix needs a
  new release to show). No code changes.

## [1.0.0] - 2026-08-26

First stable release. Functionally this is the 0.6.0 product — roadmap
Phases 1–10 complete — with every dependency refreshed under the
7-day supply-chain cooldown; the version number declares the product
stable enough for outside users (planning §4.10 had deferred 1.0.0
until then) and changes no behaviour. Plan files saved by 0.6.0
(document schema v9) open unchanged. glidepath remains an application,
not a library: nothing under `glidepath.*` is a public API, and 1.0
makes no import-level stability promise.

### Changed

- Version 1.0.0 (planning §4.10): minor releases keep carrying
  features and behaviour changes (plan-file schema steps riding the
  migration harness) and patch releases fixes only, as before.

- Dependencies refreshed under the 7-day cooldown (resolution cutoff
  2026-08-19): the exact PySide6 runtime pin moves 6.11.1 → 6.11.2
  (with shiboken6 and the essentials/addons wheels); development
  tooling — ruff 0.16.1 → 0.16.3, mypy 2.3.0 → 2.3.1, hypothesis
  6.165.2 → 6.165.10, pre-commit 4.6.1 → 4.6.2, cyclonedx-python-lib
  11.11.0 → 11.12.0, virtualenv 21.7.1 → 21.7.4 — plus transitive
  updates (ast-serialize, charset-normalizer, filelock, idna, librt,
  platformdirs, pygments, python-discovery).

## [0.6.0] - 2026-08-14

Facts-form usability release (roadmap Phase 10): retirement income
becomes a choice — a drawdown-vs-annuity preference with the annuity
purchase sections disclosed beneath it, the pot share entered as a
percentage, and a withdrawal-strategy dropdown driving every run mode —
alongside per-section "More options" disclosure with inline submission
errors, a deterministic retirement outlook shown before Monte Carlo is
run, and supply-chain visibility additions (OpenSSF Scorecard workflow,
the Sigstore provenance bundle attached to GitHub Releases).

### Added

- Retirement income choices on the Facts tab (roadmap 10.3): a
  drawdown-vs-annuity preference dropdown that reveals the annuity
  purchase sections — directly beneath it — only when they apply
  (switching back confirms before deleting the purchase rows), with
  the share of pot entered as a percentage (100 annuitises the whole
  pot) rather than a 0–1 fraction, and a withdrawal-strategy
  choice surfacing the four shipped strategies — fixed real spending
  (the default), a fixed percentage of the pot (with its rate),
  guardrails, and natural yield. The choice is stored on the plan
  (document schema v9), shown under "Your choices in effect", and
  drives the projection, scenario runs, Monte Carlo, and the
  backtest; the "When can I retire?" and "How much can I draw down?"
  cards deliberately keep answering in fixed-real terms.

- Facts form usability (roadmap 10.1/10.2): rarely needed fields now
  sit behind a per-section "More options" disclosure (anything
  already filled in, or in error, is revealed automatically);
  required fields are marked `*`; submission errors render inline
  under the fields they address, with the first scrolled into view;
  and every field's hint doubles as its tooltip so guidance survives
  typing (10.4).

- The retirement outlook card now shows a single-path deterministic
  summary as soon as a plan is projected — pots at retirement, the
  annuity income they could buy, and State Pension stacking — with a
  note inviting the Monte Carlo run that adds the likely range;
  previously the card was empty until Monte Carlo had been run
  (roadmap 10.4).

- OpenSSF Scorecard workflow (`scorecard.yml`): weekly and on-push
  automated scoring of the repo's supply-chain security posture,
  published to scorecard.dev and surfaced in the code-scanning tab,
  with a Scorecard badge in the README.

- GitHub Releases now attach the signed Sigstore provenance bundle
  (`glidepath-X.Y.Z-provenance.intoto.jsonl`) alongside the sdist and
  wheel — the same provenance already held by GitHub's attestation
  store and PyPI, made visible on the release page, verifiable
  offline (`gh attestation verify --bundle`), and recognised by the
  OpenSSF Scorecard Signed-Releases check.

### Changed

- README badges: added the PyPI version badge and replaced the
  hand-maintained Python version badge with the dynamic
  `pypi/pyversions` badge driven by the published package metadata.

## [0.5.0] - 2026-08-13

Correctness and hardening release: three UK tax fixes (the DB
pension's commencement-year annual-allowance accrual, the net-pay
income offset, and the marriage allowance transferor's cost), a
savings-rate schedule in the tax-year data files so the April 2027
savings rates can ship as data, the drawdown answer restated as a
starting withdrawal rate, and a wider test net — broader
property-based coverage and real GUI input simulation via pytest-qt.
First release whose GitHub Release artifacts carry signed build
provenance.

### Added

- Tax-year data files now carry a savings rate schedule
  (`savings.rates`, data schema v5), aligned positionally with the
  rUK bands and consumed by the savings-income layer, so the separate
  22% / 42% / 47% savings rates enacted from 6 April 2027 (Budget
  2025 OOTLAR) can ship as data in the 2027/28 file instead of being
  impossible to represent. The 2026/27 file states the current
  20/40/45 rates explicitly; assessments are unchanged (#189).

- Release artifacts attached to a GitHub Release now carry signed
  build provenance (`actions/attest-build-provenance`) — the
  GitHub-side mirror of the PEP 740 attestations PyPI already holds —
  so a file downloaded from the release page verifies with
  `gh attestation verify <file> -R williajm/glidepath`.

- Property-based testing widened beyond single-person, unique-kind
  households (#201): a second Hypothesis composite generates
  two-person households exercising the pooled withdrawal step —
  repeated wrapper kinds, mixed rUK/Scottish residencies, and
  optional DB pensions, state pension records, and annuity
  purchases — with the engine invariants (non-negative balances,
  wrapper ledger identity, retirement cash conservation, tax lines
  summing to the tax due) asserted at household level. Alongside it,
  targeted properties over the UK tax assessment (lines and band
  widths accounting exactly for the assessment, monotonicity in
  income through the taper's roundings, relief at source never
  increasing tax, annual-allowance charge top-slicing) and the
  withdrawal machinery (tax-aware ordering's group and stability
  invariants, fixed-percent draw conservation, guardrails
  one-step-adjustment bound).

- The GUI suite now simulates real user input through pytest-qt
  (#202): the offscreen QApplication comes from the plugin's `qapp`
  fixture, and a first slice of tests drives the shell with synthetic
  mouse and keyboard events (`QTest.mouseClick`/`keyClick`) and menu
  `QAction` triggers, pinning the signal connections themselves —
  scenario override removal, comparison basis and metric selection,
  the export actions, the backtest year picker's unchanged-text skip,
  the slow-run handlers' shared in-flight guard, and the report
  export's replace-failure path.

### Changed

- The "How much can I draw down?" card's detail now also restates the
  sustainable income as a starting withdrawal rate of the household's
  total wrapper balances (recorded on the answer at solve time), so
  the computed answer can be compared against rule-of-thumb rates
  like the 4% rule. The rate is derived from the user's own plan —
  glidepath still ships no "safe withdrawal rate" figure.

- The "When can I retire?" card now says its target is enforced after
  tax ("Target income £X a year after tax — N% of gross employment
  income £Y"), and the help guide explains that this is deliberately
  more demanding than the same share of take-home pay (#187).
- The help guide gains a "How spending is funded" section describing
  the fixed withdrawal order (general accounts and cash, then
  ISAs/LISAs, then crystallised and finally uncrystallised pension
  funds) as a deliberate, non-configurable simplification; the
  planning document no longer calls the ordering "configurable"
  (#192).
- The planning document's §4.1 now records retirement as a
  whole-period gate rather than claiming mid-year retirements are
  pro-rated, including the non-conservative direction of the delay
  for post-6-April birthdays (#186).

### Fixed

- The Annual Allowance no longer discards a DB pension's entire
  commencement-year accrual: per HMRC's closing-value adjustment
  (PTM054500), a stream whose benefits start mid-year now reports a
  pension input amount with its closing entitlement revalued to the
  commencement date, so the final year of accrual counts against the
  allowance instead of vanishing. Streams already in payment at the
  year's start still generate nothing. The add-back values the
  uncommuted, unadjusted entitlement — the early-retirement reduction
  nuance is recorded as a simplification in the planning document
  (#188).
- Working alongside retirement income with net-pay pension
  contributions no longer overstates spendable income: the income
  offset's employment-only tax baseline is now assessed on net-pay-
  reduced pay, matching the full assessment, so the contribution's
  tax relief stays in out-of-model take-home pay instead of leaking
  into in-model cash (#191).
- The marriage allowance now models the transferor's side of the
  election (ITA 2007 s55B(6)): the donor is re-assessed with their
  personal allowance reduced by the transferable amount, so a donor
  with income inside the transferable band bears their real cost and
  the reported household benefit is no longer up to £38/yr high
  (#190).

## [0.4.0] - 2026-08-12

Couples release: a plan gains an optional partner, modelled end to
end — a pooled two-person household with per-person tax, the marriage
allowance, deterministic survivor modelling, and joint-life annuities
— plus wrapper naming and a retirement outlook card.

### Added

- Couples get joint-life annuities end-to-end (roadmap 9.34, planning
  §4.11): an annuity purchase can now name a survivor income of 50%,
  66%, or 100% — the verified UK option structure — via a new
  survivor-income choice on the annuity form section, which also
  implies the purchase's basis (blank stays single-life). On the
  buyer's death the joint-life stream continues to the surviving
  partner at the purchased fraction, escalating as before;
  single-life purchases are unchanged. Pricing keeps the single
  joint-life factor (0.92) whatever the fraction — a labelled v1
  limitation of the annuity age-adjustment table. A joint-life
  purchase needs a partner in the plan (the form refuses one
  otherwise), and the scenario editor pairs the basis and
  survivor-income overrides so "what if joint-life" works one edit
  at a time. The facts form thereby represents every couples plan
  feature; plan documents step to schema v8 (older files load as
  before).

- Couples get deterministic survivor modelling (roadmap 9.33, planning
  §4.11): each person can state an optional "model death at age" —
  a choice, so "what if I die at 75" also works as an ordinary
  scenario override, even when the base plan sets no death age. From
  the death gate (the §4.1 period convention) the survivor inherits
  the household: pension pots merge as beneficiary drawdown —
  income-tax-free when death precedes 75, taxed at the survivor's
  marginal rate from 75, never gated, never consuming the survivor's
  lump sum allowance or triggering their MPAA (the age boundary ships
  in the UK data files) — ISAs and LISAs pass as the survivor's own
  ISA money (the additional permitted subscription), GIA/cash pass at
  the spouse exemption, and DB pensions continue at each scheme's
  survivor fraction (a new per-scheme fact in the DB form section,
  defaulting to the new `db.survivor_fraction` assumption of 50%).
  The deceased's state pension and single-life annuity income stop,
  the marriage allowance lapses from the tax year after death, and
  household spending scales by the new `spending.survivor_multiplier`
  assumption (0.70, pinned against the current PLSA single-vs-couple
  retirement budgets). Plan documents step to schema v7 and UK data
  files to schema v4 (older files migrate/load as before); plans with
  no death age are unchanged to the byte, and death ages are
  deterministic across Monte Carlo paths.

- Couples get the marriage allowance (roadmap 9.32, planning §4.11):
  each tax year the engine checks eligibility — one partner's income
  inside their personal allowance, the other liable at no more than
  the basic rate (rUK) or intermediate rate (Scotland) — picks the
  direction automatically, and reduces the recipient's assessed tax
  by the ITA 2007 s55B reducer (20% of the £1,260 transferable
  amount, up to £252 a year, capped at their liability). The claim
  is a household decision entered in the partner form section,
  defaulting to "claim when eligible"; the figures ship in the
  tax-year data files, and synthesized future years re-derive the
  transferable amount from that year's personal allowance (10%,
  rounded up to the nearest £10 per HMRC PAYE100060). Plan documents step to schema v6 (older
  files migrate on load); the reduction shows in the reported tax
  breakdown as its own "marriage allowance" line and does not alter
  withdrawal sizing — a recorded simplification.

- The facts form gains an optional partner (roadmap 9.31, planning
  §4.11): one explicit "Add a partner" action reveals an "About your
  partner" section plus the partner's own state pension, wrappers, DB
  pensions, and annuity purchases; removing the partner confirms
  first, then deletes their entries. Two-person plan files now open
  in the form instead of being refused, with every row keeping its
  stable entity id for both persons. The inspector, scenario manager,
  and cash-flow export name the persons "You" / "Your partner" (owned
  entities read "Your …" / "Partner's …"), chart categories label
  both ages ("2032 · 60/58"), the retirement outlook card reads the
  household's pots at the later retirement date with each person's
  annuity slice and State Pension spelled out, and the "When can I
  retire?" and "How much can I draw down?" cards gain a whose-age
  selector — the search varies one person's retirement age with the
  partner's held fixed, measured against household employment income.
  A plan without a partner renders, parses, and projects exactly as
  before.

- The projection engine models two-person households (roadmap 9.30,
  planning §4.11): one pooled withdrawal step per period funds the
  household's spending and planned outflows from both persons'
  wrappers, draining tax-bearing sources greedily by marginal cost so
  both personal allowances and both basic-rate bands fill naturally;
  tax-free cash headroom is tracked per person (the lump sum
  allowance is an individual cap), aggregate-pot strategies (fixed-%,
  guardrails) read the household pot, the run's horizon ends at the
  latest planning-age date, and mixed rUK/Scotland residency assesses
  each person under their own schedule. Household spending begins once
  every person has retired; a retired partner's income meanwhile
  banks like any pre-decumulation income. Single-person plans are
  unchanged (bit-identical results).

- Wrappers can carry your own name for the account ("Aviva SIPP",
  roadmap 9.28): a new optional Name field on each savings wrapper,
  shown everywhere the wrapper is named — the inspector, the scenario
  manager, the balances chart legend and allocation note, and the
  cash-flow export's column headings. Blank keeps the kind-derived
  name; a name repeated across wrappers is rejected at entry, and
  display names that still collide (say a wrapper named "ISA" beside
  an unnamed ISA) are numbered apart. Plan files gain schema version
  5 (every wrapper carries a `label` key); older files load unchanged
  with unnamed wrappers.

- Retirement outlook card on the charts screen (roadmap 9.27): a held
  Monte Carlo run summarised in plain sentences — the likely pot range
  at retirement in today's money with the 1-in-20 tails stated, the
  pension slice an annuity could be bought with, the tax-free cash and
  yearly income a whole-pot purchase would deliver under the engine's
  own conventions, and the State Pension forecast stacked on top with
  the combined total, from its month-precise start age at the run's
  rolled-forward rate.

- Monte Carlo path outcomes now retain a pension-only closing-balance
  series alongside the household one (`pension_balance_percentiles`),
  and each wrapper period result carries the region's pension marker.

## [0.3.0] - 2026-08-08

Hardening release: attested PyPI publishing, durable plan saves, and a
security policy — no modelling changes.

### Added

- `SECURITY.md`: a private vulnerability-reporting channel and the
  supply-chain measures in one place.
- Diagnostic logging when the settings file cannot be written (the
  disclaimer acknowledgement or the last-plan path): the session
  still continues unaffected, but "it forgot my plan" reports now
  leave a trail.

### Changed

- The PySide6 runtime dependency is pinned exactly (was `>=`): PyPI
  installs get the version the release was tested against, since
  `uv.lock` and the supply-chain cooldown never applied to end-user
  installs (planning §4.10).
- Release pipeline hardening (planning §4.10): the sdist/wheel are
  built and smoke-tested in an unprivileged job, published to PyPI
  with PEP 740 attestations via the PyPA publish action, and the
  GitHub Release is created only after PyPI publication succeeds,
  with the artifacts attached. Publishing now requires manual
  approval of the `pypi` environment, which only `v*` tags may
  deploy to.
- Plan saves now fsync the temporary file (and, on POSIX, the
  directory) before the atomic rename: the save is power-loss
  durable, not just crash-safe.

### Fixed

- The README renders correctly on PyPI (absolute image URLs), its
  release section no longer claims there are no packaged builds, and
  the licence badge states the real `MIT AND CC-BY-NC-SA-4.0`
  licensing.

## [0.2.1] - 2026-08-08

Metadata-only release: no code changes since 0.2.0.

### Added

- PyPI project metadata: trove classifiers (desktop finance app, Qt,
  Python 3.14), keywords, author, and sidebar URLs (homepage,
  repository, changelog, issue tracker). No `License ::` classifier —
  the SPDX license expression is the license metadata under PEP 639.

## [0.2.0] - 2026-08-08

First release published to PyPI. The headline modelling change is the
annual-allowance charge landing in the balance path (Scheme Pays and
cash routes); on the charts screen every chart gains a table twin, the
Monte Carlo fan moves to its own tab, and a "How much can I draw down?"
card answers the dual of "When can I retire?".

### Added

- Releases now publish the sdist and wheel to PyPI via trusted
  publishing (OIDC, no stored credential), so
  `uv tool install glidepath` / `pipx install glidepath` work from
  this release onward; the tag/changelog release process is unchanged
  (planning §4.10).
- Every chart sub-tab now pairs the graph with its numbers (#156): a
  Chart | Table page pair inside each sub-tab, the table one row per
  period with a money column per stacked series, fan band, and
  overlay line in the chart legend's order — the same exact amounts
  the chart draws, following the money-basis toggle. A fan band's
  cell states its low-to-high interval like its hover tooltip.
- "How much can I draw down?" card on the charts screen (#149): the
  dual of "When can I retire?" — choose a retirement age (the plan's
  stated decision by default) and the app answers with the highest
  net annual income, in today's money, the plan sustains from that
  age, on the screen's selected basis: deterministic (no unmet need
  in any period) or at least the chosen Monte Carlo success rate over
  the panel's seed and paths. The underlying sustainable-income
  search gained a deterministic basis to match.
- Monte Carlo fan chart on its own tab (#146): a held run now draws
  nested inter-percentile bands (5th-95th through 35th-65th) in a
  single hue deepening toward the median line, so the probability of
  each outcome region reads as colour depth — each band is a genuine
  interval statement ("90% of simulated paths closed inside this
  region"). The balances chart no longer carries the 10/50/90
  percentile lines, so neither surface crowds the other; the
  ending-pot metrics stay on the run-mode card, and the fan joins the
  PDF report like every chart.
- Hover tooltips on chart overlay lines (#145): backtest trajectories
  and the fan's median answer hover with the same exact-amount copy
  the bar segments already had, snapped to the nearest year; the fan's
  bands answer with their low-to-high range for the hovered year.
- The annual-allowance charge is now funded from modelled balances
  (#124): Scheme Pays debits the pension pot when the FA 2004 s237B
  mandatory conditions hold (charge over £2,000 — shipped as data —
  and that wrapper's own pension input over the standard allowance),
  the cash route debits the taxable accounts otherwise, and whatever
  no wrapper can fund joins the person's shortfall — so a sustained
  breach now degrades the balance path and the success metrics see
  it. The cash-flow CSV gains an "AA charge" column; decision record
  in planning §5.2.
- Keyboard shortcuts for the main window: Ctrl+O open, Ctrl+S save,
  Ctrl+Shift+S save-as, Ctrl+E / Ctrl+Shift+E for the CSV and PDF
  exports, F1 for the how-to-use guide, and a new File → Quit action
  on Ctrl+Q — standard keys where the platform defines one (#135).
- Unsaved-changes prompt on close: the session now tracks whether the
  plan has been edited since the last save or load, and closing the
  window with unsaved edits asks save / discard / cancel instead of
  silently discarding them (#136).
- Second end-to-end golden scenario: a mixed-income decumulator
  (crystallised + uncrystallised SIPP, taxable GIA, commuted DB
  pension, deferred state pension, staged annuity purchase, planned
  outflow, spending stages) pinning the flows the first golden never
  produces, with hand-checked anchors and whole-run ledger identities.
- Native Windows CI job running the full test suite (the app's primary
  desktop platform was previously only tested on Linux).
- Test-gap closures across the suite: multi-wrapper growth-tax
  apportionment, the sustainable-income bisection's raise-the-floor
  branch, PSA/starting-rate/state-pension-deferral boundary cases,
  construction-guard rejections (contribution caps, annuity rate
  tables, UK schema invariants), a genuine v1 fixture document loaded
  through the full migration chain, v2→v3 migration unit tests,
  scenario-override save/load round-trip, malformed/truncated document
  handling, export content assertions (Monte Carlo seed header,
  roll-forwards and retirement report sections), a README disclaimer
  sync test, and direct tests for the shared table view.
- Property-based tests over the engine: hypothesis-generated modest
  households projected through the real UK region, asserting that no
  closing balance goes negative, the wrapper ledger identity and the
  retirement cash-conservation identity reconcile every period, the
  per-band tax lines sum to the tax due, reordering a person's wrapper
  listing changes nothing, and parallel Monte Carlo reproduces the
  serial run (#132).
- Export artefact snapshots: a checked-in byte-for-byte golden of the
  cash-flow CSV (column order, quoting, and line terminators pinned),
  PDF text extraction asserting the exported report carries the
  disclaimer and its section headings, and a direct render test of the
  report chart rasteriser (#133).
- Tests for the operational scripts that gate merges and releases:
  the supply-chain dep-age check (fail-closed on malformed lockfiles,
  non-PyPI sources, late uploads, and network errors), the cooldown
  cutoff rewrite, the release version bump, the release-notes
  changelog extraction, and the JST returns-history derivation. The
  scripts now sit inside the pytest coverage gate (and the SonarQube
  coverage metric), with only the manual Monte Carlo performance
  harness left outside it (#130).

### Fixed

- The plan report's charts rendered at their default size in a corner
  of the embedded image instead of filling it: the rasteriser rendered
  a never-shown chart view whose resize Qt only delivers on show. It
  now lays the chart out at the report size directly, so exported PDFs
  carry full-size charts (#133).
- v1-era plan files carrying accumulation-stage spending multipliers
  (`early_accumulation`, `mid_accumulation`, `pre_retirement`) failed
  to load after the tokens were retired without a migration. A new
  v3→v4 schema migration drops the keys — they never scaled anything,
  spending being modelled only in retirement — and the checked-in v1
  golden fixture now carries the true v1 bytes (#129).

### Changed

- The Charts tab now gives the chart itself about two thirds of the
  window: the question cards sit in a scrollable pane above the
  chart, joined by a draggable splitter, and extra height from
  enlarging the window goes entirely to the chart. Previously the
  cards' fixed height squashed the chart into the remainder.
- The help guide's Facts section and the annuity-purchase form section
  now state the drawdown-by-default model explicitly: anything not
  annuitised stays invested in drawdown, a fraction of 1 annuitises
  the whole pot, and several purchases at different ages annuitise in
  stages — previously a reader had to infer this from the fraction
  field's hint.
- The launch example plan now holds together: retirement at 62 (was
  60), a £24,000 net spending need (was £28,000), and £4,800/year into
  the ISA (was £2,500). The old numbers left the deterministic
  projection in shortfall from age 88 and a seeded Monte Carlo run
  succeeding only 28% of the time — a first launch showed a plan
  already in ruin. The tuned persona meets every deterministic
  period's need and succeeds in roughly 71% of simulated paths, which
  still leaves honestly failing paths visible on the new fan chart.
- Coverage gate raised from 90% to 96%; statistical tolerances in the
  return-model tests tightened to catch mean/correlation regressions;
  the exception-test hygiene guard now covers `tests/gui/`.

## [0.1.0] - 2026-08-05

First tagged release, covering roadmap Phases 1–8 and the Phase 9
extensions shipped to date (`docs/planning.md` §8).

### Added

- Deterministic projection engine for a single UK person: workplace
  DC / SIPP / ISA / LISA / GIA / cash wrappers with contribution-relief
  mechanics, rUK and Scottish income tax from verified 2026/27 data
  files, dividend and savings taxation, pension allowances (annual
  allowance with taper, carry-forward and the MPAA; lump-sum
  allowance), DB pensions (deferred or active CARE-style accrual),
  state pension from the official DWP forecast including deferral,
  annuity purchases, fees, a de-risking glide path, and tax-aware
  decumulation with optional go-go/slow-go/no-go spending stages.
- Monte Carlo mode: seeded, reproducible paths with success rate,
  probability of ruin, ending-pot percentiles and 10/50/90 chart
  bands; parallel path execution; a "when can I retire?" solver for
  the earliest retirement age sustaining a target replacement rate.
- Historical backtesting over every rolling window of world market
  history since 1900 (JST Macrohistory-derived series, CC BY-NC-SA
  4.0), reporting the share of starting years the plan survives and
  the worst-case windows as real balance trajectories.
- PySide6 desktop app: facts entry, stated-vs-assumed provenance
  inspector with overridable assumptions, real/nominal projection
  charts, scenario manager with side-by-side diff, plan save/load as
  `.glidepath.json` with schema migration, CSV cash-flow export and
  PDF report, in-app help, and a launch example plan.
- Facts vs assumptions vs decisions provenance throughout; every UK
  policy figure ships as a verified TOML data file with sources and
  verification dates, never hardcoded.
- Supply-chain-hardened toolchain: 7-day dependency cooldown enforced
  in CI, locked resolution everywhere, SHA-pinned GitHub Actions, and
  a GitHub Pages landing site.
