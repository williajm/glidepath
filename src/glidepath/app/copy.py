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
            "balance and contributions, and any defined benefit pension. "
            'Add one wrapper or DB section per account; "as of" dates '
            'left blank default to today. Press "Save facts and project" to run the '
            "projection — if anything cannot be read, the message under "
            "the buttons says which field to fix and nothing is saved "
            "until it parses."
        ),
    ),
    (
        "Charts — see the projection",
        (
            "The Charts tab draws the projection, one bar per tax year: "
            "wrapper balances, income composition, and tax due. Hover a "
            "bar for the exact figures, and switch between real (today's "
            "money) and nominal presentation with the basis toggle."
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
            "overrides, and scenarios — to a plan file on your computer, "
            'and "Open plan…" loads one back; the last plan you used '
            "reopens on the next launch. All data stays local; nothing is "
            "ever transmitted."
        ),
    ),
    ("Not financial advice", DISCLAIMER_BODY),
)
