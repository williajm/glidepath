<p align="center">
  <img src="src/glidepath/gui/assets/wordmark.png" alt="glidepath" width="420">
</p>

[![CI](https://github.com/williajm/glidepath/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/williajm/glidepath/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=williajm_glidepath&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=williajm_glidepath)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=williajm_glidepath&metric=coverage)](https://sonarcloud.io/summary/new_code?id=williajm_glidepath)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue)](https://github.com/williajm/glidepath/blob/main/.python-version)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/github/license/williajm/glidepath)](LICENSE)

A desktop retirement and investment planner. UK-first, built so other
regions can be added later. Every number in a plan is a **fact** you
stated, a **decision** you made, or an **assumption** the app defaulted
— always inspectable, never silently guessed. All data stays local;
nothing is transmitted.

What it models today (single person, UK):

- **Wrappers** — workplace DC, SIPP, S&S ISA, LISA, GIA and cash, with
  UK contribution relief mechanics and dividend/savings taxation.
- **Defined benefit pensions** — deferred entitlements or active
  CARE-style accrual, with revaluation, early/late factors and
  commutation; **state pension** from your official DWP forecast,
  including deferral.
- **Tax** — rUK and Scottish income tax from verified 2026/27 data
  files; pension allowances (AA/taper/MPAA, lump-sum allowance).
- **Projection** — deterministic or Monte Carlo runs from the app:
  success rate, probability of ruin, ending-pot percentiles, and
  10th/median/90th bands on the charts, reproducible from a seed. With
  a de-risking glide path, tax-aware decumulation, and annuity
  purchases entered in the facts form. (The engine also models
  alternative withdrawal strategies — fixed %, guardrails, natural
  yield — and tax-free-cash strategies; the app currently runs the
  fixed-real defaults, with no strategy picker in the UI yet.)
- **"When can I retire?"** — a solver for the earliest retirement age
  that sustains a target income (a replacement rate you choose, 66% of
  employment income by default), met deterministically or at a Monte
  Carlo success target.
- **Historical backtesting** — replays the plan over every rolling
  window of world market history since 1900 (global equities in
  sterling terms, UK gilts and cash, deflated by UK inflation):
  the share of historical starting years the plan survives, the worst
  starting year, and the range of outcomes as chart bands —
  sequence-of-returns risk that independent Monte Carlo draws miss.
- **Scenarios** — named what-ifs over your decisions and assumptions,
  with a side-by-side comparison; plans saved as a local JSON file.

## Disclaimer

Glidepath is a personal modelling tool for exploring retirement scenarios.
It is not financial advice and is not regulated; its outputs depend on
assumptions that will not match reality. Do not make financial decisions
based solely on this tool.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and GNU Make.

```sh
make sync          # creates the platform venv and installs locked dependencies
make hooks         # installs the pre-commit hooks (required)
```

One-time per machine: set `UV_PROJECT_ENVIRONMENT` user-wide (`.venv-win`
on Windows, `.venv-wsl` in WSL) so bare `uv` commands and the git hooks use
the same venv as make. See `CLAUDE.md`.

## Run

```sh
uv run glidepath
```

Launches the desktop app. Accept the disclaimer and a fresh install
opens with an example plan already projected, so every tab has
something to show — replace its values with your own facts, or clear
the form and start blank. Charts label each bar with the tax year and
your age and switch between today's money and nominal; Help → "How to
use glidepath" walks through every tab. Save your plan from the File
menu as a `.glidepath.json` file you own, stored wherever you choose;
the next launch reopens your last plan automatically.

## Everyday commands

```sh
make check   # all merge gates: ruff, format, mypy --strict, pytest (>=90% cov), dep age
make fix     # auto-fix lint issues and reformat
make test    # tests with coverage
make deps    # the ONLY way to add/upgrade dependencies (7-day cooldown lock)
make audit   # pip-audit the lockfile for known CVEs
```

Dependencies are never added with plain `uv add`/`uv lock`: run `make deps`
so the 7-day supply-chain cooldown is applied. CI runs every `make check`
gate plus `make audit` and the SonarCloud quality gate on each PR — so a
clean `make check` locally does not quite guarantee a green pipeline. See
`CLAUDE.md` for the full policies.

## Data licences

The code is MIT-licensed (see `LICENSE`). One data file is not: the
historical return series
(`src/glidepath/regions/uk/data/returns_history.toml`) is derived from
the [JST Macrohistory Database](https://www.macrohistory.net/database/)
(Jordà, Schularick & Taylor; return series per Jordà, Knoll, Kuvshinov,
Schularick & Taylor 2019) and is distributed under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) —
attribution required, non-commercial use only, share-alike. The package
metadata declares `MIT AND CC-BY-NC-SA-4.0` accordingly; `LICENSE-DATA`
and the file's own header carry the full notice, and
`scripts/build_returns_history.py` regenerates the file from the
upstream dataset.
