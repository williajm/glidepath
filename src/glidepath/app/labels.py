"""Shared display text for provenance labels and entity ids (§4.7).

The provenance grammar (``person[<id>].field.path``, see
:func:`~glidepath.core.collect_plan_decisions`) addresses entities by
stable id. These helpers turn those labels into copy — ids named for
humans — for every screen that shows labelled facts, decisions, or
scenario overrides (the inspector and the scenario manager).
"""

import re
from collections import Counter
from typing import TYPE_CHECKING

from glidepath.app.display import format_wrapper_kind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from glidepath.core import Household

_LABEL_PATTERN = re.compile(r"^(?P<kind>[a-z_]+)\[(?P<id>[^\]]+)\]\.(?P<path>.+)$")


def capitalised(text: str) -> str:
    """The text with just its first letter upper-cased."""
    return text[:1].upper() + text[1:]


def pretty_path(path: str) -> str:
    """A dotted field path as display text."""
    return " / ".join(segment.replace("_", " ") for segment in path.split("."))


def numbered_unique[K](bases: Mapping[K, str]) -> dict[K, str]:
    """The base names, numbered in order wherever one repeats (9.28).

    User labels made name collisions possible — a wrapper named "ISA"
    beside an unnamed ISA, or two wrappers given one name — and an
    ambiguous name defeats its purpose, so every surface naming
    wrappers runs its final names through this: a unique base stands
    as-is, repeats read "base 1", "base 2" in the mapping's order.
    """
    counts = Counter(bases.values())
    numbered: Counter[str] = Counter()
    names: dict[K, str] = {}
    for key, base in bases.items():
        if counts[base] == 1:
            names[key] = base
        else:
            numbered[base] += 1
            names[key] = f"{base} {numbered[base]}"
    return names


def entity_names(household: Household | None) -> dict[str, str]:
    """Friendly names for the entity ids provenance labels address."""
    if household is None:
        return {}
    wrapper_bases: dict[str, str] = {}
    names: dict[str, str] = {}
    for person in household.persons:
        names[str(person.id)] = "You"
        for number, wrapper in enumerate(person.wrappers, start=1):
            kind = format_wrapper_kind(wrapper.kind)
            wrapper_bases[str(wrapper.id)] = (
                wrapper.label or f"Wrapper {number} ({kind})"
            )
        for number, pension in enumerate(person.db_pensions, start=1):
            names[str(pension.id)] = f"DB pension {number}"
        for number, purchase in enumerate(person.annuity_purchases, start=1):
            names[str(purchase.id)] = f"Annuity purchase {number}"
    names.update(numbered_unique(wrapper_bases))
    for outflow in household.planned_outflows:
        names[str(outflow.id)] = outflow.label
    return names


def display_label(label: str, names: Mapping[str, str]) -> str:
    """A provenance label as display text, entity ids named for humans."""
    match = _LABEL_PATTERN.match(label)
    if match is None:
        return capitalised(pretty_path(label))
    name = names.get(match["id"], capitalised(match["kind"].replace("_", " ")))
    return f"{name} — {pretty_path(match['path'])}"


__all__ = [
    "capitalised",
    "display_label",
    "entity_names",
    "numbered_unique",
    "pretty_path",
]
