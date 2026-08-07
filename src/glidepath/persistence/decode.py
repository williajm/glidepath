"""Strict reader for ``.glidepath.json`` (roadmap 6.2; planning §4.5).

Everything fails loudly with a document path in the message: unknown
keys, missing keys, mistyped values, JSON floats (money is ``Decimal``,
never float — planning §4.6), and any entity invariant violated by the
stored data. The schema itself lives in the pydantic wire models
(:mod:`glidepath.persistence.models`); this module owns the JSON
parsing (with the document-wide float ban), the migration step, and
the translation of validation failures into :class:`PersistenceError`.
Migration (roadmap 6.4) runs before decoding, so the wire models only
ever read the current schema version.
"""

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from glidepath.persistence.document import PersistenceError, PlanDocument
from glidepath.persistence.migrations import apply_migrations
from glidepath.persistence.models import (
    WIRE_CONTEXT,
    WireDocument,
    document_error_message,
)

if TYPE_CHECKING:
    from pathlib import Path


def loads_plan(text: str) -> PlanDocument:
    """Parse canonical ``.glidepath.json`` text into a document.

    Older schema versions are upgraded through the migration harness
    (roadmap 6.4) before strict decoding.

    Raises:
        PersistenceError: If the text is not valid JSON, needs a
            migration this build lacks, or violates the schema.
    """
    raw = _parse_json(text)
    return _document(apply_migrations(raw))


def load_plan(path: Path) -> PlanDocument:
    """Read and parse the plan document stored at ``path``.

    Raises:
        PersistenceError: As :func:`loads_plan`, or if the file is not
            UTF-8 text (a defective document, same as invalid JSON).
        OSError: If the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        msg = "plan file is not UTF-8 text"
        raise PersistenceError(msg) from exc
    return loads_plan(text)


def _reject_float(text: str) -> object:
    """Refuse JSON floats: money and rates travel as strings (§4.6)."""
    msg = f"JSON floats are not permitted in a plan document, got {text!r}"
    raise PersistenceError(msg)


def _parse_json(text: str) -> dict[str, object]:
    """Parse the raw JSON text into a top-level object."""
    try:
        raw: object = json.loads(text, parse_float=_reject_float)
    except json.JSONDecodeError as error:
        msg = f"not valid JSON: {error}"
        raise PersistenceError(msg) from error
    if not isinstance(raw, dict):
        msg = f"document root must be an object, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return raw


def _document(raw: dict[str, object]) -> PlanDocument:
    """Decode the whole migrated document through the wire models."""
    try:
        wire = WireDocument.model_validate(raw, context=WIRE_CONTEXT)
    except ValidationError as error:
        raise PersistenceError(document_error_message(error)) from error
    return wire.domain
