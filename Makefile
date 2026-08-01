# Canonical developer commands for glidepath. See CLAUDE.md.

.DEFAULT_GOAL := help
.PHONY: help check fix test deps audit sonar hooks

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

help:
	@echo "make sync   - install the locked dependencies into the platform venv"
	@echo "make check  - run all merge gates (ruff, mypy, pytest+coverage, dep age)"
	@echo "make fix    - auto-fix lint issues and format"
	@echo "make test   - run tests with coverage"
	@echo "make deps   - upgrade/lock dependencies with the 7-day cooldown, sync, verify"
	@echo "make audit  - pip-audit the lockfile for known CVEs"
	@echo "make sonar  - run tests then a local SonarQube scan (needs sonar-scanner)"
	@echo "make hooks  - install pre-commit hooks"

# All non-dependency commands use `uv run --locked` so they can never
# silently re-resolve the lockfile — only `make deps` may change it.
sync:
	uv sync --locked

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

audit:
	uv export --frozen --no-emit-project --output-file requirements-audit.txt
	uv run --locked pip-audit --disable-pip --requirement requirements-audit.txt

sonar:
	uv run --locked pytest
	sonar-scanner

hooks:
	uv run --locked pre-commit install
