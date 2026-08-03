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
