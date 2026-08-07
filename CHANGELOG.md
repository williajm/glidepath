# Changelog

All notable changes to glidepath are documented in this file, most
recent release first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[SemVer](https://semver.org/spec/v2.0.0.html) 0.x (see the release
process in `docs/planning.md` §4.10). Each release's section is curated
in the release PR and becomes the GitHub Release notes verbatim.

## [Unreleased]

### Added

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
