"""Tagged JSON encoding for polymorphic values (roadmap 6.2; planning §4.5).

Entity fields decode by field context — the schema knows a balance is
``Money`` — but assumption values and scenario override values are
polymorphic: an override may hold an ``int`` retirement age, a
``Decimal`` fraction, a ``Money`` amount, a rule tag, a structured
table, or an annuity product enum. A closed tag vocabulary keeps the
exact runtime type through the JSON round trip: ``Decimal`` and
``Money`` travel as strings (never JSON floats — money is ``Decimal``,
planning §4.6), tables as tagged objects, and enums as their stable
tokens.

This module also holds the primitive parsers and the enum token tables
the entity codecs share.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType

from glidepath.core import (
    AnnuityBasis,
    AnnuityType,
    LifeStage,
    Money,
    ReliefMechanic,
    RevaluationReference,
    Sex,
)
from glidepath.persistence.document import PersistenceError

_KIND = "kind"
_VALUE = "value"

_KIND_INT = "int"
_KIND_TEXT = "text"
_KIND_DECIMAL = "decimal"
_KIND_MONEY = "money"
_KIND_TABLE = "table"
_KIND_ANNUITY_TYPE = "annuity_type"
_KIND_ANNUITY_BASIS = "annuity_basis"


@dataclass(frozen=True, slots=True)
class EnumTokens[E: Enum]:
    """A bidirectional stable-token map for one enum (planning §4.5).

    Tokens are persisted, so they must never change meaning; retire a
    token by adding a new one, exactly like assumption keys.
    """

    by_member: Mapping[E, str]

    def token(self, member: E) -> str:
        """The stable token written for ``member``."""
        return self.by_member[member]

    def member(self, raw: object, path: str) -> E:
        """The member a stored token names.

        Raises:
            PersistenceError: If ``raw`` is not one of the tokens.
        """
        for member, token in self.by_member.items():
            if token == raw:
                return member
        known = ", ".join(sorted(self.by_member.values()))
        msg = f"{path}: unknown token {raw!r} (one of: {known})"
        raise PersistenceError(msg)


SEX_TOKENS = EnumTokens({Sex.FEMALE: "female", Sex.MALE: "male"})
RELIEF_MECHANIC_TOKENS = EnumTokens(
    {
        ReliefMechanic.RELIEF_AT_SOURCE: "relief_at_source",
        ReliefMechanic.NET_PAY: "net_pay",
    }
)
REVALUATION_REFERENCE_TOKENS = EnumTokens(
    {
        RevaluationReference.CPI: "cpi",
        RevaluationReference.FIXED: "fixed",
        RevaluationReference.NONE: "none",
    }
)
ANNUITY_TYPE_TOKENS = EnumTokens(
    {
        AnnuityType.LEVEL: "level",
        AnnuityType.ESCALATING: "escalating",
        AnnuityType.INFLATION_LINKED: "inflation_linked",
    }
)
ANNUITY_BASIS_TOKENS = EnumTokens(
    {AnnuityBasis.SINGLE: "single", AnnuityBasis.JOINT: "joint"}
)
LIFE_STAGE_TOKENS = EnumTokens(
    {
        LifeStage.EARLY_ACCUMULATION: "early_accumulation",
        LifeStage.MID_ACCUMULATION: "mid_accumulation",
        LifeStage.PRE_RETIREMENT: "pre_retirement",
        LifeStage.DECUMULATION: "decumulation",
        LifeStage.GO_GO: "go_go",
        LifeStage.SLOW_GO: "slow_go",
        LifeStage.NO_GO: "no_go",
    }
)


def parse_decimal(raw: object, path: str) -> Decimal:
    """Parse a stored ``Decimal`` string, exactly and finitely.

    Raises:
        PersistenceError: If ``raw`` is not a string holding a finite
            decimal number.
    """
    if not isinstance(raw, str):
        msg = f"{path}: expected a decimal string, got {type(raw).__name__}"
        raise PersistenceError(msg)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        msg = f"{path}: not a decimal number: {raw!r}"
        raise PersistenceError(msg) from None
    if not value.is_finite():
        msg = f"{path}: decimal values must be finite, got {raw!r}"
        raise PersistenceError(msg)
    return value


def parse_int(raw: object, path: str) -> int:
    """Parse a stored whole number (``bool`` rejected, as ever).

    Raises:
        PersistenceError: If ``raw`` is not an integer.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        msg = f"{path}: expected a whole number, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return raw


def parse_bool(raw: object, path: str) -> bool:
    """Parse a stored JSON boolean (a decision payload, never an override).

    Raises:
        PersistenceError: If ``raw`` is not a boolean.
    """
    if not isinstance(raw, bool):
        msg = f"{path}: expected a boolean, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return raw


def parse_str(raw: object, path: str) -> str:
    """Parse a stored string.

    Raises:
        PersistenceError: If ``raw`` is not a string.
    """
    if not isinstance(raw, str):
        msg = f"{path}: expected a string, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return raw


def parse_date(raw: object, path: str) -> date:
    """Parse a stored ISO-8601 calendar date.

    Raises:
        PersistenceError: If ``raw`` is not an ISO-8601 date string.
    """
    text = parse_str(raw, path)
    try:
        return date.fromisoformat(text)
    except ValueError:
        msg = f"{path}: not an ISO-8601 date: {text!r}"
        raise PersistenceError(msg) from None


def parse_datetime(raw: object, path: str) -> datetime:
    """Parse a stored ISO-8601 timezone-aware datetime.

    Raises:
        PersistenceError: If ``raw`` is not an ISO-8601 datetime string
            carrying a UTC offset.
    """
    text = parse_str(raw, path)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        msg = f"{path}: not an ISO-8601 datetime: {text!r}"
        raise PersistenceError(msg) from None
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        msg = f"{path}: datetimes must be timezone-aware, got {text!r}"
        raise PersistenceError(msg)
    return moment


def parse_money(raw: object, path: str) -> Money:
    """Parse a stored monetary amount (a ``Decimal`` string).

    Raises:
        PersistenceError: If ``raw`` is not a finite decimal string.
    """
    return Money(parse_decimal(raw, path))


def encode_value(value: object, path: str) -> dict[str, object]:
    """Encode one polymorphic value as a tagged JSON object.

    Raises:
        PersistenceError: If the value's type is outside the closed
            vocabulary, or a table key is not a string.
    """
    kind, payload = _tagged(value, path)
    return {_KIND: kind, _VALUE: payload}


def _tagged(value: object, path: str) -> tuple[str, object]:
    """The tag and JSON payload for one value."""
    if isinstance(value, bool):
        msg = f"{path}: booleans are not a persisted value type"
        raise PersistenceError(msg)
    scalar = _tagged_scalar(value, path)
    if scalar is not None:
        return scalar
    if isinstance(value, AnnuityType):
        return (_KIND_ANNUITY_TYPE, ANNUITY_TYPE_TOKENS.token(value))
    if isinstance(value, AnnuityBasis):
        return (_KIND_ANNUITY_BASIS, ANNUITY_BASIS_TOKENS.token(value))
    if isinstance(value, Mapping):
        return (_KIND_TABLE, _encode_table(value, path))
    msg = f"{path}: cannot persist a value of type {type(value).__name__}"
    raise PersistenceError(msg)


def _tagged_scalar(value: object, path: str) -> tuple[str, object] | None:
    """The tag and payload for a scalar value; ``None`` for non-scalars.

    A non-finite ``Decimal`` is rejected here so the writer never
    produces a file :func:`parse_decimal` would refuse on reload
    (``Money`` enforces finiteness at construction already).

    Raises:
        PersistenceError: If a ``Decimal`` value is not finite.
    """
    if isinstance(value, int):
        return (_KIND_INT, value)
    if isinstance(value, str):
        return (_KIND_TEXT, value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            msg = f"{path}: decimal values must be finite, got {value!r}"
            raise PersistenceError(msg)
        return (_KIND_DECIMAL, str(value))
    if isinstance(value, Money):
        return (_KIND_MONEY, str(value.amount))
    return None


def _encode_table(value: Mapping[object, object], path: str) -> dict[str, object]:
    """Encode a structured table value, entry by entry."""
    table: dict[str, object] = {}
    for key, entry in value.items():
        if not isinstance(key, str):
            msg = f"{path}: table keys must be strings, got {type(key).__name__}"
            raise PersistenceError(msg)
        table[key] = encode_value(entry, f"{path}.{key}")
    return table


def _decode_table(raw: object, path: str) -> dict[str, object]:
    """Decode a tagged table's entries back to a plain dictionary."""
    if not isinstance(raw, dict):
        msg = f"{path}: expected a table object, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return {key: decode_value(entry, f"{path}.{key}") for key, entry in raw.items()}


_DECODERS: Mapping[str, Callable[[object, str], object]] = MappingProxyType(
    {
        _KIND_INT: parse_int,
        _KIND_TEXT: parse_str,
        _KIND_DECIMAL: parse_decimal,
        _KIND_MONEY: parse_money,
        _KIND_ANNUITY_TYPE: ANNUITY_TYPE_TOKENS.member,
        _KIND_ANNUITY_BASIS: ANNUITY_BASIS_TOKENS.member,
        _KIND_TABLE: _decode_table,
    }
)


def decode_value(raw: object, path: str) -> object:
    """Decode one tagged JSON object back to its exact runtime type.

    Raises:
        PersistenceError: If the tag shape or kind is not recognised.
    """
    if not isinstance(raw, dict) or set(raw) != {_KIND, _VALUE}:
        msg = f"{path}: expected a tagged value object with keys 'kind' and 'value'"
        raise PersistenceError(msg)
    kind = raw[_KIND]
    decoder = _DECODERS.get(kind) if isinstance(kind, str) else None
    if decoder is None:
        msg = f"{path}: unknown value kind {kind!r}"
        raise PersistenceError(msg)
    return decoder(raw[_VALUE], path)
