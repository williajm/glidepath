# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately via GitHub's
[private vulnerability reporting](https://github.com/williajm/glidepath/security/advisories/new)
— please do not open a public issue. Expect an initial response within
a week.

## Supported versions

Only the latest released 0.x version receives fixes.

## Scope

glidepath is a local desktop application: it transmits nothing and
runs no network services. The security-relevant surfaces are the plan
file parser (`.glidepath.json`), the shipped region data files, and
the supply chain.

## Supply-chain measures

- Dependencies are locked with hash verification against PyPI only; no
  dependency may be locked to a version published within the last
  7 days (see `CLAUDE.md` for the full policy).
- GitHub Actions are pinned to commit SHAs; Dependabot proposes
  updates with the same 7-day cooldown.
- Releases are built and smoke-tested in CI, then published to PyPI
  via trusted publishing (OIDC, no stored credentials) with PEP 740
  attestations.
- The lockfile is audited for known CVEs (`pip-audit`) on every CI
  run.
