"""Shared Qt fixtures for the GUI smoke tests (§4.7).

The offscreen QPA platform is selected before the singleton
QApplication is created, so the suite runs headless on both CI and
workstations.
"""

import os

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(autouse=True, scope="session")
def qt_app() -> QApplication:
    """The process-wide QApplication, created on the offscreen platform."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if existing is None:
        return QApplication([])
    assert isinstance(existing, QApplication)
    return existing
