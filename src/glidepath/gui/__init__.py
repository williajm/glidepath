"""PySide6 desktop shell (roadmap 8.1; planning §4.7).

Thin by policy: widgets bind view models from ``glidepath.app`` and
forward user actions back. No domain logic, no formatting, no copy —
those live in the app layer so a future web shell can reuse them.
"""
