"""Canonical writer for ``.glidepath.json`` (roadmap 6.2; planning §4.5).

Deterministic output for clean diffs: ``schema_version`` stamped in,
keys sorted, 2-space indent, LF line endings, ``Decimal`` as strings,
ISO-8601 timezone-aware datetimes. A given document always serializes
to the same bytes and a load→save round trip is byte-stable; value
representations are preserved exactly (a ``Decimal`` keeps the
exponent the user stated, a datetime its offset), so two values that
compare equal but were written differently keep their distinct
spellings. Only the user's assumption *overrides* are written;
defaults re-resolve on load against shipped region data.

The writer never produces a file the reader rejects: serialization
goes through the same validating wire models the reader uses
(:mod:`glidepath.persistence.models`), so everything the strict
reader refuses that the domain model does not already rule out
(non-finite decimals, booleans in whole-number fields, empty entity
ids) is rejected here too — by construction, not by parallel checks.
"""

import contextlib
import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from glidepath.persistence.document import PersistenceError, PlanDocument
from glidepath.persistence.models import WireDocument, document_error_message

if TYPE_CHECKING:
    from pathlib import Path


def dumps_plan(document: PlanDocument) -> str:
    """The document as canonical ``.glidepath.json`` text (planning §4.5).

    Raises:
        PersistenceError: If an override value's type is outside the
            persistable vocabulary.
    """
    try:
        wire = WireDocument.from_domain(document)
    except ValidationError as error:
        raise PersistenceError(document_error_message(error)) from error
    payload = wire.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_plan(document: PlanDocument, path: Path) -> None:
    """Write the document to ``path`` in canonical form.

    Serialization completes before any file is opened, so a document
    that cannot be encoded never touches the disk; the text is then
    written to a sibling temporary file and moved over ``path`` only
    once the write has closed cleanly, so a mid-write failure (disk
    full, power loss) can never truncate the last saved plan.
    ``newline=""`` disables platform newline translation so the file
    carries LF endings on every platform (planning §4.5).
    """
    text = dumps_plan(document)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        temp.replace(path)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
