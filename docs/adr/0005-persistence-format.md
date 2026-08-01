# ADR-0005: Persistence format

> Status: **Proposed** · 2026-08-01 · [Index](README.md)

## Context

User data (plan, scenarios) must persist locally in a human-readable format
— privacy is a product guarantee: **everything stays on the user's machine,
nothing is transmitted**. Separately, the UK region ships read-only policy
data (tax-year figures, default assumptions). The project currently has zero
runtime dependencies and wants to keep it that way; money is `Decimal`.

## Decision

Two formats for two jobs:

1. **User data — JSON** (stdlib `json`): one document per plan, extension
   `.glidepath.json`, containing the base plan (facts), assumption
   *overrides* only, and scenarios ([ADR-0003](0003-scenario-model.md)).
   Canonical serialisation: `schema_version` field, sorted keys, 2-space
   indent, `\n` line endings, `Decimal` as strings, datetimes ISO-8601 with
   offset. Stored wherever the user chooses; never transmitted.
2. **Shipped region data — TOML** (stdlib `tomllib`): per-tax-year figure
   files and default assumptions under `regions/uk/data/`
   ([uk-region.md](../design/uk-region.md)). Read-only at runtime — the app
   never writes TOML, so the stdlib's lack of a TOML writer is irrelevant.
   TOML comments carry source citations inline.

Default assumptions are **not** copied into user files: only overrides are
saved, and defaults re-resolve on load against the shipped data. The file
records which defaults/data version it was last resolved against, so a new
app version's updated defaults propagate visibly unless overridden.

## Rationale

Both jobs stay stdlib-only for read *and* write. JSON round-trips
programmatic writes canonically — deterministic output means clean diffs in
backups/VCS, which is the practical meaning of "human-readable" for a file
the app owns. TOML is the better hand-maintained format for data files that
developers edit and review, with comments for citations. Decimal-as-string
is non-negotiable in both: TOML/JSON floats are binary floats.

## Alternatives considered

- **TOML for user files** — rejected: writing needs a `tomli-w` dependency.
- **YAML** — rejected: dependency plus type-coercion footguns, no gain.
- **SQLite** — rejected: opaque, not diffable, overkill for one document per
  plan.
- **One format for both jobs** — rejected: forces the worse tool onto one
  side for symmetry's sake.

## Consequences

- A versioned schema-migration harness is needed from v1 (even as a no-op)
  so old files always open.
- Storing only overrides means results can change across app versions when
  defaults change — mitigated by recording the resolved-against version and
  surfacing default changes to the user via provenance.
