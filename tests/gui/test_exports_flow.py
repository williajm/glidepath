"""Export flow tests, offscreen (roadmap 9.19; planning §4.7).

The shell is thin by policy: the File menu's export actions route
through the app layer's builders, the shell contributing only the
dialog, the chart images, and the PDF paint device.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING

from PySide6.QtGui import QPdfWriter
from PySide6.QtPdf import QPdfDocument

from glidepath.app import (
    DISCLAIMER_BODY,
    NOTHING_TO_EXPORT_MESSAGE,
    REPORT_NOT_WRITTEN_MESSAGE,
    build_shell_view_model,
)
from glidepath.gui import widgets
from glidepath.gui.widgets import MainWindow

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _window_with_example() -> MainWindow:
    """A window whose launch example is already submitted and projected."""
    return MainWindow(build_shell_view_model())


def _pdf_text(path: Path) -> str:
    """Every page's extracted text, whitespace-collapsed to single spaces.

    Qt wraps paragraphs into lines at print time, so assertions on the
    extracted text must not depend on where the line breaks fell.
    """
    document = QPdfDocument()
    error = document.load(str(path))
    assert error == QPdfDocument.Error.None_
    assert document.pageCount() >= 1
    pages = [document.getAllText(index).text() for index in range(document.pageCount())]
    document.close()
    return " ".join(" ".join(pages).split())


class TestExportMenu:
    """The File menu offers both exports (the 9.19 acceptance)."""

    def test_file_menu_offers_both_exports(self) -> None:
        """Both export actions appear with the app layer's labels."""
        window = _window_with_example()
        menu = build_shell_view_model().file_menu
        assert window.export_cash_flow_action.text() == menu.export_cash_flow_label
        assert window.export_report_action.text() == menu.export_report_label


class TestExportCashFlowFlow:
    """The CSV export writes through the app layer."""

    def test_export_writes_the_csv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exported file carries the header block and the table."""
        target = tmp_path / "cash-flow.csv"
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(target), "")),
        )
        window.export_cash_flow_dialog()
        assert target.exists()
        text = target.read_text(encoding="utf-8")
        assert DISCLAIMER_BODY in text
        assert "Unsaved plan" in text
        assert str(target) in window.statusBar().currentMessage()

    def test_export_appends_the_csv_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare filename gains the .csv suffix."""
        window = _window_with_example()
        bare = tmp_path / "cash-flow"
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(bare), "")),
        )
        window.export_cash_flow_dialog()
        assert (tmp_path / "cash-flow.csv").exists()

    def test_cancelled_dialog_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancelling the export dialog leaves the disk untouched."""
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: ("", "")),
        )
        window.export_cash_flow_dialog()
        assert list(tmp_path.iterdir()) == []

    def test_cleared_session_reports_nothing_to_export(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a projection the export declines with the copy."""
        target = tmp_path / "cash-flow.csv"
        window = _window_with_example()
        window.facts_pane.clear_button.click()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(target), "")),
        )
        window.export_cash_flow_dialog()
        assert not target.exists()
        assert window.statusBar().currentMessage() == NOTHING_TO_EXPORT_MESSAGE


class TestExportReportFlow:
    """The report export prints the app layer's document to a PDF."""

    def test_export_writes_the_pdf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exported file is a real PDF and the status names it."""
        target = tmp_path / "report.pdf"
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(target), "")),
        )
        window.export_report_dialog()
        assert target.exists()
        assert target.read_bytes().startswith(b"%PDF")
        assert str(target) in window.statusBar().currentMessage()
        assert not (tmp_path / "report.pdf.part").exists()

    def test_export_appends_the_pdf_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare filename gains the .pdf suffix."""
        window = _window_with_example()
        bare = tmp_path / "report"
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(bare), "")),
        )
        window.export_report_dialog()
        assert (tmp_path / "report.pdf").exists()

    def test_cleared_session_reports_nothing_to_export(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a projection the report declines with the copy."""
        target = tmp_path / "report.pdf"
        window = _window_with_example()
        window.facts_pane.clear_button.click()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(target), "")),
        )
        window.export_report_dialog()
        assert not target.exists()
        assert window.statusBar().currentMessage() == NOTHING_TO_EXPORT_MESSAGE

    def test_pdf_text_carries_disclaimer_and_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rendered pages carry the report's substance (issue #133).

        The write test above only proves a PDF file exists; extracting
        the text Qt actually laid out catches a report that renders
        blank, drops the disclaimer, or loses a section.
        """
        target = tmp_path / "report.pdf"
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(target), "")),
        )
        window.export_report_dialog()
        text = _pdf_text(target)
        assert " ".join(DISCLAIMER_BODY.split()) in text
        assert "Inputs and provenance" in text
        assert "Projection results" in text
        assert "Unsaved plan" in text

    def test_failed_device_keeps_the_stale_file_and_reports_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A device that writes nothing can never report success.

        A PDF device whose file cannot be opened only logs a Qt
        warning; over-writing an existing export must then report the
        failure and leave the stale file alone, never claim success
        because the old bytes still exist.
        """
        target = tmp_path / "report.pdf"
        target.write_bytes(b"stale")
        window = _window_with_example()

        class NullWriter(QPdfWriter):
            """A device aimed elsewhere, as a failed file open behaves."""

            def __init__(self, _path: str) -> None:
                super().__init__(str(tmp_path / "elsewhere.pdf"))

        monkeypatch.setattr(widgets, "QPdfWriter", NullWriter)
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(target), "")),
        )
        window.export_report_dialog()
        assert target.read_bytes() == b"stale"
        assert window.statusBar().currentMessage() == REPORT_NOT_WRITTEN_MESSAGE
        assert not (tmp_path / "report.pdf.part").exists()
