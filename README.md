# glidepath

[![CI](https://github.com/williajm/glidepath/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/williajm/glidepath/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=williajm_glidepath&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=williajm_glidepath)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=williajm_glidepath&metric=coverage)](https://sonarcloud.io/summary/new_code?id=williajm_glidepath)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue)](https://github.com/williajm/glidepath/blob/main/.python-version)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/github/license/williajm/glidepath)](LICENSE)

A desktop retirement and investment planner. UK-first, built so other
regions can be added later. Early scaffolding — no app features yet.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and GNU Make.

```sh
make sync          # creates the platform venv and installs locked dependencies
make hooks         # installs the pre-commit hooks (required)
```

One-time per machine: set `UV_PROJECT_ENVIRONMENT` user-wide (`.venv-win`
on Windows, `.venv-wsl` in WSL) so bare `uv` commands and the git hooks use
the same venv as make. See `CLAUDE.md`.

## Everyday commands

```sh
make check   # all merge gates: ruff, format, mypy --strict, pytest (>=90% cov), dep age
make fix     # auto-fix lint issues and reformat
make test    # tests with coverage
make deps    # the ONLY way to add/upgrade dependencies (7-day cooldown lock)
make audit   # pip-audit the lockfile for known CVEs
```

Dependencies are never added with plain `uv add`/`uv lock`: run `make deps`
so the 7-day supply-chain cooldown is applied. CI runs every `make check`
gate plus `make audit` and the SonarCloud quality gate on each PR — so a
clean `make check` locally does not quite guarantee a green pipeline. See
`CLAUDE.md` for the full policies.
