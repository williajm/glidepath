# Canonical developer commands for glidepath. See CLAUDE.md.

.DEFAULT_GOAL := help
.PHONY: help check fix test deps bump audit sonar hooks binary

BINARY_MODE ?= standalone

# Per-machine venv naming: Windows and WSL share this checkout, so each
# platform gets its own environment (see CLAUDE.md).
ifeq ($(OS),Windows_NT)
export UV_PROJECT_ENVIRONMENT := .venv-win
# The uv cache (C:) and this checkout (E:) are on different drives, so
# hardlinking is impossible; copy silently instead of warning every run.
export UV_LINK_MODE := copy
else
export UV_PROJECT_ENVIRONMENT := .venv-wsl
endif

# uv environment variables take precedence over pyproject.toml, so a
# user-wide UV_EXCLUDE_NEWER (a sensible shell default for other projects)
# would silently replace this repo's absolute exclude-newer cutoff with a
# relative one: `uv lock` then records an unverifiable placeholder that
# scripts/check_dep_age.py rejects, and every `uv run --locked` reports
# the lockfile as stale. The repo enforces its own cooldown, so recipes
# never see the ambient value. Mirrored in .pre-commit-config.yaml.
unexport UV_EXCLUDE_NEWER

help:
	@echo "make sync   - install the locked dependencies into the platform venv"
	@echo "make check  - run all merge gates (ruff, mypy, pytest+coverage, dep age)"
	@echo "make fix    - auto-fix lint issues and format"
	@echo "make test   - run tests with coverage"
	@echo "make deps   - upgrade/lock dependencies with the 7-day cooldown, sync, verify"
	@echo "make bump   - set the release version (V=X.Y.Z), minimally re-lock, verify"
	@echo "make audit  - pip-audit the lockfile for known CVEs"
	@echo "make sonar  - run tests then a local SonarQube scan (needs sonar-scanner)"
	@echo "make hooks  - install pre-commit hooks"
	@echo "make binary - compile with Nuitka (BINARY_MODE=standalone or onefile)"

# All non-dependency commands use `uv run --locked` so they can never
# silently re-resolve the lockfile — only `make deps` may change it.
sync:
	uv sync --locked

binary:
	uv run --locked --group binary python scripts/build_binary.py --mode $(BINARY_MODE)

check:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mypy
	uv run --locked pytest
	uv run --locked python scripts/check_dep_age.py

fix:
	uv run --locked ruff check --fix .
	uv run --locked ruff format .

test:
	uv run --locked pytest

# The ONLY sanctioned way to add or upgrade dependencies (see CLAUDE.md).
# Bumps the exclude-newer cooldown cutoff in pyproject.toml, re-resolves
# everything against it (--upgrade, or existing pins are kept forever),
# syncs, then independently verifies artifact upload ages via the PyPI API.
deps:
	uv run --no-project python scripts/update_exclude_newer.py
	uv lock --upgrade
	uv sync --locked
	uv run --locked python scripts/check_dep_age.py

# Sanctioned version bump for releases (planning §4.10). uv.lock embeds the
# project version, so a bump needs a re-lock — but a MINIMAL one: no
# --upgrade, so every existing pin is kept, the exclude-newer cooldown
# cutoff still applies to anything newly resolved, and the age check
# re-verifies the whole lockfile afterwards.
bump:
	uv run --no-project python scripts/bump_version.py $(V)
	uv lock
	uv sync --locked
	uv run --locked python scripts/check_dep_age.py

audit:
	uv export --frozen --all-groups --no-emit-project --output-file requirements-audit.txt
	uv run --locked pip-audit --disable-pip --requirement requirements-audit.txt

sonar:
	uv run --locked pytest
	sonar-scanner

hooks:
	uv run --locked pre-commit install
