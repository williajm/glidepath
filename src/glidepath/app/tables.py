"""Text form for table-valued assumption overrides (planning §1, issue #70/#71).

Structured defaults are edited as plain text — one ``key = value``
line per figure, dotted keys for nested tables — so every shipped
default is overridable in place without a bespoke editor per table
shape. Serialising and parsing are exact inverses over the value
shapes the catalogue holds (planning §7): ``Decimal`` and ``int``
figures, string tags, and nested tables. Content validation is not
this module's job — the per-key policy parsers vet a parsed table
before it can reach the assumption set (:mod:`glidepath.app.plan`).
"""

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from glidepath.regions.uk import AssumptionValue

_INT_PATTERN = re.compile(r"[+-]?\d+")


def table_edit_text(value: Mapping[str, object]) -> str:
    """The table as editable text: one ``key = value`` line per figure.

    Nested tables flatten to dotted keys, so the text round-trips
    through :func:`parse_table_text` for every shipped table shape.
    """
    lines: list[str] = []
    _serialise(lines, value, prefix="")
    return "\n".join(lines)


def _serialise(lines: list[str], table: Mapping[str, object], prefix: str) -> None:
    """Append one dotted-key line per leaf of ``table`` to ``lines``."""
    for key, entry in table.items():
        path = f"{prefix}{key}"
        if isinstance(entry, Mapping):
            _serialise(lines, entry, f"{path}.")
        else:
            lines.append(f"{path} = {_leaf_text(entry)}")


def _leaf_text(entry: object) -> str:
    """One leaf value as text that re-parses to an equal figure.

    An integral ``Decimal`` (e.g. a hand-edited plan's ``1``) would
    otherwise serialise to bare-integer text and re-parse as ``int``,
    which the policy parsers reject — so it gains a fractional digit,
    keeping numeric equality.
    """
    text = str(entry)
    if isinstance(entry, Decimal) and _INT_PATTERN.fullmatch(text):
        return f"{text}.0"
    return text


def parse_table_text(text: str) -> dict[str, AssumptionValue]:
    """Parse ``key = value`` lines back into a nested table.

    Whole numbers parse as ``int``, other numerics as ``Decimal``, and
    anything else as a string tag — the same convention the catalogue's
    TOML loader applies, so a serialised default parses back exactly.
    Blank lines are ignored.

    Raises:
        ValueError: On a line without ``=``, a missing value, an empty
            key segment, or a duplicate/conflicting key path.
    """
    table: dict[str, AssumptionValue] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key_text, separator, value_text = stripped.partition("=")
        if not separator:
            msg = f"line {stripped!r}: expected 'key = value'"
            raise ValueError(msg)
        raw_value = value_text.strip()
        if not raw_value:
            msg = f"line {stripped!r}: missing value"
            raise ValueError(msg)
        path = [segment.strip() for segment in key_text.strip().split(".")]
        if not all(path):
            msg = f"line {stripped!r}: empty key"
            raise ValueError(msg)
        _assign(table, path, _parsed_scalar(raw_value))
    return table


def _assign(
    table: dict[str, AssumptionValue], path: list[str], value: AssumptionValue
) -> None:
    """Set ``value`` at the dotted ``path`` in ``table``, refusing conflicts.

    Raises:
        ValueError: If the path duplicates an earlier line or mixes a
            figure with a nested table at the same key.
    """
    node = table
    for depth, segment in enumerate(path[:-1], start=1):
        child = node.setdefault(segment, {})
        if not isinstance(child, dict):
            # A user-text conflict, not a Python typing error, so the
            # message must travel the ValueError rejection channel.
            msg = f"key {'.'.join(path[:depth])!r} holds both a value and a table"
            raise ValueError(msg)  # noqa: TRY004
        node = child
    leaf = path[-1]
    if leaf in node:
        msg = f"duplicate key {'.'.join(path)!r}"
        raise ValueError(msg)
    node[leaf] = value


def _parsed_scalar(raw: str) -> AssumptionValue:
    """The typed value one line's text denotes.

    Non-finite numerics (``Infinity``, ``NaN``) fall through as string
    tags — the policy parsers reject them by type, so they can never
    enter a table as figures.
    """
    if _INT_PATTERN.fullmatch(raw):
        return int(raw)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite():
        return raw
    return value


__all__ = [
    "parse_table_text",
    "table_edit_text",
]
