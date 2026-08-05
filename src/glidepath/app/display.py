"""Display formatting for the app layer (planning §4.7).

Decimal→display conversion lives here so shells render pre-formatted
strings and never touch domain values. The inverse direction — parsing
user-entered text back into domain values — lives in
:mod:`glidepath.app.forms`.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Final

from glidepath.core import LifeStage, Money

_MAX_STRUCTURED_LENGTH = 120

_ENUM_LABELS: Final[Mapping[Enum, str]] = {
    LifeStage.GO_GO: "Go-go",
    LifeStage.SLOW_GO: "Slow-go",
    LifeStage.NO_GO: "No-go",
}
"""Enum members whose copy the generic underscore rule would mangle
("Go go"); the retirement sub-stages read hyphenated (issue #114)."""

WRAPPER_KIND_NAMES: Final[Mapping[str, str]] = {
    "uk.workplace_dc": "Workplace DC",
    "uk.sipp": "SIPP",
    "uk.isa": "ISA",
    "uk.lisa": "Lifetime ISA",
    "uk.gia": "General investment account",
    "uk.cash": "Cash savings",
}
"""Display names for the shipped wrapper kinds (planning §2, roadmap 9.2)."""


def format_wrapper_kind(kind: object) -> str:
    """A wrapper kind id as its display name, falling back to the id."""
    text = str(kind)
    return WRAPPER_KIND_NAMES.get(text, text)


ASSUMPTION_NAMES: Final[Mapping[str, str]] = {
    "inflation.cpi": "Inflation (CPI)",
    "earnings.growth.real": "Earnings growth (above inflation)",
    "returns.equity.real": "Equity return (above inflation)",
    "returns.bonds.real": "Bond return (above inflation)",
    "returns.cash.real": "Cash return (above inflation)",
    "volatility.equity": "Equity volatility",
    "volatility.bonds": "Bond volatility",
    "volatility.cash": "Cash volatility",
    "correlation.equity_bonds": "Equity-bond correlation",
    "correlation.equity_cash": "Equity-cash correlation",
    "correlation.bonds_cash": "Bond-cash correlation",
    "fees.platform": "Platform fee",
    "fees.fund": "Fund fee",
    "yield.equity": "Equity yield",
    "yield.bonds": "Bond yield",
    "yield.cash": "Cash yield",
    "horizon.planning_age": "Planning horizon age",
    "glidepath.default_shape": "Default glide path shape",
    "policy.state_pension.uprating": "State pension uprating policy",
    "policy.tax.future_years": "Future tax years policy",
    "annuity.level.single.65": "Annuity rate (level, single life, age 65)",
    "annuity.escalating3.single.65": (
        "Annuity rate (3% escalating, single life, age 65)"
    ),
    "annuity.inflation_linked.single.65": (
        "Annuity rate (inflation-linked, single life, age 65)"
    ),
    "annuity.age_adjustment": "Annuity rate age adjustment",
}
"""Human display names for the shipped assumption keys (roadmap 8.3).

Screens show these instead of the raw dotted ids; the id stays
available (tooltips, override targeting) because it is what scenario
overrides and persisted plans address."""


def format_assumption_key(key: object) -> str:
    """An assumption key as its display name, falling back to the id."""
    text = str(key)
    return ASSUMPTION_NAMES.get(text, text)


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


def format_percent(value: Decimal) -> str:
    """A fraction as a percentage to one decimal place, e.g. ``95.0%``."""
    return f"{value * Decimal(100):.1f}%"


def format_share(value: Decimal) -> str:
    """A fraction as a whole-reading percentage, e.g. ``80%`` or ``62.5%``.

    Trailing zeros are trimmed — allocation copy reads "80% equity",
    never "80.0% equity".
    """
    text = format(value * Decimal(100), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


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
        generic = str(value.name).replace("_", " ").capitalize()
        return _ENUM_LABELS.get(value, generic)
    if isinstance(value, Mapping):
        return _format_mapping(value)
    return str(value)
