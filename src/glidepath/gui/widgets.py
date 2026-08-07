"""The shell's widgets: disclaimer, main window, and its tabs (§1, §4.7).

The main window owns the immutable app-layer session state and swaps
it through the pure transitions in :mod:`glidepath.app`; widgets only
render view models and forward raw user input back.
"""

import contextlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QStandardPaths,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QKeySequence, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import (
    BACKTEST_RUNNING_MESSAGE,
    BACKTEST_STALE_MESSAGE,
    DEFAULT_CHART_BASIS,
    DEFAULT_COMPARISON_METRIC_KEY,
    DEFAULT_RUN_MODE,
    DRAWDOWN_RUNNING_MESSAGE,
    DRAWDOWN_STALE_MESSAGE,
    MONTE_CARLO_STALE_MESSAGE,
    NOTHING_TO_EXPORT_MESSAGE,
    REPORT_EXPORT_FAILED_PREFIX,
    REPORT_NOT_WRITTEN_MESSAGE,
    RETIREMENT_RUNNING_MESSAGE,
    RETIREMENT_STALE_MESSAGE,
    UNSAVED_CHANGES_PROMPT,
    UNSAVED_CHANGES_TITLE,
    AboutViewModel,
    DisclaimerViewModel,
    DrawdownRequest,
    FactsFormData,
    HelpGuideViewModel,
    PlanReport,
    PlanState,
    ReportRequest,
    RetirementRequest,
    ShellViewModel,
    basis_from_key,
    build_charts_view_model,
    build_inspector_view_model,
    build_plan_report,
    build_scenarios_view_model,
    chart_resource_name,
    example_facts_form_data,
    export_cash_flow_csv,
    facts_form_data_from_household,
    facts_saved_message,
    format_form_errors,
    has_unsaved_changes,
    initial_plan_state,
    load_plan_state,
    metric_from_key,
    monte_carlo_running_status,
    parse_facts_form,
    plan_display_name,
    plan_entity_ids,
    record_last_plan_path,
    report_exported_message,
    run_mode_from_key,
    save_plan_state,
    state_marked_saved,
    state_with_backtest,
    state_with_drawdown,
    state_with_household,
    state_with_monte_carlo,
    state_with_override,
    state_with_retirement,
    state_with_scenario_added,
    state_with_scenario_override,
    state_without_scenario,
    state_without_scenario_override,
)
from glidepath.gui.charts import ChartsPane, ChartsPaneCallbacks, chart_image
from glidepath.gui.forms import FactsEntryPane
from glidepath.gui.inspector import InspectorPane
from glidepath.gui.scenarios import ScenariosPane, ScenariosPaneCallbacks
from glidepath.gui.style import wordmark_pixmap

if TYPE_CHECKING:
    from collections.abc import Callable

_REPORT_DPI = 96
"""The PDF paint device's resolution.

Matches the CSS reference pixel, so the report HTML's pixel-sized
chart images print at the size the app layer specified.
"""


def _today() -> date:
    """The user's calendar day, as the run and form defaults use it (§4.8)."""
    return datetime.now(tz=UTC).astimezone().date()


class DisclaimerDialog(QDialog):
    """Modal first-run disclaimer; accepting is required to proceed (§1)."""

    def __init__(
        self, view_model: DisclaimerViewModel, parent: QWidget | None = None
    ) -> None:
        """Bind the disclaimer view model to the dialog."""
        super().__init__(parent)
        self.setWindowTitle(view_model.title)
        self.setModal(True)

        body = QLabel(view_model.body, self)
        body.setWordWrap(True)

        buttons = QDialogButtonBox(self)
        self.accept_button = QPushButton(view_model.accept_label, self)
        self.decline_button = QPushButton(view_model.decline_label, self)
        buttons.addButton(self.accept_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.decline_button, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(body)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    """The About box: the wordmark above the disclaimer copy (§1).

    A stock ``QMessageBox`` would put the wordmark beside the text and
    squeeze the disclaimer into a narrow column; stacking them keeps
    the copy readable.
    """

    def __init__(
        self, view_model: AboutViewModel, parent: QWidget | None = None
    ) -> None:
        """Bind the about view model to the dialog."""
        super().__init__(parent)
        self.setWindowTitle(view_model.title)

        self.wordmark_label = QLabel(self)
        self.wordmark_label.setPixmap(
            wordmark_pixmap().scaledToWidth(
                320, Qt.TransformationMode.SmoothTransformation
            )
        )
        self.wordmark_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.body_label = QLabel(view_model.body, self)
        self.body_label.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, self)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.wordmark_label)
        layout.addWidget(self.body_label)
        layout.addWidget(buttons)
        # A wrapped label only wraps against a bound, and its size hint
        # understates the wrapped height — fix the width and take the
        # height from the layout's height-for-width so no line is cut.
        self.setFixedSize(440, layout.totalHeightForWidth(440))


class HelpGuideDialog(QDialog):
    """The how-to-use guide: the intro, then one card per section."""

    def __init__(
        self, view_model: HelpGuideViewModel, parent: QWidget | None = None
    ) -> None:
        """Bind the help guide view model to the dialog."""
        super().__init__(parent)
        self.setWindowTitle(view_model.title)

        self.intro_label = QLabel(view_model.intro, self)
        self.intro_label.setWordWrap(True)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        cards = []
        for section in view_model.sections:
            card = QGroupBox(section.heading, content)
            body = QLabel(section.body, card)
            body.setWordWrap(True)
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(body)
            content_layout.addWidget(card)
            cards.append(card)
        self.section_cards = tuple(cards)
        content_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.intro_label)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self.resize(560, 640)


class _TransitionSignals(QObject):
    """Delivers a finished session state back to the GUI thread."""

    finished = Signal(object)


class _TransitionWorker(QRunnable):
    """Runs a slow state transition off the GUI thread (9.13, 9.14).

    A Monte Carlo run or a retirement-age search projects the plan
    many times over at the recorded per-path cost (planning §4.6) —
    seconds to minutes, far too long to block the event loop. The
    transition itself is pure app-layer code; only the delivery back
    to the window touches Qt, via a queued signal.
    """

    def __init__(self, task: Callable[[], PlanState]) -> None:
        """Hold the pure transition to run and the delivery signal."""
        super().__init__()
        self._task = task
        self.signals = _TransitionSignals()

    def run(self) -> None:
        """Compute the next session state and emit it."""
        self.signals.finished.emit(self._task())


class MainWindow(QMainWindow):
    """The application shell: facts entry, the inspector, and the Help menu."""

    def __init__(
        self, view_model: ShellViewModel, settings_path: Path | None = None
    ) -> None:
        """Bind the shell view model to the window and start a session.

        ``settings_path`` is the per-user settings file recording the
        last plan path for the next launch; ``None`` (tests, embedded
        shells) records nothing.
        """
        super().__init__()
        self._view_model = view_model
        self._about_view_model = view_model.about
        self._settings_path = settings_path
        self._plan_path: Path | None = None
        self._state = initial_plan_state()
        self._charts_basis = DEFAULT_CHART_BASIS
        self._charts_mode = DEFAULT_RUN_MODE
        self._comparison_basis = DEFAULT_CHART_BASIS
        self._comparison_metric = DEFAULT_COMPARISON_METRIC_KEY
        self.monte_carlo_pool = QThreadPool(self)
        self.monte_carlo_pool.setMaxThreadCount(1)
        self._monte_carlo_worker: _TransitionWorker | None = None
        self._monte_carlo_input: PlanState | None = None
        self._retirement_worker: _TransitionWorker | None = None
        self._retirement_input: PlanState | None = None
        self._drawdown_worker: _TransitionWorker | None = None
        self._drawdown_input: PlanState | None = None
        self._backtest_worker: _TransitionWorker | None = None
        self._backtest_input: PlanState | None = None
        self._backtest_year = ""
        self.setWindowTitle(view_model.window_title)
        self.resize(1120, 780)

        self.facts_pane = FactsEntryPane(
            view_model.facts_form,
            self._handle_facts_submitted,
            self._handle_cleared,
        )
        self.charts_pane = ChartsPane(
            ChartsPaneCallbacks(
                select_basis=self._handle_charts_basis,
                select_mode=self._handle_charts_mode,
                run_monte_carlo=self._handle_monte_carlo_run,
                run_retirement=self._handle_retirement_run,
                run_drawdown=self._handle_drawdown_run,
                run_backtest=self._handle_backtest_run,
                select_backtest_year=self._handle_backtest_year,
            )
        )
        self.scenarios_pane = ScenariosPane(
            ScenariosPaneCallbacks(
                add_scenario=self._handle_scenario_added,
                remove_scenario=self._handle_scenario_removed,
                set_override=self._handle_scenario_override,
                remove_override=self._handle_scenario_override_removed,
                select_basis=self._handle_comparison_basis,
                select_metric=self._handle_comparison_metric,
            )
        )
        self.inspector_pane = InspectorPane(self._handle_override)
        tabs = QTabWidget(self)
        tabs.addTab(self.facts_pane, view_model.facts_tab_label)
        tabs.addTab(self.charts_pane, view_model.charts_tab_label)
        tabs.addTab(self.scenarios_pane, view_model.scenarios_tab_label)
        tabs.addTab(self.inspector_pane, view_model.inspector_tab_label)
        self.setCentralWidget(tabs)
        self._load_example()
        self._build_menus(view_model)

    def _build_menus(self, view_model: ShellViewModel) -> None:
        """Populate the menu bar from the shell view model.

        The "&" mnemonics and keyboard shortcuts are toolkit
        mechanics, not copy — the labels themselves come from the app
        layer (§4.7). Standard keys follow platform conventions
        (issue #135); the exports have no standard key, so they take
        explicit accelerators.
        """
        file_menu = self.menuBar().addMenu(f"&{view_model.file_menu.menu_label}")
        self.open_action = file_menu.addAction(view_model.file_menu.open_label)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_plan_dialog)
        self.save_action = file_menu.addAction(view_model.file_menu.save_label)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_plan)
        self.save_as_action = file_menu.addAction(view_model.file_menu.save_as_label)
        self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_as_action.triggered.connect(self.save_plan_as_dialog)
        file_menu.addSeparator()
        self.export_cash_flow_action = file_menu.addAction(
            view_model.file_menu.export_cash_flow_label
        )
        self.export_cash_flow_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_cash_flow_action.triggered.connect(self.export_cash_flow_dialog)
        self.export_report_action = file_menu.addAction(
            view_model.file_menu.export_report_label
        )
        self.export_report_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.export_report_action.triggered.connect(self.export_report_dialog)
        file_menu.addSeparator()
        self.quit_action = file_menu.addAction(view_model.file_menu.quit_label)
        # Not StandardKey.Quit: Windows resolves it to the rare
        # Key_Exit multimedia key rather than an accelerator. The
        # literal gives Ctrl+Q, and macOS maps Ctrl to Command, so the
        # binding lands on each platform's convention anyway.
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.close)
        help_menu = self.menuBar().addMenu(f"&{view_model.help_menu_label}")
        self.help_guide_action = help_menu.addAction(view_model.help_guide.title)
        self.help_guide_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        self.help_guide_action.triggered.connect(self.show_help_guide)
        self.about_action = help_menu.addAction(view_model.about.title)
        self.about_action.triggered.connect(self.show_about)

    def open_plan(self, path: Path) -> bool:
        """Load the plan at ``path`` into the session; True when it loaded.

        On success every pane re-renders from the loaded state and the
        facts form repopulates from the loaded household, so an edit
        starts from what the file says. On failure the session is left
        untouched and the status bar explains.
        """
        outcome = load_plan_state(path, today=_today())
        self.statusBar().showMessage(outcome.message)
        if outcome.state is None:
            return False
        self._state = outcome.state
        self._plan_path = path
        self._remember_plan_path(path)
        household = outcome.state.household
        if household is not None:
            self.facts_pane.set_form_data(facts_form_data_from_household(household))
            self.facts_pane.status_label.setText(outcome.message)
        self._refresh_result_panes()
        return True

    def save_plan(self) -> None:
        """Save to the session's plan file, or ask for one the first time."""
        if self._plan_path is None:
            self.save_plan_as_dialog()
            return
        self._write_plan(self._plan_path)

    def _dialog_dir(self) -> str:
        """Where file dialogs open (never the app's install directory).

        Beside the session's plan file when there is one, else the
        user's documents folder.
        """
        if self._plan_path is not None:
            return str(self._plan_path.parent)
        return QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )

    def open_plan_dialog(self) -> None:
        """Ask for a plan file and load it."""
        menu = self._view_model.file_menu
        filename, _selected = QFileDialog.getOpenFileName(
            self, menu.open_dialog_title, self._dialog_dir(), menu.file_filter
        )
        if filename:
            self.open_plan(Path(filename))

    def save_plan_as_dialog(self) -> None:
        """Ask where to save the plan and write it there."""
        menu = self._view_model.file_menu
        filename, _selected = QFileDialog.getSaveFileName(
            self, menu.save_dialog_title, self._dialog_dir(), menu.file_filter
        )
        if not filename:
            return
        if not filename.endswith(menu.file_suffix):
            filename += menu.file_suffix
        self._write_plan(Path(filename))

    def export_cash_flow_dialog(self) -> None:
        """Ask where to export the cash-flow CSV and write it there (9.19).

        The CSV presents the active run in the charts screen's money
        basis; the app layer builds and writes it — the shell only
        contributes the dialog.
        """
        menu = self._view_model.file_menu
        filename, _selected = QFileDialog.getSaveFileName(
            self,
            menu.export_cash_flow_dialog_title,
            self._dialog_dir(),
            menu.export_cash_flow_filter,
        )
        if not filename:
            return
        if not filename.endswith(menu.export_cash_flow_suffix):
            filename += menu.export_cash_flow_suffix
        outcome = export_cash_flow_csv(
            self._state,
            Path(filename),
            basis=self._charts_basis,
            plan_name=plan_display_name(self._plan_path),
        )
        self.statusBar().showMessage(outcome.message)

    def export_report_dialog(self) -> None:
        """Ask where to export the plan report and print it there (9.19).

        The report presents the charts screen's basis and run mode and
        the scenarios screen's comparison selections — exactly what is
        on screen. The app layer builds the document; the shell
        contributes the chart images and the PDF paint device (§4.7).
        """
        menu = self._view_model.file_menu
        filename, _selected = QFileDialog.getSaveFileName(
            self,
            menu.export_report_dialog_title,
            self._dialog_dir(),
            menu.export_report_filter,
        )
        if not filename:
            return
        if not filename.endswith(menu.export_report_suffix):
            filename += menu.export_report_suffix
        report = build_plan_report(
            self._state,
            ReportRequest(
                plan_name=plan_display_name(self._plan_path),
                basis=self._charts_basis,
                mode=self._charts_mode,
                comparison_basis=self._comparison_basis,
                comparison_metric_key=self._comparison_metric,
                backtest_year=self._backtest_year,
            ),
        )
        if report is None:
            self.statusBar().showMessage(NOTHING_TO_EXPORT_MESSAGE)
            return
        self.statusBar().showMessage(self._write_report_pdf(report, Path(filename)))

    def _write_report_pdf(self, report: PlanReport, path: Path) -> str:
        """Print the report to a PDF at ``path``; the status line to show.

        Every chart spec renders to an image registered under the
        resource name the report's HTML references, then the laid-out
        document prints to the PDF device. The device reports no write
        status (a failed open only logs a Qt warning), so the document
        prints to a sibling ``.part`` file that must come out non-empty
        before it replaces ``path`` — over-writing a stale export can
        therefore never report success while leaving the old file in
        place. A failure folds into the returned message, matching the
        app-layer transitions' rule.
        """
        document = QTextDocument()
        for index, chart in enumerate(report.charts):
            document.addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(chart_resource_name(index)),
                chart_image(chart, report.categories),
            )
        document.setHtml(report.html)
        partial = path.with_name(f"{path.name}.part")
        try:
            writer = QPdfWriter(str(partial))
            writer.setResolution(_REPORT_DPI)
            document.print_(writer)
            # The writer must release its file handle before the
            # replace, or Windows refuses to move the finished file.
            del writer
            if not partial.exists() or partial.stat().st_size == 0:
                return REPORT_NOT_WRITTEN_MESSAGE
            partial.replace(path)
        except OSError as exc:
            return f"{REPORT_EXPORT_FAILED_PREFIX}{exc}"
        finally:
            with contextlib.suppress(OSError):
                partial.unlink(missing_ok=True)
        return report_exported_message(path)

    def _write_plan(self, path: Path) -> None:
        """Write the session's plan to ``path`` and report the outcome."""
        outcome = save_plan_state(self._state, path)
        self.statusBar().showMessage(outcome.message)
        if outcome.saved:
            self._state = state_marked_saved(self._state)
            self._plan_path = path
            self._remember_plan_path(path)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Ask before a close discards unsaved plan edits (issue #136).

        Save runs the normal save flow — asking for a path when the
        session has none — and the window closes only once the changes
        are actually saved: a failed or cancelled save keeps it open
        rather than silently discarding the edits it just promised to
        keep.
        """
        if not has_unsaved_changes(self._state):
            event.accept()
            return
        choice = QMessageBox.question(
            self,
            UNSAVED_CHANGES_TITLE,
            UNSAVED_CHANGES_PROMPT,
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            self.save_plan()
            if has_unsaved_changes(self._state):
                event.ignore()
                return
            event.accept()
            return
        if choice == QMessageBox.StandardButton.Discard:
            event.accept()
            return
        event.ignore()

    def _remember_plan_path(self, path: Path) -> None:
        """Record the plan path for the next launch, best-effort.

        An unwritable config directory must not break the save or load
        that just succeeded — worst case the next launch starts on the
        example again.
        """
        if self._settings_path is None:
            return
        with contextlib.suppress(OSError):
            record_last_plan_path(self._settings_path, path)

    def _load_example(self) -> None:
        """Open with the example plan on screen and projected (§4.9).

        The example is raw form text through the normal submission
        path — guaranteed parseable by test — with the status line
        explaining it is an example, not the user's data. It is
        shipped demo data, not a user edit, so closing straight after
        launch must not ask about saving it (issue #136).
        """
        self.facts_pane.set_form_data(example_facts_form_data())
        self._handle_facts_submitted(self.facts_pane.form_data())
        self._state = state_marked_saved(self._state)
        self.facts_pane.status_label.setText(self._view_model.facts_form.example_note)

    def _handle_cleared(self) -> str:
        """Reset the session to no plan and re-render the result panes.

        The session's plan file detaches too: after a clear, the next
        Save asks where to write rather than overwriting the file the
        cleared plan came from.
        """
        self._state = initial_plan_state()
        self._plan_path = None
        self._refresh_result_panes()
        return self._view_model.facts_form.cleared_note

    def _handle_facts_submitted(self, data: FactsFormData) -> str:
        """Parse a facts submission; on success, re-project and refresh."""
        now = datetime.now(tz=UTC)
        # Provenance timestamps stay UTC; "today" is the user's calendar
        # day, which differs from the UTC date around midnight. The form
        # defaults blank as_of dates from the same local day the run
        # uses, so a defaulted balance date is never future-dated (§4.8).
        today = now.astimezone().date()
        result = parse_facts_form(
            data, recorded_on=now, today=today, previous=self._state.household
        )
        if result.household is None:
            return format_form_errors(self._view_model.facts_form, result.errors)
        self._state = state_with_household(self._state, result.household, today=today)
        # Rows typed fresh mint their entity ids at parse time; seeding
        # them back means the next resubmission edits the same entities
        # instead of minting again and orphaning overrides (§4.3).
        self.facts_pane.set_entity_ids(plan_entity_ids(result.household))
        self._refresh_result_panes()
        return facts_saved_message(self._state)

    def _handle_override(self, key: str, raw_value: str) -> str | None:
        """Apply an in-place assumption override; report a rejection."""
        now = datetime.now(tz=UTC)
        outcome = state_with_override(
            self._state, key, raw_value, recorded_on=now, today=now.astimezone().date()
        )
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self._refresh_result_panes()
        return None

    def _handle_charts_basis(self, key: str) -> None:
        """Re-present the charts in the basis the user selected."""
        self._charts_basis = basis_from_key(key)
        self._refresh_charts_pane()

    def _handle_charts_mode(self, key: str) -> None:
        """Re-present the charts in the run mode the user selected (9.13)."""
        self._charts_mode = run_mode_from_key(key)
        self._refresh_charts_pane()

    def _transition_in_flight(self) -> bool:
        """Whether a slow run (Monte Carlo, retirement, drawdown, backtest) is running.

        The transitions share one guard: each computes from the
        session state captured at start, so a second launched while
        the first runs could only ever be discarded as stale after
        minutes of wasted compute.
        """
        return (
            self._monte_carlo_worker is not None
            or self._retirement_worker is not None
            or self._drawdown_worker is not None
            or self._backtest_worker is not None
        )

    def _set_transitions_busy(self, *, busy: bool) -> None:
        """Disable (or re-enable) every slow-run action together."""
        self.charts_pane.set_monte_carlo_busy(busy=busy)
        self.charts_pane.set_retirement_busy(busy=busy)
        self.charts_pane.set_drawdown_busy(busy=busy)
        self.charts_pane.set_backtest_busy(busy=busy)

    def _handle_monte_carlo_run(self, seed_text: str, paths_text: str) -> None:
        """Start a Monte Carlo run off the GUI thread (9.13).

        The window stays responsive while the paths run; the finished
        state arrives back on the GUI thread through the worker's
        queued signal. Both slow-run actions are disabled meanwhile,
        and a request while either is in flight is ignored.
        """
        if self._transition_in_flight():
            return
        state = self._state
        today = _today()
        worker = _TransitionWorker(
            lambda: state_with_monte_carlo(state, seed_text, paths_text, today=today)
        )
        worker.signals.finished.connect(self._handle_monte_carlo_finished)
        self._monte_carlo_worker = worker
        self._monte_carlo_input = state
        self._set_transitions_busy(busy=True)
        status = monte_carlo_running_status(paths_text)
        self.statusBar().showMessage(status)
        self.charts_pane.show_busy(status)
        self.monte_carlo_pool.start(worker)

    def _handle_monte_carlo_finished(self, state: PlanState) -> None:
        """Adopt a finished Monte Carlo run unless the plan moved on.

        The run was computed from the session state captured at start;
        if a facts save, override, or scenario edit replaced the state
        meanwhile, the result describes a plan no longer on screen and
        is discarded (the status bar says so).
        """
        self._monte_carlo_worker = None
        self._set_transitions_busy(busy=False)
        self.charts_pane.clear_busy()
        stale = self._state is not self._monte_carlo_input
        self._monte_carlo_input = None
        if stale:
            self.statusBar().showMessage(MONTE_CARLO_STALE_MESSAGE)
            return
        self._state = state
        self.statusBar().clearMessage()
        self._refresh_result_panes()

    def _handle_retirement_run(
        self, rate_text: str, success_text: str, seed_text: str, paths_text: str
    ) -> None:
        """Start a retirement-age search off the GUI thread (9.14).

        The search runs the plan once per candidate age — same
        threading rules as the Monte Carlo run: the window stays
        responsive, both slow-run actions disable meanwhile, and a
        request while either is in flight is ignored. The screen's
        current run mode is the search's basis; the seed and path text
        come from the Monte Carlo panel's own controls.
        """
        if self._transition_in_flight():
            return
        state = self._state
        today = _today()
        request = RetirementRequest(
            mode=self._charts_mode,
            rate_text=rate_text,
            seed_text=seed_text,
            paths_text=paths_text,
            success_text=success_text,
        )
        worker = _TransitionWorker(
            lambda: state_with_retirement(state, request, today=today)
        )
        worker.signals.finished.connect(self._handle_retirement_finished)
        self._retirement_worker = worker
        self._retirement_input = state
        self._set_transitions_busy(busy=True)
        self.statusBar().showMessage(RETIREMENT_RUNNING_MESSAGE)
        self.charts_pane.show_busy(RETIREMENT_RUNNING_MESSAGE)
        self.monte_carlo_pool.start(worker)

    def _handle_retirement_finished(self, state: PlanState) -> None:
        """Adopt a finished search unless the plan moved on (9.14).

        Same staleness rule as the Monte Carlo delivery: an answer
        computed from a session state that was replaced mid-search
        describes a plan no longer on screen and is discarded.
        """
        self._retirement_worker = None
        self._set_transitions_busy(busy=False)
        self.charts_pane.clear_busy()
        stale = self._state is not self._retirement_input
        self._retirement_input = None
        if stale:
            self.statusBar().showMessage(RETIREMENT_STALE_MESSAGE)
            return
        self._state = state
        self.statusBar().clearMessage()
        self._refresh_result_panes()

    def _handle_drawdown_run(
        self, age_text: str, success_text: str, seed_text: str, paths_text: str
    ) -> None:
        """Start a sustainable-income search off the GUI thread (9.25).

        The search runs the plan once per probed spending level — same
        threading rules as the Monte Carlo run: the window stays
        responsive, every slow-run action disables meanwhile, and a
        request while any is in flight is ignored. The screen's
        current run mode is the search's basis; the seed and path text
        come from the Monte Carlo panel's own controls.
        """
        if self._transition_in_flight():
            return
        state = self._state
        today = _today()
        request = DrawdownRequest(
            mode=self._charts_mode,
            age_text=age_text,
            seed_text=seed_text,
            paths_text=paths_text,
            success_text=success_text,
        )
        worker = _TransitionWorker(
            lambda: state_with_drawdown(state, request, today=today)
        )
        worker.signals.finished.connect(self._handle_drawdown_finished)
        self._drawdown_worker = worker
        self._drawdown_input = state
        self._set_transitions_busy(busy=True)
        self.statusBar().showMessage(DRAWDOWN_RUNNING_MESSAGE)
        self.charts_pane.show_busy(DRAWDOWN_RUNNING_MESSAGE)
        self.monte_carlo_pool.start(worker)

    def _handle_drawdown_finished(self, state: PlanState) -> None:
        """Adopt a finished search unless the plan moved on (9.25).

        Same staleness rule as the Monte Carlo delivery: an answer
        computed from a session state that was replaced mid-search
        describes a plan no longer on screen and is discarded.
        """
        self._drawdown_worker = None
        self._set_transitions_busy(busy=False)
        self.charts_pane.clear_busy()
        stale = self._state is not self._drawdown_input
        self._drawdown_input = None
        if stale:
            self.statusBar().showMessage(DRAWDOWN_STALE_MESSAGE)
            return
        self._state = state
        self.statusBar().clearMessage()
        self._refresh_result_panes()

    def _handle_backtest_year(self, text: str) -> None:
        """Re-present the charts with the picked starting year (9.18).

        Presentation state like the basis and mode selections — no
        run happens; the trajectory comes from the held result. Qt
        fires ``editingFinished`` on every focus-out, so an unchanged
        text skips the rebuild.
        """
        if text == self._backtest_year:
            return
        self._backtest_year = text
        self._refresh_charts_pane()

    def _handle_backtest_run(self) -> None:
        """Start a historical backtest off the GUI thread (9.18).

        One deterministic window per historical starting year — same
        threading rules as the Monte Carlo run: the window stays
        responsive, every slow-run action disables meanwhile, and a
        request while any is in flight is ignored.
        """
        if self._transition_in_flight():
            return
        state = self._state
        today = _today()
        worker = _TransitionWorker(lambda: state_with_backtest(state, today=today))
        worker.signals.finished.connect(self._handle_backtest_finished)
        self._backtest_worker = worker
        self._backtest_input = state
        self._set_transitions_busy(busy=True)
        self.statusBar().showMessage(BACKTEST_RUNNING_MESSAGE)
        self.charts_pane.show_busy(BACKTEST_RUNNING_MESSAGE)
        self.monte_carlo_pool.start(worker)

    def _handle_backtest_finished(self, state: PlanState) -> None:
        """Adopt a finished backtest unless the plan moved on (9.18).

        Same staleness rule as the Monte Carlo delivery: a result
        computed from a session state that was replaced mid-run
        describes a plan no longer on screen and is discarded.
        """
        self._backtest_worker = None
        self._set_transitions_busy(busy=False)
        self.charts_pane.clear_busy()
        stale = self._state is not self._backtest_input
        self._backtest_input = None
        if stale:
            self.statusBar().showMessage(BACKTEST_STALE_MESSAGE)
            return
        self._state = state
        self.statusBar().clearMessage()
        self._refresh_result_panes()

    def _refresh_charts_pane(self) -> None:
        """Re-render the charts tab in its selected basis and run mode."""
        self.charts_pane.refresh(
            build_charts_view_model(
                self._state,
                basis=self._charts_basis,
                mode=self._charts_mode,
                backtest_year=self._backtest_year,
            )
        )

    def _handle_scenario_added(self, name: str) -> str | None:
        """Add a scenario; report a rejection."""
        outcome = state_with_scenario_added(self._state, name, today=_today())
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self._refresh_result_panes()
        return None

    def _handle_scenario_removed(self, name: str) -> None:
        """Remove a scenario and re-render the comparison."""
        self._state = state_without_scenario(self._state, name, today=_today())
        self._refresh_result_panes()

    def _handle_scenario_override(
        self, scenario: str, target_key: str, raw_value: str
    ) -> str | None:
        """Set one scenario override; report a rejection."""
        outcome = state_with_scenario_override(
            self._state, scenario, target_key, raw_value, today=_today()
        )
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self._refresh_result_panes()
        return None

    def _handle_scenario_override_removed(self, scenario: str, target_key: str) -> None:
        """Remove one scenario override and re-render the comparison."""
        self._state = state_without_scenario_override(
            self._state, scenario, target_key, today=_today()
        )
        self._refresh_result_panes()

    def _handle_comparison_basis(self, key: str) -> None:
        """Re-present the comparison in the basis the user selected."""
        self._comparison_basis = basis_from_key(key)
        self._refresh_scenarios_pane()

    def _handle_comparison_metric(self, key: str) -> None:
        """Re-present the comparison on the metric the user selected."""
        self._comparison_metric = metric_from_key(key)
        self._refresh_scenarios_pane()

    def _refresh_scenarios_pane(self) -> None:
        """Re-render the scenario manager and comparison report."""
        self.scenarios_pane.refresh(
            build_scenarios_view_model(
                self._state,
                basis=self._comparison_basis,
                metric_key=self._comparison_metric,
            )
        )

    def _refresh_result_panes(self) -> None:
        """Re-render every pane that reads the session's projection."""
        self._refresh_charts_pane()
        self._refresh_scenarios_pane()
        self.inspector_pane.refresh(build_inspector_view_model(self._state))

    def about_dialog(self) -> AboutDialog:
        """The About dialog, bound to the shell's about view model."""
        return AboutDialog(self._about_view_model, self)

    def show_about(self) -> None:
        """Show the About dialog; it repeats the disclaimer (§1)."""
        dialog = self.about_dialog()
        dialog.exec()
        # Parented dialogs outlive exec(); release each one instead of
        # accruing a child per menu click until the window closes.
        dialog.deleteLater()

    def help_guide_dialog(self) -> HelpGuideDialog:
        """The how-to-use guide, bound to the shell's guide view model."""
        return HelpGuideDialog(self._view_model.help_guide, self)

    def show_help_guide(self) -> None:
        """Show the how-to-use guide."""
        dialog = self.help_guide_dialog()
        dialog.exec()
        dialog.deleteLater()


def prompt_disclaimer(view_model: DisclaimerViewModel) -> bool:
    """Run the disclaimer dialog modally; True means the user accepted."""
    dialog = DisclaimerDialog(view_model)
    return dialog.exec() == QDialog.DialogCode.Accepted


__all__ = [
    "AboutDialog",
    "AboutViewModel",
    "DisclaimerDialog",
    "HelpGuideDialog",
    "MainWindow",
    "prompt_disclaimer",
]
