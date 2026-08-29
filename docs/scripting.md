# Scripting glidepath from Python

Glidepath is a desktop app, but the engine underneath it is plain
typed Python with no Qt dependency, and every screen in the app is a
thin shell over it. This guide shows how to install glidepath into a
Python project, run a projection from a script, and adjust the numbers
the projection depends on — the growth rate, inflation, fees, the
planning horizon — without opening the GUI.

**Before you start:**

- **The module layout is not a stable API.** Glidepath is versioned as
  an application (planning §4.10): a new release may rename or move
  what this guide imports, so pin the version in your project and
  re-read this page when you upgrade. Everything below is tested
  against the release it ships with.
- **Money is `Decimal`, never `float`**; datetimes are timezone-aware.
  The examples follow both rules — copy them exactly.
- **Disclaimer.** Glidepath is a personal modelling tool for exploring
  retirement scenarios. It is not financial advice and is not
  regulated; its outputs depend on assumptions that will not match
  reality. Do not make financial decisions based solely on this tool.

## 1. Install from nothing

You need [uv](https://docs.astral.sh/uv/), which manages Python itself
as well as packages — you do not need Python installed first.

**Step 1 — install uv.**

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS or Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen the terminal, then check it is on your path:

```sh
uv --version
```

(If the shell cannot find `uv`, run `uv tool update-shell` once, or
add `~/.local/bin` — `%USERPROFILE%\.local\bin` on Windows — to your
`PATH`.)

**Step 2 — make a project and add glidepath.**

```sh
uv init --python 3.14 my-plan
cd my-plan
uv add glidepath
```

`uv init` creates a folder with a `pyproject.toml`; `uv add` downloads
Python 3.14 if you do not have it, resolves glidepath and its
dependencies, and pins them in `uv.lock`. Glidepath requires
Python 3.14 or newer, hence the `--python` flag.

Expect the download to be a few hundred megabytes: glidepath is one
package, and it depends on the PySide6 GUI toolkit even when you only
use the engine.

**Step 3 — run a script.**

Save the script from the next section as `projection.py` inside
`my-plan`, then:

```sh
uv run python projection.py
```

`uv run` executes the script inside the project's environment; there
is no virtualenv to activate.

**Alternatives.** For a one-off with no project folder,
`uv run --python 3.14 --with glidepath python projection.py` installs
into a cache and runs. If you already manage Python 3.14 yourself,
`python -m pip install glidepath` inside a virtual environment works
too.

## 2. What to import

Three packages cover everything a script needs:

| Package | What it gives you | Names used in this guide |
| --- | --- | --- |
| `glidepath.core` | The region-agnostic engine: run a projection, present it, describe assumptions | `run`, `run_paths`, `RunConfig`, `RunMode`, `build_report`, `ReportBasis`, `AssumptionKey`, `AssumptionSet`, `Assumption`, `Provenance`, `Money`, `RetirementAgeSearch`, `earliest_retirement_age` |
| `glidepath.regions.uk` | The UK rules and the shipped default assumptions | `default_assumption_set`, `future_years_extension`, `uk_region` |
| `glidepath.app` | The app layer: parse a plan from plain text, load/save `.glidepath.json` files, export CSV | `FactsFormData`, `PersonFormData`, `parse_facts_form`, `initial_plan_state`, `state_with_household`, `state_with_override`, `save_plan_state`, `load_plan_state`, `cash_flow_csv`, `format_money` |

The engine's signature is the shape to remember:

```text
run(plan, assumptions, region, config) -> ProjectionResult
```

- **plan** — a `Household`: the facts you state and the decisions you
  make (people, spending, wrappers, pensions).
- **assumptions** — an `AssumptionSet`: every estimated number, each
  with its default, source and date. This is where the growth rate
  lives (section 4).
- **region** — the `Region` bundle of UK tax years, allowances and
  age rules, built from the shipped data files.
- **config** — a `RunConfig`: today's date, deterministic or Monte
  Carlo, seed, withdrawal strategy.

## 3. A first projection

The simplest way to build a `Household` is the same route the app's
facts form takes: describe the plan as plain text and let
`parse_facts_form` validate it and stamp every fact with its
provenance. This is the app's own example plan — a 35-year-old on
£52,000 targeting retirement at 62, with a workplace pension and an
ISA.

```python
from datetime import UTC, date, datetime

from glidepath.app import FactsFormData, PersonFormData, format_money, parse_facts_form
from glidepath.core import ReportBasis, RunConfig, build_report, run
from glidepath.regions.uk import (
    default_assumption_set,
    future_years_extension,
    uk_region,
)

TODAY = date.today()
RECORDED = datetime.now(UTC)  # when these facts were stated (must be tz-aware)

form = FactsFormData(
    persons=(
        PersonFormData(
            person={
                "date_of_birth": "1991-06-15",
                "tax_residency": "uk.ruk",  # or "uk.scotland"
                "employment_income": "52000",
                "target_retirement_age": "62",
            },
            state_pension={"forecast_weekly_amount": "230.25"},
        ),
    ),
    spending={"annual_spending_real": "24000"},  # net, today's money
    wrappers=(
        {
            "kind": "uk.workplace_dc",
            "balance": "48000",
            "employee_contribution": "4200",
            "employer_contribution": "3150",
            "relief_mechanic": "net_pay",
            "escalation": "earnings",
        },
        {"kind": "uk.isa", "balance": "16500", "employee_contribution": "4800"},
    ),
)

parsed = parse_facts_form(form, recorded_on=RECORDED, today=TODAY)
if parsed.household is None:
    raise SystemExit(parsed.errors)  # each error names its section and field
household = parsed.household

assumptions = default_assumption_set()
region = uk_region(future_years_extension(assumptions))
config = RunConfig(today=TODAY)

result = run(household, assumptions, region, config)
report = build_report(result, ReportBasis.REAL)  # today's money; NOMINAL for cash terms

for row in report.rows:
    print(
        f"{row.period.start.year}/{(row.period.start.year + 1) % 100:02d}",
        f"age {row.age_at_period_start}",
        f"{row.stage.name:<18}",
        f"pot {format_money(row.closing_balance):>14}",
        f"shortfall {format_money(row.shortfall):>12}",
    )
```

Each report row is one tax year for one person: income, tax, spending
need, what was withdrawn, fees, growth, and the closing balance, with
per-wrapper balances under `row.wrapper_balances`. A non-zero
`shortfall` means that year's spending need was not met — the plan
runs out of money.

`future_years_extension(assumptions)` is what lets the run project
past the last shipped tax year: it tells the region how thresholds
move in the future (frozen, then CPI-indexed by default). Build the
region from the *same* assumption set you run with; if you change
`inflation.cpi` or `policy.tax.future_years`, rebuild it.

Every plan-file field the app's form accepts is a string key in these
mappings. Rather than maintain a second list here, ask the form for
its own field catalogue — the keys, labels, whether a field is
required, and the accepted choices:

```python
from glidepath.app import build_facts_form_view_model

form_spec = build_facts_form_view_model()
for section in (form_spec.person, form_spec.spending, form_spec.state_pension, form_spec.wrapper):
    print(f"[{section.key}] {section.title}")
    for field in section.fields:
        choices = " | ".join(option.value or '""' for option in field.choices)
        print(f"  {field.key:<28} {'required' if field.required else '':<9} {choices}")
```

(`form_spec.db_pension`, `form_spec.annuity_purchase`, and the
`partner_*` sections list the rest; a second person goes in a second
`PersonFormData`, with their wrappers carrying `"owner": "1"`.)

## 4. Adjusting assumptions — growth rate, inflation, fees, horizon

Glidepath separates three kinds of number, and which kind you are
changing decides where you change it:

- **Facts** you state (date of birth, balances, income) and
  **decisions** you make (retirement age, contributions, equity
  allocation, withdrawal strategy) are plan fields — edit the form
  mappings in section 3 and re-parse.
- **Assumptions** are estimates the app supplied — growth, inflation,
  volatility, fees, how long you plan for. They live in the
  `AssumptionSet` and are the subject of this section.

### The shipped defaults

Rates are annual fractions (`0.04` = 4%/yr) and, unless the key says
otherwise, **real** — over and above inflation. The sources are in
`docs/planning.md` §7 and on each `Assumption.source`.

| Key (`AssumptionKey`) | Default | Meaning |
| --- | --- | --- |
| `inflation.cpi` | `0.02` | CPI; also drives future tax-threshold indexation |
| `earnings.growth.real` | `0.005` | Real growth of employment income (and of contributions set to grow with earnings) |
| `returns.equity.real` | `0.04` | **Real growth rate of equities** — the usual "growth rate" knob |
| `returns.bonds.real` | `0.005` | Real growth rate of bonds |
| `returns.cash.real` | `-0.005` | Real return on cash |
| `volatility.equity` / `.bonds` / `.cash` | `0.18` / `0.07` / `0.01` | Annual volatility, Monte Carlo only |
| `correlation.equity_bonds` / `.equity_cash` / `.bonds_cash` | `0.2` / `0.0` / `0.2` | Return correlations, Monte Carlo only |
| `fees.platform` | `0.0025` | Platform fee on every invested wrapper |
| `fees.fund` | `0.0015` | Fund charge (OCF) on every invested wrapper |
| `yield.equity` / `.bonds` / `.cash` | `0.02` / `0.025` / `0.015` | Income yields — read only by the natural-yield strategy and taxable GIA/cash wrappers |
| `horizon.planning_age` | `95` (an `int`) | The age the projection runs to |
| `glidepath.default_shape` | 80% equity, de-risking linearly over the 15 years before retirement to 40%, held in drawdown | The default glide path when a wrapper states no equity allocation (a table) |
| `policy.state_pension.uprating` | triple lock, floor 2.5% | How the state pension grows (a table) |
| `policy.tax.future_years` | frozen to 2030/31, then CPI-indexed | How tax thresholds move beyond the shipped tax year (a table) |
| `annuity.level.single.65` and other `annuity.*` | see planning §7 | Annuity pricing, used only when the plan buys an annuity |
| `db.survivor_fraction` | `0.50` | Survivor's share of a DB pension |
| `spending.survivor_multiplier` | `0.70` | Household spending after a partner's death |

The full list is `list(AssumptionKey)`; the exact value of any default
is `default_assumption_set().get(key).value`.

### Overriding an assumption

An `AssumptionSet` is immutable, so an override is a new set with one
entry replaced. The replacement keeps the key, the shipped default and
the description, and records the new value, who set it, and when —
the same provenance the app's assumptions inspector writes, so a
report can still answer "which of these numbers did I change?":

```python
from dataclasses import replace
from decimal import Decimal

from glidepath.core import AssumptionKey, AssumptionSet, Provenance


def with_override(assumptions, key, value, *, source="my script"):
    """A copy of ``assumptions`` with ``key`` set to ``value``."""
    base = assumptions.get(key)
    changed = replace(
        base,
        value=value,
        provenance=Provenance.USER_OVERRIDE,
        source=source,
        recorded_on=RECORDED,
    )
    return AssumptionSet(
        changed if k == key else assumptions.get(k) for k in assumptions.keys
    )


cautious = with_override(assumptions, AssumptionKey.RETURNS_EQUITY_REAL, Decimal("0.02"))
cautious = with_override(cautious, AssumptionKey.FEES_PLATFORM, Decimal("0.0045"))
cautious = with_override(cautious, AssumptionKey.HORIZON_PLANNING_AGE, 100)

cautious_region = uk_region(future_years_extension(cautious))  # rebuild alongside the set
cautious_result = run(household, cautious, cautious_region, config)

print("Ending pot, shipped defaults:", format_money(report.rows[-1].closing_balance))
print("Ending pot, cautious set:   ", format_money(build_report(cautious_result).rows[-1].closing_balance))
```

Match the value's type to the default's: `Decimal` for every rate,
`int` for `horizon.planning_age`, and a `dict` for the table-valued
keys, for example a steeper glide path:

```python
steeper = with_override(
    assumptions,
    AssumptionKey.GLIDEPATH_DEFAULT_SHAPE,
    {
        "equity_start": "0.90",
        "derisk_years_before_retirement": 10,
        "equity_at_retirement": "0.30",
        "transition": "linear",
        "in_drawdown": "hold",
    },
)
```

### The same thing through the app layer

If you would rather work the way the GUI does — hold one `PlanState`,
override by key name from text, and get the projection re-run for
you — use the app layer. It validates the text, keeps the state
immutable, and folds any failure into a message instead of raising:

```python
from glidepath.app import initial_plan_state, state_with_household, state_with_override

state = state_with_household(initial_plan_state(), household, today=TODAY)
outcome = state_with_override(
    state, "returns.equity.real", "0.02", recorded_on=RECORDED, today=TODAY
)
if outcome.error:
    raise SystemExit(outcome.error)
state = outcome.state  # state.result is the re-run projection
print("Ending pot via app layer:", format_money(build_report(state.result).rows[-1].closing_balance))
```

A blank value (`""`) restores the shipped default. Table-valued keys
take `key = value` lines, one per line (nested keys dotted:
`scotland.lower_bands_frozen_until_tax_year = 2027/28`).

## 5. Monte Carlo

The same engine, with returns drawn at random around the
`returns.*.real` means using the `volatility.*` and `correlation.*`
assumptions. Every path is seeded, so a run is reproducible from its
seed and path index:

```python
from decimal import Decimal

from glidepath.core import RunMode, run_paths

mc_config = RunConfig(today=TODAY, mode=RunMode.MONTE_CARLO, seed=42)
mc = run_paths(household, assumptions, region, mc_config, paths=200)

print(f"Success rate:        {mc.success_rate:.1%}")
print(f"Probability of ruin: {mc.probability_of_ruin:.1%}")
print("Median ending pot:  ", format_money(mc.ending_pot_percentile(Decimal("50"))))  # percentile in [0, 100]
```

200 paths take a few seconds on one core; the app defaults to 100 and
allows up to 10,000. For a large run pass `parallelism=` — the app
layer's `glidepath.app.path_pool(paths)` context manager sizes a
process pool to your machine.

## 6. "When can I retire?"

The solver reruns the projection at each candidate retirement age and
returns the earliest that sustains a target net income (today's
money), or `None` if no age in the range does:

```python
from glidepath.core import Money, RetirementAgeSearch, earliest_retirement_age

search = RetirementAgeSearch(
    target_income=Money(Decimal("24000")), minimum_age=55, maximum_age=68
)
print("Earliest retirement age:", earliest_retirement_age(household, assumptions, region, config, search))
```

Under a Monte Carlo `config`, set `paths=` and `target_success_rate=`
on the search to ask for, say, 90% of paths avoiding ruin.

## 7. Saved plans and exports

The `.glidepath.json` files the app saves are plain JSON and round-trip
through the app layer, so a script can load a plan you built in the
GUI, or hand a scripted plan to the GUI:

```python
from pathlib import Path

from glidepath.app import cash_flow_csv, load_plan_state, save_plan_state

plan_path = Path("my-plan.glidepath.json")
print(save_plan_state(state, plan_path).message)

loaded = load_plan_state(plan_path, today=TODAY)  # projected on load
if loaded.state is None:
    raise SystemExit(loaded.message)

csv_text = cash_flow_csv(loaded.state, basis=ReportBasis.REAL, plan_name="my plan")
Path("my-plan-cash-flow.csv").write_text(csv_text, encoding="utf-8")
```

The CSV is the same per-year cash-flow table the app exports, headed
by the plan name, run mode, and every assumption the run used.

## 8. What did the run actually use?

Every result carries its own manifest: the facts stated, the
decisions in effect, the assumptions the engine *read* (only those —
an assumption the plan never needed is not listed), the region data
version, and the seed:

```python
for assumption in result.provenance.assumptions:
    print(f"{assumption.key.value:<32} {assumption.provenance.name:<20} {assumption.value!s:.40}")
print(result.provenance.region_data_version[:60], "...")
```

That list is the honest answer to "what did this projection assume?"
— and the place to check that an override you intended was actually
read.
