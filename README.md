<!-- Image and badge URLs are absolute so the README renders on PyPI,
     which does not resolve repository-relative paths. -->
<p align="center">
  <img src="https://raw.githubusercontent.com/williajm/glidepath/main/src/glidepath/gui/assets/wordmark.png" alt="glidepath" width="420">
</p>

[![PyPI](https://img.shields.io/pypi/v/glidepath)](https://pypi.org/project/glidepath/)
[![CI](https://github.com/williajm/glidepath/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/williajm/glidepath/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=williajm_glidepath&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=williajm_glidepath)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=williajm_glidepath&metric=coverage)](https://sonarcloud.io/summary/new_code?id=williajm_glidepath)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/williajm/glidepath/badge)](https://scorecard.dev/viewer/?uri=github.com/williajm/glidepath)
[![Python versions](https://img.shields.io/pypi/pyversions/glidepath)](https://pypi.org/project/glidepath/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![License: MIT + CC BY-NC-SA 4.0 data](https://img.shields.io/badge/license-MIT%20%2B%20CC%20BY--NC--SA%204.0%20data-blue)](https://github.com/williajm/glidepath/blob/main/LICENSE)

<p align="center">
  <a href="https://williajm.github.io/glidepath/"><b>williajm.github.io/glidepath</b></a>
</p>

A desktop retirement and investment planner. UK-first, built so other
regions can be added later. Every number in a plan is a **fact** you
stated, a **decision** you made, or an **assumption** the app defaulted
— always inspectable, never silently guessed. All data stays local;
nothing is transmitted.

<p align="center">
  <img src="https://raw.githubusercontent.com/williajm/glidepath/main/docs/screenshots/charts.png"
       alt="The charts tab: the Monte Carlo fan chart — nested percentile bands deepening toward the median line — beside the success-rate readout and the retirement-age and backtest cards"
       width="800">
</p>

*(All screenshots show example data, not anyone's real finances.)*

What it models today (UK, single or couple):

- **Wrappers** — workplace DC, SIPP, S&S ISA, LISA, GIA and cash, with
  UK contribution relief mechanics and dividend/savings taxation.
- **Defined benefit pensions** — deferred entitlements or active
  CARE-style accrual, with revaluation, early/late factors and
  commutation; **state pension** from your official DWP forecast,
  including deferral.
- **Tax** — rUK and Scottish income tax from verified 2026/27 data
  files; pension allowances (AA/taper/MPAA, lump-sum allowance).
- **Couples** — an optional partner, modelled end to end: one pooled
  household decumulation drawing tax-efficiently across both partners'
  wrappers with each person taxed individually (mixed rUK/Scottish
  residency included), the marriage allowance claimed when eligible,
  optional survivor modelling ("model death at age" — pensions pass as
  beneficiary drawdown, ISAs via the additional permitted
  subscription, DB schemes at their survivor fraction), and joint-life
  annuities paying a 50/66/100% survivor income.
- **Projection** — deterministic or Monte Carlo runs from the app:
  success rate, probability of ruin, ending-pot percentiles, and a
  probability fan chart on its own tab, reproducible from a seed. With
  a de-risking glide path, tax-aware decumulation with optional
  go-go/slow-go/no-go retirement spending multipliers, and annuity
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

## More screenshots

Facts entry — everything on this screen is either a fact you state or
a choice you make; anything estimated lives in the assumptions
inspector instead:

<p align="center">
  <img src="https://raw.githubusercontent.com/williajm/glidepath/main/docs/screenshots/facts.png"
       alt="The facts tab: the About you, Household spending, and State pension cards of the entry form, with the example plan's values filled in"
       width="800">
</p>

Stated vs assumed — the provenance view: the facts you stated, the
choices in effect, and every assumption the run used with its value,
default/overridden status, source, and date:

<p align="center">
  <img src="https://raw.githubusercontent.com/williajm/glidepath/main/docs/screenshots/stated_vs_assumed.png"
       alt="The stated-vs-assumed tab: tables of stated facts, choices in effect, assumptions used with sources, and the plan structure"
       width="800">
</p>

## Disclaimer

Glidepath is a personal modelling tool for exploring retirement scenarios.
It is not financial advice and is not regulated; its outputs depend on
assumptions that will not match reality. Do not make financial decisions
based solely on this tool.

## Install

Glidepath is installed from [PyPI](https://pypi.org/project/glidepath/)
with [uv](https://docs.astral.sh/uv/), which also fetches the Python it
needs — you do not need Python installed first.

1. **Install uv.** Windows (PowerShell):

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   macOS or Linux:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Close and reopen the terminal, then check `uv --version` prints a
   version.

2. **Install glidepath:**

   ```sh
   uv tool install glidepath
   ```

3. **Launch it:**

   ```sh
   glidepath
   ```

If the shell cannot find `glidepath` afterwards, run
`uv tool update-shell` once and reopen the terminal. Upgrade to a new
release later with `uv tool upgrade glidepath`. (`pipx install
glidepath` works too.)

Or run straight from a checkout:

```sh
git clone https://github.com/williajm/glidepath.git
cd glidepath
uv run glidepath
```

The first run from a checkout creates a virtual environment and
installs the locked dependencies automatically, then launches the
desktop app. Accept the
disclaimer and a fresh install
opens with an example plan already projected, so every tab has
something to show — replace its values with your own facts, or clear
the form and start blank. Charts label each bar with the tax year and
your age and switch between today's money and nominal; Help → "How to
use glidepath" walks through every tab. Save your plan from the File
menu as a `.glidepath.json` file you own, stored wherever you choose;
the next launch reopens your last plan automatically.

## Scripting

The engine has no Qt dependency and runs from a Python script: build
a plan, project it deterministically or by Monte Carlo, and change the
assumptions it uses — the growth rate, inflation, fees, the planning
horizon — without opening the GUI.
[`docs/scripting.md`](https://github.com/williajm/glidepath/blob/main/docs/scripting.md)
walks through it step by step, from a machine with no uv to a worked
projection. Glidepath is versioned as an application, not a library:
the module layout is not a stable API, so pin the release your script
was written against.

## Developing

Development additionally requires GNU Make, which drives every workflow
command:

```sh
make sync          # create the platform venv from the lockfile (fails if the lock has drifted)
make hooks         # install the pre-commit hooks (required before committing)
```

One-time per machine: set `UV_PROJECT_ENVIRONMENT` user-wide (`.venv-win`
on Windows, `.venv-wsl` in WSL) so bare `uv` commands and the git hooks use
the same venv as make. See `CLAUDE.md`. (Skipping this only matters for a
shared Windows/WSL checkout — for trying the app, uv's default `.venv` is
fine.)

## Everyday commands

```sh
make check   # all merge gates: ruff, format, mypy --strict, pytest (>=96% cov), dep age
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

## Releases

Releases are `vX.Y.Z` tags on `main`. Each one is published to
[PyPI](https://pypi.org/project/glidepath/) as an sdist and wheel via
trusted publishing with PEP 740 attestations, and as a GitHub Release
carrying its notes from
[`CHANGELOG.md`](https://github.com/williajm/glidepath/blob/main/CHANGELOG.md)
with the same artifacts attached, each carrying signed build
provenance — verify a downloaded file with
`gh attestation verify <file> -R williajm/glidepath`. The signed
provenance bundle is attached to each release too
(`glidepath-X.Y.Z-provenance.intoto.jsonl`), so verification also
works offline with `--bundle`. There are no
packaged binary builds (installer/exe) yet — install from PyPI as
above.

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
