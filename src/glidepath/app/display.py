"""Display formatting for the app layer (planning §4.7).

Decimal→display conversion lives here so shells render pre-formatted
strings and never touch domain values. The inverse direction — parsing
user-entered text back into domain values — lives in
:mod:`glidepath.app.forms`.
"""

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

from glidepath.core import Money


def format_money(value: Money) -> str:
    """A money amount as pounds and pence, e.g. ``£1,234.56``."""
    amount = value.quantized().amount
    if amount < 0:
        return f"-£{-amount:,.2f}"
    return f"£{amount:,.2f}"


def format_date(value: date) -> str:
    """A date in ISO format — the one date format the product uses."""
    return value.isoformat()


def format_recorded(moment: datetime) -> str:
    """A recorded-on timestamp shown as its (UTC) calendar date."""
    return moment.date().isoformat()


def format_value(value: object) -> str:
    """Any fact, decision, or assumption value as display text.

    Covers every value type the domain model wraps: money, dates,
    numbers, enum choices, policy strings, and structured tables
    (which summarise rather than dump — they are inspected in their
    own views, not in a table cell).
    """
    if isinstance(value, Money):
        return format_money(value)
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return format_recorded(value)
    if isinstance(value, date):
        return format_date(value)
    if isinstance(value, Enum):
        return str(value.name).replace("_", " ").capitalize()
    if isinstance(value, Mapping):
        return f"structured table ({len(value)} entries)"
    return str(value)
