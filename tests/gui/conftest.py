"""Shared Qt fixtures for the GUI smoke tests (§4.7).

The offscreen QPA platform is selected at import time — before
pytest-qt's session-scoped ``qapp`` creates the singleton
QApplication — so the suite runs headless on both CI and
workstations.
"""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QEvent

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtWidgets import QApplication

GUI_TESTS_DIR = Path(__file__).resolve().parent

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    # The offscreen platform's freetype font database does not scan
    # the Windows system fonts, so text renders as replacement-glyph
    # boxes and the exported PDF embeds no extractable text at all;
    # pointing it at the system directory restores real text.
    _fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if _fonts.is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", str(_fonts))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test collected under this directory as ``gui``.

    The marker is applied here rather than per-module so new GUI test
    files cannot forget it; ``-m "not gui"`` then deselects the whole
    offscreen-Qt suite locally or shards it in CI.
    """
    for item in items:
        if item.path.is_relative_to(GUI_TESTS_DIR):
            item.add_marker(pytest.mark.gui)


@pytest.fixture(autouse=True)
def _reap_top_level_widgets(qapp: QApplication) -> Iterator[None]:
    """Destroy the widgets each test leaves behind.

    Depending on pytest-qt's session ``qapp`` also guarantees the
    QApplication exists for every GUI test, including the many that
    construct widgets directly rather than through ``qtbot``. Tests
    never close their windows and dialogs, so they accumulate on the
    session QApplication — and every app-wide restyle (each
    ``apply_theme`` or ``MainWindow`` construction) repolishes all of
    them, growing from milliseconds to many seconds per test by the
    end of the suite.
    """
    yield
    for widget in qapp.topLevelWidgets():
        widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
