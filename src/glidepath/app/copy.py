"""User-facing product copy (planning §1, §4.7).

The disclaimer is a product requirement: shown on first run and in
About, preserved in exports and the README. The wording here is the
canonical in-app copy and matches the README's Disclaimer section.
"""

from typing import Final

APP_NAME: Final = "glidepath"

DISCLAIMER_TITLE: Final = "Before you start"

DISCLAIMER_BODY: Final = (
    "Glidepath is a personal modelling tool for exploring retirement "
    "scenarios. It is not financial advice and is not regulated; its "
    "outputs depend on assumptions that will not match reality. Do not "
    "make financial decisions based solely on this tool."
)

DISCLAIMER_ACCEPT_LABEL: Final = "I understand — continue"

DISCLAIMER_DECLINE_LABEL: Final = "Quit"

ABOUT_TITLE: Final = f"About {APP_NAME}"

HELP_MENU_LABEL: Final = "Help"

HELP_GUIDE_TITLE: Final = f"How to use {APP_NAME}"

DATE_PICKER_TOOLTIP: Final = "Pick a date from the calendar"

HELP_GUIDE_INTRO: Final = (
    "Glidepath projects a retirement plan from facts you state, choices "
    "you make, and assumptions you can always inspect and override. Each "
    "tab is one part of that loop."
)

HELP_GUIDE_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    (
        "Start from the example",
        (
            "On a fresh install glidepath opens with an example plan "
            "already projected, so every tab has something to show; the "
            "note under the form's buttons tells you while the example is "
            "on screen. Replace its values with your own facts, or press "
            '"Clear the form" to start blank. Once you have saved or '
            "opened a plan of your own, the last one you used reopens at "
            "launch instead."
        ),
    ),
    (
        "Facts — enter what you know",
        (
            "The Facts tab captures what you state: your date of birth and "
            "tax residency, planned retirement age, household spending, "
            "your state pension record, each savings wrapper (workplace "
            "pension, SIPP, ISA, LISA, general account, or cash) with its "
            "balance, contributions, and optionally its own equity "
            "allocation percentage — blank follows the de-risking glide "
            "path, 100 models an all-equity wrapper — any defined "
            "benefit pension, and "
            "any planned annuity purchase — converting part of your "
            "pension pot into lifetime income at an age you choose. "
            "Add one wrapper, DB, or annuity section per item. Dates can "
            "be typed or picked from the calendar assist, and the "
            '"as of" dates on balances and your state pension forecast '
            "default to today when left blank. Your state pension needs "
            "your official DWP forecast (gov.uk/check-state-pension) — "
            "the app never re-derives what DWP has already computed. "
            'Press "Save facts and project" to run the '
            "projection — if anything cannot be read, the message under "
            "the buttons says which field to fix and nothing is saved "
            "until it parses."
        ),
    ),
    (
        "Charts — see the projection",
        (
            "The Charts tab draws the projection, one bar per tax year "
            "labelled with the year and your age at its start: wrapper "
            "balances, income composition, and tax due. An "
            '"Invested as" line states the asset mix each wrapper '
            "actually ran — your stated equity split, or the glide path "
            "with whether it is the shipped default or your override — "
            "so the modelled allocation is never a silent assumption. "
            "Hover any bar, line, or band for "
            "the exact figures, and switch between real (today's money) "
            "and nominal presentation with the basis toggle. Switch the "
            "run mode to Monte Carlo, choose the paths and seed, and "
            "press Run Monte Carlo to read the success metrics and open "
            "a Monte Carlo fan chart on its own tab — nested percentile "
            "bands deepening in colour toward the median line, each band "
            "the central share of simulated paths that closed inside it; "
            "the same seed and inputs always reproduce the same result. "
            "Runs execute in the background — the buttons disable while "
            "one is in flight — and any change to the plan clears a held "
            "result, so the charts never show a run that no longer "
            "matches the plan on screen. "
            "The historical backtest card replays the plan over every "
            "rolling window of world market history (a global equity "
            "index in sterling terms, UK gilts and cash, deflated by UK "
            "inflation): press Run backtest to read the share of "
            "historical starting years the plan survives, the best and "
            "worst starting years, and — over the balances chart — the "
            "actual balance paths those years would have produced, plus "
            "any starting year you type into the card. Sequence-of-"
            "returns risk that independent Monte Carlo draws cannot "
            "reproduce. "
            'The "When can I retire?" card answers with the earliest '
            "retirement age at which the plan sustains a target income — "
            "a replacement rate you choose (66% of your employment income "
            "by default) — on the selected run mode's basis: met with no "
            "shortfall deterministically, or with at least your chosen "
            "Monte Carlo success rate. "
            'The "How much can I draw down?" card asks the same question '
            "the other way around: choose a retirement age (your planned "
            "one by default) and it answers with the highest net annual "
            "income, in today's money, the plan sustains from that age — "
            "on the same selected basis."
        ),
    ),
    (
        "Scenarios — compare what-ifs",
        (
            "The Scenarios tab compares variants of your plan. Add a named "
            "scenario, then give it overrides: each override changes one "
            "of your decisions (like your retirement age or contribution "
            "choices) or one assumption — never a stated fact. The "
            "comparison table and chart show every scenario against the "
            "base plan on the metric and money basis you pick."
        ),
    ),
    (
        "Stated vs assumed — check every number",
        (
            'The stated-vs-assumed tab answers "which of these numbers '
            'did I state, and which did the app assume?" It lists your '
            "facts, every assumption with its value, source, date, and "
            "whether you overrode the default, your decisions, and the "
            "plan's structure. Double-click an assumption to override its "
            "value in place; the projection re-runs immediately."
        ),
    ),
    (
        "Save and reopen your plan",
        (
            'File → "Save plan" writes everything — facts, decisions, '
            "overrides, and scenarios — to a plan file on your computer "
            '("Save plan as…" picks a new file), and "Open plan…" loads '
            "one back; the last plan you used reopens on the next "
            "launch. All data stays local; nothing is ever transmitted."
        ),
    ),
    (
        "Export the plan",
        (
            'File → "Export cash flow (CSV)" writes the projection\'s '
            "per-year table — every income, tax, contribution, fee, and "
            "balance figure exactly as charted, in the money basis the "
            "Charts tab has selected — for a spreadsheet to audit or "
            'extend. "Export report (PDF)" prints the whole plan: your '
            "inputs with their stated-vs-assumed provenance, the "
            "projection charts, Monte Carlo metrics when a run is held, "
            "and the scenario comparison when scenarios exist. Both "
            "exports carry the disclaimer."
        ),
    ),
    ("Not financial advice", DISCLAIMER_BODY),
)
