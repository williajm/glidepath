# glidepath

Desktop retirement/investment planner in Python. UK-first: all UK-specific
logic (tax rules, ISA/SIPP wrappers, state pension) lives in isolated region
modules (under `glidepath/regions/uk/`) so the core engine stays
region-agnostic and other regions can be added later. The GUI is a thin
PySide6 shell (`glidepath/gui/`) over the UI-agnostic app layer
(`glidepath/app/`, planning §4.7); launch it with `uv run glidepath`.

## Canonical commands

- `make sync` — install the locked dependencies into the platform venv.
- `make check` — all merge gates: ruff check, ruff format --check,
  mypy --strict, pytest with coverage (fail under 96%), dependency age check.
- `make fix` — ruff auto-fix + format.
- `make test` — tests with coverage.
- `make deps` — the ONLY sanctioned way to add or upgrade dependencies.
- `make bump V=X.Y.Z` — the ONLY sanctioned way to set the release version
  (rewrites `[project] version`, minimal re-lock, re-verifies ages).
- `make audit` — pip-audit the lockfile for known CVEs.
- `make sonar` — tests + local SonarQube scan.
- `make hooks` — install pre-commit hooks.

## Supply-chain policy (permanent, all sessions)

No dependency — direct or transitive — may be locked to a version published
to PyPI within the last 7 days (cooldown against freshly compromised
releases). Rules:

- Add/upgrade dependencies ONLY via `make deps`, which bumps the
  `exclude-newer` cutoff in `pyproject.toml` to UTC now minus 7 days, runs
  `uv lock --upgrade`, syncs, and then runs `scripts/check_dep_age.py` to
  verify. Never run bare `uv add`, `uv lock`, or `uv lock --upgrade`.
- One exception: `make bump V=X.Y.Z` (release version bump) runs a bare
  `uv lock` because uv.lock embeds the project version. It is a minimal
  re-lock — no `--upgrade`, existing pins kept, the `exclude-newer` cutoff
  still applies — and it re-runs `scripts/check_dep_age.py` afterwards.
- All other commands (make targets, pre-commit hooks) use
  `uv run --locked`, so nothing outside `make deps` and `make bump` can
  rewrite the lockfile.
- `scripts/check_dep_age.py` independently verifies the lockfile: the
  embedded cutoff must be ≥7 days old right now, every package must come
  from PyPI (root project excepted), and every locked wheel/sdist's PyPI
  upload time must predate the cutoff. It runs in CI — a violating
  lockfile cannot merge.
- PyPI is the only permitted package index; uv hash verification stays on.
- The uv binary itself follows the same cooldown: install/update it only to
  releases at least 7 days old (CI pins the exact version in `ci.yml`).
- GitHub Actions are pinned to full commit SHAs, never floating tags.
  Dependabot (`.github/dependabot.yml`) is security-only: it alerts on
  action security advisories but proposes no routine version updates —
  pins are refreshed by hand, honouring the 7-day cooldown.
- The runtime dependency in `[project] dependencies` is pinned exactly
  (`pyside6==X.Y.Z`, moved only by `make deps`): end users installing
  from PyPI resolve fresh, so a `>=` range would bypass the lockfile
  and the cooldown entirely (planning §4.10).

## Quality bar (enforced by pre-commit + CI, not optional)

- Ruff with `lint.select = ["ALL"]`; every ignore needs a one-line
  justification in `pyproject.toml`. Prefer fixing code over adding ignores.
- mypy `--strict`; fully typed code, no untyped `def`s.
- pytest coverage on `src/glidepath` with `fail_under = 96`;
  `coverage.xml` is emitted for SonarQube.
- SonarQube quality gate runs in CI when `SONAR_TOKEN` is configured.
- Pre-commit hooks are required locally (`make hooks`); ruff/mypy run via
  `uv run` so they always match the locked versions.

## Documentation & design (read before feature work)

- `docs/planning.md` is the single planning document and the source of
  truth for scope, design decisions, verified UK figures, default
  assumptions, and the phased roadmap; implementation issues are raised
  from its roadmap section. Update it when any of those change.
- FACTS vs ASSUMPTIONS vs DECISIONS is a load-bearing product principle:
  user-entered data is `Fact[T]`; estimated/defaulted inputs are
  `Assumption[T]` with value, source, date recorded, and
  default/overridden provenance; user *choices* (retirement age,
  contributions, planned outflows) are `Decision[T]` — the only
  scenario-overridable plan fields (see the "Domain model" section of
  `docs/planning.md`).
- UK policy figures (tax bands, allowances, state pension rates, age rules)
  are NEVER hardcoded — they live in TOML data files under
  `src/glidepath/regions/uk/data/` with `verified_on` + `sources` (see the
  "UK region data files" section of `docs/planning.md`). A guard test
  enforces this.
- Product disclaimer: glidepath is a personal modelling tool, not regulated
  financial advice; the disclaimer must be preserved in the UI, exports,
  and README.

## Release process (details in planning §4.10)

- SemVer 0.x; the version lives in `[project] version`, released as a
  `vX.Y.Z` tag on `main`. Releases are tag + GitHub Release + PyPI
  (sdist/wheel via trusted publishing) — no binary artifacts yet
  (§4.10 records why).
- `CHANGELOG.md` (Keep a Changelog format) is curated in the release PR;
  the tagged version's section becomes the GitHub Release notes.
- To cut a release, on an up-to-date `dev`: `make bump V=X.Y.Z`, move the
  Unreleased items into a dated `## [X.Y.Z]` section, `make check`, PR to
  `main`. After the merge, tag the merge commit `vX.Y.Z` and push the
  tag; `release.yml` validates it (tag on main, version match, changelog
  section present), builds and smoke-tests the sdist/wheel, publishes
  them to PyPI with PEP 740 attestations once the `pypi` environment
  deployment is manually approved (GitHub → the run's review prompt),
  then creates the GitHub Release with the artifacts attached.

## Coding conventions

- Money is `Decimal`, never float.
- Datetimes are always timezone-aware (ruff `DTZ` enforces this).
- Region-specific logic is isolated under `glidepath/regions/`; nothing
  UK-specific may leak into the core engine.
- src layout: code in `src/glidepath/`, tests in `tests/`, dev scripts in
  `scripts/`. All tool config lives in `pyproject.toml`.

## Environment notes

- Python pinned in `.python-version` (3.14.6 at setup); uv manages the
  toolchain. No pip/poetry/conda for project work.
- This checkout is shared between Windows and WSL: the venv is `.venv-win`
  on Windows and `.venv-wsl` on WSL so the two platforms don't fight over
  one directory. The Makefile sets `UV_PROJECT_ENVIRONMENT` accordingly,
  and it is also set user-wide (Windows user env var; `~/.bashrc` export in
  WSL) so bare `uv run` and pre-commit hooks use the same venv.
