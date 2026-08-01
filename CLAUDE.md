# glidepath

Desktop retirement/investment planner in Python. UK-first: all UK-specific
logic (tax rules, ISA/SIPP wrappers, state pension) lives in isolated region
modules (e.g. `glidepath/regions/uk.py`) so the core engine stays
region-agnostic and other regions can be added later. GUI will be PySide6
(verified 2026-08-01: PySide6 6.11.1 supports Python 3.14, `<3.15`).

## Canonical commands

- `make check` — all merge gates: ruff check, ruff format --check,
  mypy --strict, pytest with coverage (fail under 90%), dependency age check.
- `make fix` — ruff auto-fix + format.
- `make deps` — the ONLY sanctioned way to add or upgrade dependencies.
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
- All other commands (make targets, pre-commit hooks) use
  `uv run --locked`, so nothing outside `make deps` can rewrite the
  lockfile.
- `scripts/check_dep_age.py` independently verifies the lockfile: the
  embedded cutoff must be ≥7 days old right now, every package must come
  from PyPI (root project excepted), and every locked wheel/sdist's PyPI
  upload time must predate the cutoff. It runs in CI — a violating
  lockfile cannot merge.
- PyPI is the only permitted package index; uv hash verification stays on.
- The uv binary itself follows the same cooldown: install/update it only to
  releases at least 7 days old (CI pins the exact version in `ci.yml`).
- GitHub Actions are pinned to full commit SHAs, never floating tags.

## Quality bar (enforced by pre-commit + CI, not optional)

- Ruff with `lint.select = ["ALL"]`; every ignore needs a one-line
  justification in `pyproject.toml`. Prefer fixing code over adding ignores.
- mypy `--strict`; fully typed code, no untyped `def`s.
- pytest coverage on `src/glidepath` with `fail_under = 90`;
  `coverage.xml` is emitted for SonarQube.
- SonarQube quality gate runs in CI when `SONAR_TOKEN` is configured.
- Pre-commit hooks are required locally (`make hooks`); ruff/mypy run via
  `uv run` so they always match the locked versions.

## Documentation & design (read before feature work)

- `docs/planning.md` is the single planning document and the source of
  truth for scope, design decisions, verified UK figures, default
  assumptions, and the phased roadmap; implementation issues are raised
  from its roadmap section. Update it when any of those change.
- FACTS vs ASSUMPTIONS is a load-bearing product principle: user-entered
  data is `Fact[T]`; every other input is `Assumption[T]` with value,
  source, date recorded, and default/overridden provenance
  (`docs/planning.md` §5.1).
- UK policy figures (tax bands, allowances, state pension rates, age rules)
  are NEVER hardcoded — they live in TOML data files under
  `src/glidepath/regions/uk/data/` with `verified_on` + `sources`
  (`docs/planning.md` §5.3). A guard test enforces this.
- Product disclaimer: glidepath is a personal modelling tool, not regulated
  financial advice; the disclaimer must be preserved in the UI, exports,
  and README.

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
