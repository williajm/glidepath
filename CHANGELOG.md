# Changelog

All notable changes to glidepath are documented in this file, most
recent release first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[SemVer](https://semver.org/spec/v2.0.0.html) 0.x (see the release
process in `docs/planning.md` §4.10). Each release's section is curated
in the release PR and becomes the GitHub Release notes verbatim.

## [Unreleased]

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
