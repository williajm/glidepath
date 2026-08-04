"""Shared Qt fixtures for the GUI smoke tests (§4.7).

The offscreen QPA platform is selected before the singleton
QApplication is created, so the suite runs headless on both CI and
workstations.
"""

import os
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, scope="session")
def qt_app() -> QApplication:
    """The process-wide QApplication, created on the offscreen platform."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if existing is None:
        return QApplication([])
    assert isinstance(existing, QApplication)
    return existing


@pytest.fixture(autouse=True)
def _reap_top_level_widgets(qt_app: QApplication) -> Iterator[None]:
    """Destroy the widgets each test leaves behind.

    Tests never close their windows and dialogs, so they accumulate on
    the session QApplication — and every app-wide restyle (each
    ``apply_theme`` or ``MainWindow`` construction) repolishes all of
    them, growing from milliseconds to many seconds per test by the end
    of the suite.
    """
    yield
    for widget in qt_app.topLevelWidgets():
        widget.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
