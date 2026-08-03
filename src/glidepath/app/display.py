"""Display formatting for the app layer (planning §4.7).

Decimal→display conversion lives here so shells render pre-formatted
strings and never touch domain values. The inverse direction — parsing
user-entered text back into domain values — lives in
:mod:`glidepath.app.forms`.
"""

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Final

from glidepath.core import Money

_MAX_STRUCTURED_LENGTH = 120

WRAPPER_KIND_NAMES: Final[Mapping[str, str]] = {
    "uk.workplace_dc": "Workplace DC",
    "uk.sipp": "SIPP",
    "uk.isa": "ISA",
}
"""Display names for the shipped wrapper kinds (planning §2)."""


def format_wrapper_kind(kind: object) -> str:
    """A wrapper kind id as its display name, falling back to the id."""
    text = str(kind)
    return WRAPPER_KIND_NAMES.get(text, text)


def _format_mapping(value: Mapping[object, object]) -> str:
    """A structured table as compact ``key=value`` pairs, truncated."""
    rendered = "; ".join(f"{key}={format_value(entry)}" for key, entry in value.items())
    if len(rendered) > _MAX_STRUCTURED_LENGTH:
        return rendered[: _MAX_STRUCTURED_LENGTH - 1] + "…"
    return rendered


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
    (rendered as compact ``key=value`` pairs, truncated when long).
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
        return _format_mapping(value)
    return str(value)
