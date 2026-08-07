"""Projection chart widget tests, offscreen (issues 8.4, 9.13; §4.7).

The pane is thin by policy, so these tests only check the bindings:
chart sub-tabs render from the view model, the empty-state message
gates them, the basis and run-mode toggles forward the selected keys
back, the Monte Carlo run action forwards the raw seed and path-count
text, overlay bands draw as line series, and the Monte Carlo fan
chart draws its nested fills as area series on its own tab (9.24)
with hover tooltips on bars, lines, and fills alike (9.23).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCharts import (
    QAreaSeries,
    QBarSet,
    QChartView,
    QLineSeries,
    QStackedBarSeries,
)
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QRadioButton

from glidepath.app import (
    BACKTEST_RUNNING_MESSAGE,
    BACKTEST_STALE_MESSAGE,
    DRAWDOWN_RUNNING_MESSAGE,
    DRAWDOWN_STALE_MESSAGE,
    MONTE_CARLO_CHART_TITLE,
    MONTE_CARLO_STALE_MESSAGE,
    NO_BACKTEST_MESSAGE,
    NO_DRAWDOWN_MESSAGE,
    NO_MONTE_CARLO_MESSAGE,
    NO_RETIREMENT_MESSAGE,
    RETIREMENT_RUNNING_MESSAGE,
    RETIREMENT_STALE_MESSAGE,
    ChartsViewModel,
    DrawdownRequest,
    PlanState,
    RetirementRequest,
    bar_tooltip,
    build_charts_view_model,
    build_shell_view_model,
    example_facts_form_data,
    fill_tooltip,
    initial_plan_state,
    monte_carlo_running_status,
    parse_facts_form,
    state_with_backtest,
    state_with_drawdown,
    state_with_household,
    state_with_monte_carlo,
    state_with_override,
    state_with_retirement,
)
from glidepath.app.backtest import BACKTEST_YEAR_LABEL
from glidepath.app.drawdown import DRAWDOWN_ANSWER_PREFIX
from glidepath.app.retirement import RETIREMENT_ANSWER_PREFIX
from glidepath.core import (
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    RunMode,
    SpendingPlan,
    Wrapper,
)
from glidepath.gui import charts as gui_charts
from glidepath.gui.charts import ChartsPane, ChartsPaneCallbacks
from glidepath.gui.style import (
    CHART_BAND_INKS,
    CHART_FAN_ALPHAS,
    CHART_FAN_FILL,
    CHART_SERIES,
    CHART_SURFACE,
)
from glidepath.gui.widgets import MainWindow
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

if TYPE_CHECKING:
    from collections.abc import Callable

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def callbacks(
    *,
    select_basis: Callable[[str], None] | None = None,
    select_mode: Callable[[str], None] | None = None,
    run_monte_carlo: Callable[[str, str], None] | None = None,
    run_retirement: Callable[[str, str, str, str], None] | None = None,
    run_drawdown: Callable[[str, str, str, str], None] | None = None,
    run_backtest: Callable[[], None] | None = None,
    select_backtest_year: Callable[[str], None] | None = None,
) -> ChartsPaneCallbacks:
    """Pane callbacks defaulting to no-ops for uninvolved actions."""
    return ChartsPaneCallbacks(
        select_basis=select_basis or (lambda _key: None),
        select_mode=select_mode or (lambda _key: None),
        run_monte_carlo=run_monte_carlo or (lambda _seed, _paths: None),
        run_retirement=run_retirement or (lambda _rate, _success, _seed, _paths: None),
        run_drawdown=run_drawdown or (lambda _age, _success, _seed, _paths: None),
        run_backtest=run_backtest or (lambda: None),
        select_backtest_year=select_backtest_year or (lambda _year: None),
    )


def projected_state() -> PlanState:
    """One projected session over a minimal ISA saver."""
    isa = Wrapper(
        id=EntityId("charts-gui-isa"),
        kind=ISA_KIND,
        balance=Fact(value=Money(Decimal(25000)), as_of=AS_OF, recorded_on=RECORDED),
    )
    person = Person(
        id=EntityId("charts-gui-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(isa,),
    )
    plan = Household(
        persons=(person,),
        spending=SpendingPlan(
            annual_spending_real=Fact(
                value=Money(Decimal(18000)), as_of=AS_OF, recorded_on=RECORDED
            )
        ),
    )
    return state_with_household(initial_plan_state(), plan, today=TODAY)


def projected_view_model() -> ChartsViewModel:
    """A charts view model over one projected ISA saver."""
    return build_charts_view_model(projected_state())


def monte_carlo_view_model() -> ChartsViewModel:
    """The charts screen after a 2-path Monte Carlo run, Monte Carlo mode."""
    state = state_with_monte_carlo(projected_state(), "7", "2", today=TODAY)
    return build_charts_view_model(state, mode=RunMode.MONTE_CARLO)


def wait_for_monte_carlo(window: MainWindow) -> None:
    """Wait out the worker, then deliver its queued finish signal."""
    assert window.monte_carlo_pool.waitForDone(60_000)
    QApplication.processEvents()


def retirement_state() -> PlanState:
    """A short-horizon 60-year-old with employment income, solved.

    The planning age is overridden down to 65, so the deterministic
    search covers ages 60 to 64 and stays fast.
    """
    outcome = state_with_override(
        initial_plan_state(),
        "horizon.planning_age",
        "65",
        recorded_on=RECORDED,
        today=TODAY,
    )
    assert outcome.error is None
    isa = Wrapper(
        id=EntityId("retire-gui-isa"),
        kind=ISA_KIND,
        balance=Fact(value=Money(Decimal(300000)), as_of=AS_OF, recorded_on=RECORDED),
    )
    person = Person(
        id=EntityId("retire-gui-person"),
        date_of_birth=Fact(value=date(1966, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=63, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=Fact(
            value=Money(Decimal(50000)), as_of=AS_OF, recorded_on=RECORDED
        ),
        wrappers=(isa,),
    )
    plan = Household(
        persons=(person,),
        spending=SpendingPlan(
            annual_spending_real=Fact(
                value=Money(Decimal(18000)), as_of=AS_OF, recorded_on=RECORDED
            )
        ),
    )
    state = state_with_household(outcome.state, plan, today=TODAY)
    request = RetirementRequest(mode=RunMode.DETERMINISTIC, rate_text="66")
    return state_with_retirement(state, request, today=TODAY)


def drawdown_state() -> PlanState:
    """The same short-horizon saver, solved for the sustainable income."""
    outcome = state_with_override(
        initial_plan_state(),
        "horizon.planning_age",
        "65",
        recorded_on=RECORDED,
        today=TODAY,
    )
    assert outcome.error is None
    isa = Wrapper(
        id=EntityId("drawdown-gui-isa"),
        kind=ISA_KIND,
        balance=Fact(value=Money(Decimal(300000)), as_of=AS_OF, recorded_on=RECORDED),
    )
    person = Person(
        id=EntityId("drawdown-gui-person"),
        date_of_birth=Fact(value=date(1966, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=63, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(isa,),
    )
    plan = Household(
        persons=(person,),
        spending=SpendingPlan(
            annual_spending_real=Fact(
                value=Money(Decimal(18000)), as_of=AS_OF, recorded_on=RECORDED
            )
        ),
    )
    state = state_with_household(outcome.state, plan, today=TODAY)
    request = DrawdownRequest(mode=RunMode.DETERMINISTIC, age_text="63")
    return state_with_drawdown(state, request, today=TODAY)


class TestChartsPane:
    """The pane binds the view model and forwards the basis toggle."""

    def test_empty_view_model_shows_only_the_message(self) -> None:
        """No charts yet: the message shows and the sub-tabs hide."""
        pane = ChartsPane(callbacks())
        view_model = build_charts_view_model(initial_plan_state())
        pane.refresh(view_model)
        assert pane.message_label.text() == view_model.message
        assert not pane.message_label.isHidden()
        assert pane.chart_tabs.isHidden()
        assert pane.chart_tabs.count() == 0

    def test_projected_view_model_renders_one_tab_per_chart(self) -> None:
        """Each chart binds to a sub-tab titled from the view model."""
        pane = ChartsPane(callbacks())
        view_model = projected_view_model()
        pane.refresh(view_model)
        assert pane.message_label.isHidden()
        assert not pane.chart_tabs.isHidden()
        tab_labels = [
            pane.chart_tabs.tabText(index) for index in range(pane.chart_tabs.count())
        ]
        assert tab_labels == [chart.title for chart in view_model.charts]

    def test_refresh_replaces_rather_than_accumulates(self) -> None:
        """A second refresh rebuilds the sub-tabs in place."""
        pane = ChartsPane(callbacks())
        view_model = projected_view_model()
        pane.refresh(view_model)
        pane.refresh(view_model)
        assert pane.chart_tabs.count() == len(view_model.charts)

    def test_the_allocation_note_renders(self) -> None:
        """The "Invested as" line shows with a projection, hides without."""
        pane = ChartsPane(callbacks())
        pane.refresh(build_charts_view_model(initial_plan_state()))
        assert pane.allocation_label.isHidden()
        view_model = projected_view_model()
        pane.refresh(view_model)
        assert not pane.allocation_label.isHidden()
        assert pane.allocation_label.text() == view_model.allocation_note
        assert view_model.allocation_note.startswith("Invested as")

    def test_refresh_keeps_the_selected_chart(self) -> None:
        """Rebuilding the sub-tabs must not move the user off a chart."""
        pane = ChartsPane(callbacks())
        view_model = projected_view_model()
        pane.refresh(view_model)
        pane.chart_tabs.setCurrentIndex(2)
        pane.refresh(view_model)
        assert pane.chart_tabs.currentIndex() == 2

    def test_basis_toggle_forwards_the_option_key(self) -> None:
        """Clicking a basis radio reports its key to the shell handler."""
        selected: list[str] = []
        pane = ChartsPane(callbacks(select_basis=selected.append))
        view_model = projected_view_model()
        pane.refresh(view_model)
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        labels = {option.key: option.label for option in view_model.basis_options}
        assert buttons[labels["real"]].isChecked()
        buttons[labels["nominal"]].click()
        assert selected == ["nominal"]


class TestMonteCarloControls:
    """The run-mode control and Monte Carlo readout bindings (9.13)."""

    def test_deterministic_mode_hides_the_run_controls(self) -> None:
        """The default presentation carries no Monte Carlo inputs."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        assert pane.seed_edit.isHidden()
        assert pane.paths_edit.isHidden()
        assert pane.run_button.isHidden()
        assert pane.metrics_label.isHidden()

    def test_mode_toggle_forwards_the_option_key(self) -> None:
        """Clicking a mode radio reports its key to the shell handler."""
        selected: list[str] = []
        pane = ChartsPane(callbacks(select_mode=selected.append))
        view_model = projected_view_model()
        pane.refresh(view_model)
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        labels = {
            option.key: option.label for option in view_model.monte_carlo.mode_options
        }
        assert buttons[labels["deterministic"]].isChecked()
        buttons[labels["monte_carlo"]].click()
        assert selected == ["monte_carlo"]

    def test_run_button_forwards_the_raw_seed_and_paths_text(self) -> None:
        """The shell parses; the pane only captures and forwards."""
        runs: list[tuple[str, str]] = []
        pane = ChartsPane(
            callbacks(run_monte_carlo=lambda seed, paths: runs.append((seed, paths)))
        )
        state = projected_state()
        pane.refresh(build_charts_view_model(state, mode=RunMode.MONTE_CARLO))
        assert not pane.run_button.isHidden()
        pane.seed_edit.setText("42")
        pane.paths_edit.setText("5")
        pane.run_button.click()
        assert runs == [("42", "5")]

    def test_a_run_renders_metrics_and_the_fan_tab(self) -> None:
        """The readout fills and the fan chart binds to its own tab (9.24).

        The balances chart stays clean — the Monte Carlo presentation
        lives entirely on the fan tab: one area series per fill,
        outermost first, with the median as the single line series.
        """
        pane = ChartsPane(callbacks())
        view_model = monte_carlo_view_model()
        pane.refresh(view_model)
        assert "Success rate" in pane.metrics_label.text()
        assert not pane.metrics_label.isHidden()
        assert pane.monte_carlo_message_label.isHidden()
        balances = pane.chart_tabs.widget(0)
        assert isinstance(balances, QChartView)
        assert len(balances.chart().series()) == 1
        fan_index = pane.chart_tabs.count() - 1
        assert pane.chart_tabs.tabText(fan_index) == MONTE_CARLO_CHART_TITLE
        view = pane.chart_tabs.widget(fan_index)
        assert isinstance(view, QChartView)
        fan = view_model.charts[-1]
        areas = [
            entry for entry in view.chart().series() if isinstance(entry, QAreaSeries)
        ]
        assert [area.name() for area in areas] == [fill.label for fill in fan.fills]
        lines = [
            entry for entry in view.chart().series() if isinstance(entry, QLineSeries)
        ]
        assert [line.name() for line in lines] == [band.label for band in fan.bands]


class TestBacktestCard:
    """The historical-backtest card bindings (9.18)."""

    def test_no_run_shows_the_empty_state_copy(self) -> None:
        """Before any run the card carries the no-backtest message."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        assert pane.backtest_metrics_label.isHidden()
        assert pane.backtest_message_label.text() == NO_BACKTEST_MESSAGE

    def test_run_button_forwards_the_click(self) -> None:
        """The card has no inputs; the click alone reaches the shell."""
        runs: list[bool] = []
        pane = ChartsPane(callbacks(run_backtest=lambda: runs.append(True)))
        pane.refresh(projected_view_model())
        assert not pane.backtest_button.isHidden()
        pane.backtest_button.click()
        assert runs == [True]

    def test_a_run_renders_metrics_and_band_lines(self) -> None:
        """The readout fills and the balances chart gains line series."""
        pane = ChartsPane(callbacks())
        state = state_with_backtest(projected_state(), today=TODAY)
        view_model = build_charts_view_model(state)
        pane.refresh(view_model)
        assert "Success rate" in pane.backtest_metrics_label.text()
        assert not pane.backtest_metrics_label.isHidden()
        assert pane.backtest_message_label.isHidden()
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        series = view.chart().series()
        assert len(series) == 1 + len(view_model.charts[0].bands)
        assert len(view_model.charts[0].bands) == 2

    def test_the_year_picker_is_labelled_and_explained(self) -> None:
        """The picker must say what it is for and what it expects.

        The label names the control, the tooltip explains what an
        entry draws, and — once a result is held — the placeholder
        shows the valid span greyed inside the empty box. Pinned
        because an unsynced label renders as a bare unexplained box.
        """
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        assert pane.backtest_year_label.text() == BACKTEST_YEAR_LABEL
        assert pane.backtest_year_edit.toolTip()
        state = state_with_backtest(projected_state(), today=TODAY)
        pane.refresh(build_charts_view_model(state))
        assert state.backtest is not None
        first = state.backtest.outcomes[0].start_year
        last = state.backtest.outcomes[-1].start_year
        assert pane.backtest_year_edit.placeholderText() == f"{first}-{last}"
        assert str(first) in pane.backtest_year_edit.toolTip()

    def test_year_picker_forwards_the_raw_text(self) -> None:
        """The shell parses; the picker only captures and forwards."""
        picked: list[str] = []
        pane = ChartsPane(callbacks(select_backtest_year=picked.append))
        pane.refresh(projected_view_model())
        pane.backtest_year_edit.setText("1973")
        pane.backtest_year_edit.editingFinished.emit()
        assert picked == ["1973"]


class TestRetirementCard:
    """The "When can I retire?" card bindings (9.14)."""

    def test_deterministic_mode_hides_the_success_target(self) -> None:
        """The deterministic basis offers no success target to set."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        assert pane.success_edit.isHidden()
        assert pane.success_label.isHidden()
        assert not pane.rate_edit.isHidden()
        assert not pane.retirement_button.isHidden()
        assert pane.retirement_message_label.text() == NO_RETIREMENT_MESSAGE

    def test_monte_carlo_mode_shows_the_success_target(self) -> None:
        """The Monte Carlo basis adds the success-target input."""
        pane = ChartsPane(callbacks())
        view_model = build_charts_view_model(
            projected_state(), mode=RunMode.MONTE_CARLO
        )
        pane.refresh(view_model)
        assert not pane.success_edit.isHidden()
        assert not pane.success_label.isHidden()

    def test_find_button_forwards_the_raw_text(self) -> None:
        """The shell parses; the card only captures and forwards."""
        searches: list[tuple[str, str, str, str]] = []
        pane = ChartsPane(
            callbacks(
                run_retirement=lambda rate, success, seed, paths: searches.append(
                    (rate, success, seed, paths)
                )
            )
        )
        view_model = build_charts_view_model(
            projected_state(), mode=RunMode.MONTE_CARLO
        )
        pane.refresh(view_model)
        pane.rate_edit.setText("70")
        pane.success_edit.setText("85")
        pane.seed_edit.setText("42")
        pane.paths_edit.setText("5")
        pane.retirement_button.click()
        assert searches == [("70", "85", "42", "5")]

    def test_an_answer_renders_on_the_card(self) -> None:
        """The answer and its detail show; the no-run message hides."""
        pane = ChartsPane(callbacks())
        pane.refresh(build_charts_view_model(retirement_state()))
        assert pane.retirement_answer_label.text()
        assert not pane.retirement_answer_label.isHidden()
        assert not pane.retirement_detail_label.isHidden()
        assert "£33,000.00" in pane.retirement_detail_label.text()
        assert pane.retirement_message_label.isHidden()


class TestDrawdownCard:
    """The "How much can I draw down?" card bindings (9.25)."""

    def test_deterministic_mode_hides_the_success_target(self) -> None:
        """The deterministic basis offers no success target to set."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        assert pane.drawdown_success_edit.isHidden()
        assert pane.drawdown_success_label.isHidden()
        assert not pane.drawdown_age_edit.isHidden()
        assert not pane.drawdown_button.isHidden()
        assert pane.drawdown_message_label.text() == NO_DRAWDOWN_MESSAGE

    def test_monte_carlo_mode_shows_the_success_target(self) -> None:
        """The Monte Carlo basis adds the success-target input."""
        pane = ChartsPane(callbacks())
        view_model = build_charts_view_model(
            projected_state(), mode=RunMode.MONTE_CARLO
        )
        pane.refresh(view_model)
        assert not pane.drawdown_success_edit.isHidden()
        assert not pane.drawdown_success_label.isHidden()

    def test_the_age_defaults_to_the_stated_decision(self) -> None:
        """Before any run the age input echoes the plan's decision."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        assert pane.drawdown_age_edit.text() == "60"

    def test_find_button_forwards_the_raw_text(self) -> None:
        """The shell parses; the card only captures and forwards."""
        searches: list[tuple[str, str, str, str]] = []
        pane = ChartsPane(
            callbacks(
                run_drawdown=lambda age, success, seed, paths: searches.append(
                    (age, success, seed, paths)
                )
            )
        )
        view_model = build_charts_view_model(
            projected_state(), mode=RunMode.MONTE_CARLO
        )
        pane.refresh(view_model)
        pane.drawdown_age_edit.setText("62")
        pane.drawdown_success_edit.setText("85")
        pane.seed_edit.setText("42")
        pane.paths_edit.setText("5")
        pane.drawdown_button.click()
        assert searches == [("62", "85", "42", "5")]

    def test_an_answer_renders_on_the_card(self) -> None:
        """The answer and its detail show; the no-run message hides."""
        pane = ChartsPane(callbacks())
        pane.refresh(build_charts_view_model(drawdown_state()))
        answer_text = pane.drawdown_answer_label.text()
        assert answer_text.startswith(DRAWDOWN_ANSWER_PREFIX)
        assert not pane.drawdown_answer_label.isHidden()
        assert not pane.drawdown_detail_label.isHidden()
        assert "age 63" in pane.drawdown_detail_label.text()
        assert pane.drawdown_message_label.isHidden()


class TestChartTheme:
    """The charts wear the theme's series palette and chrome (§4.7)."""

    def test_bar_sets_take_the_series_palette_in_fixed_order(self) -> None:
        """Series colours come from the fixed-order palette, by slot.

        The slot order is the palette's colour-vision-safety
        mechanism, so it is pinned: re-ranking or cycling colours
        would silently break the validated adjacent-pair separation.
        The surface-coloured border is the gap that keeps stacked
        segments readable.
        """
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        for tab_index in range(pane.chart_tabs.count()):
            view = pane.chart_tabs.widget(tab_index)
            assert isinstance(view, QChartView)
            [series] = view.chart().series()
            assert isinstance(series, QStackedBarSeries)
            for slot, bar_set in enumerate(series.barSets()):
                assert bar_set.color().name() == CHART_SERIES[slot]
                assert bar_set.borderColor().name() == CHART_SURFACE

    def test_band_lines_wear_the_ordered_inks(self) -> None:
        """Overlay lines take the neutral ink ramp, in band order.

        One hue stepped by lightness keeps the ordered bands apart
        from every series fill and from each other. The backtest
        trajectories are the overlay the balances chart still draws
        (the Monte Carlo lines moved to the fan tab, 9.24).
        """
        pane = ChartsPane(callbacks())
        state = state_with_backtest(projected_state(), today=TODAY)
        pane.refresh(build_charts_view_model(state))
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        bands = view.chart().series()[1:]
        assert bands
        for slot, line in enumerate(bands):
            assert isinstance(line, QLineSeries)
            pen = line.pen()
            assert pen.color().name() == CHART_BAND_INKS[slot]
            assert pen.width() == 2

    def test_the_fan_wears_the_single_hue_alpha_ramp(self) -> None:
        """Fan fills take the one fan hue at stepped alphas (9.24).

        Depth by alpha alone keeps the fan colour-vision-safe; the
        median line wears the darkest band ink over the fills.
        """
        pane = ChartsPane(callbacks())
        pane.refresh(monte_carlo_view_model())
        view = pane.chart_tabs.widget(pane.chart_tabs.count() - 1)
        assert isinstance(view, QChartView)
        areas = [
            entry for entry in view.chart().series() if isinstance(entry, QAreaSeries)
        ]
        assert areas
        for slot, area in enumerate(areas):
            colour = area.brush().color()
            assert colour.name() == CHART_FAN_FILL
            assert colour.alpha() == CHART_FAN_ALPHAS[slot]
        [median] = [
            entry for entry in view.chart().series() if isinstance(entry, QLineSeries)
        ]
        assert median.pen().color().name() == CHART_BAND_INKS[0]

    def test_the_chart_chrome_is_recessive(self) -> None:
        """White surface, horizontal-only hairline grid, muted axes."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        chart = view.chart()
        assert chart.backgroundBrush().color().name() == CHART_SURFACE
        assert chart.localizeNumbers()
        [x_axis] = chart.axes(Qt.Orientation.Horizontal)
        assert not x_axis.isGridLineVisible()
        [y_axis] = chart.axes(Qt.Orientation.Vertical)
        assert y_axis.isGridLineVisible()

    def test_single_series_charts_drop_the_legend(self) -> None:
        """One unbanded series: the sub-tab title alone names it.

        As soon as a second labelled entry appears (a fan fill, an
        overlay line), the legend is back — identity is never
        colour-alone.
        """
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        assert not view.chart().legend().isVisible()
        pane.refresh(monte_carlo_view_model())
        fan = pane.chart_tabs.widget(pane.chart_tabs.count() - 1)
        assert isinstance(fan, QChartView)
        assert fan.chart().legend().isVisible()


class _ToolTipRecorder:
    """A stand-in QToolTip recording the show and hide calls."""

    def __init__(self) -> None:
        self.shown: list[str] = []
        self.hides = 0

    def showText(self, _pos: object, text: str) -> None:  # noqa: N802
        """Record the copy a hover would pop up."""
        self.shown.append(text)

    def hideText(self) -> None:  # noqa: N802
        """Record that the tooltip was dismissed."""
        self.hides += 1


def _first_bar_set(pane: ChartsPane) -> QBarSet:
    """The first chart's first bar set, fresh from a refresh."""
    view = pane.chart_tabs.widget(0)
    assert isinstance(view, QChartView)
    [series] = view.chart().series()
    assert isinstance(series, QStackedBarSeries)
    [bar_set] = series.barSets()
    return bar_set


class TestBarTooltips:
    """Hovering a bar segment shows the app layer's tooltip copy."""

    @pytest.fixture(name="tooltips")
    def tooltips_fixture(self, monkeypatch: pytest.MonkeyPatch) -> _ToolTipRecorder:
        """Capture the tooltip calls the charts module makes."""
        recorder = _ToolTipRecorder()
        monkeypatch.setattr(gui_charts, "QToolTip", recorder)
        return recorder

    def test_hover_shows_the_segment_copy(self, tooltips: _ToolTipRecorder) -> None:
        """The hovered segment pops its series, period, and amount."""
        pane = ChartsPane(callbacks())
        view_model = projected_view_model()
        pane.refresh(view_model)
        bar_set = _first_bar_set(pane)
        hovering = True
        bar_set.hovered.emit(hovering, 1)
        chart = view_model.charts[0]
        expected = bar_tooltip(
            view_model.categories[1], chart.series[0].label, chart.series[0].values[1]
        )
        assert tooltips.shown == [expected]
        assert tooltips.hides == 0

    def test_leaving_the_bar_hides_the_tooltip(
        self, tooltips: _ToolTipRecorder
    ) -> None:
        """Hover-off dismisses rather than leaving stale copy up."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        bar_set = _first_bar_set(pane)
        hovering = False
        bar_set.hovered.emit(hovering, 0)
        assert tooltips.shown == []
        assert tooltips.hides == 1

    def test_out_of_range_hover_hides_rather_than_misreports(
        self, tooltips: _ToolTipRecorder
    ) -> None:
        """A hover index off the categories shows nothing."""
        pane = ChartsPane(callbacks())
        pane.refresh(projected_view_model())
        bar_set = _first_bar_set(pane)
        hovering = True
        bar_set.hovered.emit(hovering, -1)
        assert tooltips.shown == []
        assert tooltips.hides == 1


def _fan_view(pane: ChartsPane) -> QChartView:
    """The last sub-tab's chart view — the fan chart when one is held."""
    view = pane.chart_tabs.widget(pane.chart_tabs.count() - 1)
    assert isinstance(view, QChartView)
    return view


class TestOverlayTooltips:
    """Hovering overlay lines and fan fills pops app-layer copy (9.23)."""

    @pytest.fixture(name="tooltips")
    def tooltips_fixture(self, monkeypatch: pytest.MonkeyPatch) -> _ToolTipRecorder:
        """Capture the tooltip calls the charts module makes."""
        recorder = _ToolTipRecorder()
        monkeypatch.setattr(gui_charts, "QToolTip", recorder)
        return recorder

    def test_hovering_the_median_line_shows_its_point_copy(
        self, tooltips: _ToolTipRecorder
    ) -> None:
        """The hover snaps to the nearest category, exact amount shown."""
        pane = ChartsPane(callbacks())
        view_model = monte_carlo_view_model()
        pane.refresh(view_model)
        [line] = [
            entry
            for entry in _fan_view(pane).chart().series()
            if isinstance(entry, QLineSeries)
        ]
        [median] = view_model.charts[-1].bands
        hovering = True
        line.hovered.emit(QPointF(1.2, 999.0), hovering)
        expected = bar_tooltip(view_model.categories[1], median.label, median.values[1])
        assert tooltips.shown == [expected]
        assert tooltips.hides == 0

    def test_hovering_a_fill_shows_its_interval_copy(
        self, tooltips: _ToolTipRecorder
    ) -> None:
        """A fan fill answers with its label and low-to-high amounts."""
        pane = ChartsPane(callbacks())
        view_model = monte_carlo_view_model()
        pane.refresh(view_model)
        areas = [
            entry
            for entry in _fan_view(pane).chart().series()
            if isinstance(entry, QAreaSeries)
        ]
        fill = view_model.charts[-1].fills[0]
        hovering = True
        areas[0].hovered.emit(QPointF(0.4, 999.0), hovering)
        expected = fill_tooltip(
            view_model.categories[0], fill.label, fill.lower[0], fill.upper[0]
        )
        assert tooltips.shown == [expected]

    def test_a_backtest_trajectory_answers_hover_too(
        self, tooltips: _ToolTipRecorder
    ) -> None:
        """The balances chart's overlay lines carry the same binding."""
        pane = ChartsPane(callbacks())
        state = state_with_backtest(projected_state(), today=TODAY)
        view_model = build_charts_view_model(state)
        pane.refresh(view_model)
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        lines = [
            entry for entry in view.chart().series() if isinstance(entry, QLineSeries)
        ]
        band = view_model.charts[0].bands[0]
        hovering = True
        lines[0].hovered.emit(QPointF(0.0, 0.0), hovering)
        expected = bar_tooltip(view_model.categories[0], band.label, band.values[0])
        assert tooltips.shown == [expected]

    def test_leaving_a_line_hides_the_tooltip(self, tooltips: _ToolTipRecorder) -> None:
        """Hover-off dismisses rather than leaving stale copy up."""
        pane = ChartsPane(callbacks())
        pane.refresh(monte_carlo_view_model())
        [line] = [
            entry
            for entry in _fan_view(pane).chart().series()
            if isinstance(entry, QLineSeries)
        ]
        hovering = False
        line.hovered.emit(QPointF(0.0, 0.0), hovering)
        assert tooltips.shown == []
        assert tooltips.hides == 1

    def test_an_off_chart_hover_hides_rather_than_misreports(
        self, tooltips: _ToolTipRecorder
    ) -> None:
        """A point past the last category shows nothing."""
        pane = ChartsPane(callbacks())
        pane.refresh(monte_carlo_view_model())
        [line] = [
            entry
            for entry in _fan_view(pane).chart().series()
            if isinstance(entry, QLineSeries)
        ]
        hovering = True
        line.hovered.emit(QPointF(999.0, 0.0), hovering)
        assert tooltips.shown == []
        assert tooltips.hides == 1


class TestMainWindowChartsFlow:
    """Saving facts populates the charts tab through the app layer."""

    @pytest.fixture(name="window")
    def window_fixture(self) -> MainWindow:
        """A window with a minimal projectable plan already saved."""
        window = MainWindow(build_shell_view_model())
        facts = window.facts_pane
        facts.person_form.set_value("date_of_birth", "1991-02-01")
        facts.person_form.set_value("tax_residency", str(RUK_RESIDENCY))
        facts.person_form.set_value("target_retirement_age", "60")
        facts.spending_form.set_value("annual_spending_real", "18000")
        wrapper_form = facts.wrappers.add_entry()
        wrapper_form.set_value("kind", str(ISA_KIND))
        wrapper_form.set_value("balance", "25000")
        facts.submit_button.click()
        return window

    def test_saving_facts_fills_the_charts_tab(self, window: MainWindow) -> None:
        """The charts pane renders the projection the submission ran."""
        assert window.charts_pane.chart_tabs.count() == 3
        assert window.charts_pane.message_label.isHidden()

    def test_nominal_toggle_re_renders_in_place(self, window: MainWindow) -> None:
        """Toggling nominal keeps the charts and moves the selection."""
        buttons = {
            button.text(): button
            for button in window.charts_pane.findChildren(QRadioButton)
        }
        window.charts_pane.chart_tabs.setCurrentIndex(2)
        buttons["Nominal"].click()
        assert window.charts_pane.chart_tabs.count() == 3
        assert buttons["Nominal"].isChecked()
        assert not buttons["Real (today's money)"].isChecked()
        assert window.charts_pane.chart_tabs.currentIndex() == 2

    def test_monte_carlo_run_through_the_window(self, window: MainWindow) -> None:
        """Mode toggle, seed + paths, run: metrics and the fan appear.

        The fan chart binds to its own sub-tab (9.24) — one area
        series per fill plus the median line over the empty bar
        series — and switching back to deterministic drops the tab.
        """
        pane = window.charts_pane
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        buttons["Monte Carlo"].click()
        assert not pane.seed_edit.isHidden()
        pane.seed_edit.setText("7")
        pane.paths_edit.setText("2")
        pane.run_button.click()
        # The run executes off the GUI thread; the button stays disabled
        # and the busy indicator shows until the finished state is
        # delivered back (9.16).
        assert not pane.run_button.isEnabled()
        assert not pane.busy_bar.isHidden()
        assert pane.busy_label.text() == monte_carlo_running_status("2")
        wait_for_monte_carlo(window)
        assert pane.run_button.isEnabled()
        assert pane.busy_bar.isHidden()
        assert pane.busy_label.isHidden()
        assert "Success rate" in pane.metrics_label.text()
        assert pane.seed_edit.text() == "7"
        assert pane.paths_edit.text() == "2"
        assert pane.chart_tabs.count() == 4
        assert pane.chart_tabs.tabText(3) == MONTE_CARLO_CHART_TITLE
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        assert len(view.chart().series()) == 1
        fan_view = pane.chart_tabs.widget(3)
        assert isinstance(fan_view, QChartView)
        assert len(fan_view.chart().series()) == 6
        buttons["Deterministic"].click()
        assert pane.seed_edit.isHidden()
        assert pane.chart_tabs.count() == 3
        deterministic_view = pane.chart_tabs.widget(0)
        assert isinstance(deterministic_view, QChartView)
        assert len(deterministic_view.chart().series()) == 1

    def test_a_second_run_request_while_one_is_in_flight_is_ignored(
        self, window: MainWindow
    ) -> None:
        """Overlapping runs would race over the session state."""
        pane = window.charts_pane
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        buttons["Monte Carlo"].click()
        pane.seed_edit.setText("7")
        pane.paths_edit.setText("2")
        pane.run_button.click()
        pane.run_button.click()
        wait_for_monte_carlo(window)
        assert "Success rate" in pane.metrics_label.text()

    def test_a_plan_change_discards_an_in_flight_run(self, window: MainWindow) -> None:
        """A result computed from a replaced state never lands (9.13)."""
        pane = window.charts_pane
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        buttons["Monte Carlo"].click()
        pane.seed_edit.setText("7")
        pane.paths_edit.setText("2")
        pane.run_button.click()
        window.facts_pane.submit_button.click()
        wait_for_monte_carlo(window)
        assert window.statusBar().currentMessage() == MONTE_CARLO_STALE_MESSAGE
        assert pane.metrics_label.isHidden()
        assert pane.run_button.isEnabled()
        # The staleness-discard path must not leave a spinner running.
        assert pane.busy_bar.isHidden()

    def test_scenario_edits_clear_the_monte_carlo_charts(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scenario change re-renders the charts without the old run."""
        pane = window.charts_pane
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        buttons["Monte Carlo"].click()
        pane.seed_edit.setText("7")
        pane.paths_edit.setText("2")
        pane.run_button.click()
        wait_for_monte_carlo(window)
        assert "Success rate" in pane.metrics_label.text()
        monkeypatch.setattr(
            QInputDialog,
            "getText",
            staticmethod(lambda *_args, **_kwargs: ("what if", True)),
        )
        window.scenarios_pane.add_scenario_button.click()
        assert pane.metrics_label.isHidden()
        assert pane.monte_carlo_message_label.text() == NO_MONTE_CARLO_MESSAGE
        assert pane.chart_tabs.count() == 3
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        assert len(view.chart().series()) == 1


class TestMainWindowRetirementFlow:
    """The retirement-age search runs through the window (9.14)."""

    @pytest.fixture(name="window")
    def window_fixture(self) -> MainWindow:
        """A window with a plan carrying employment income saved.

        A 10% replacement rate keeps the search to a single probe:
        the ascending scan succeeds at the very first candidate age.
        """
        window = MainWindow(build_shell_view_model())
        facts = window.facts_pane
        facts.person_form.set_value("date_of_birth", "1966-02-01")
        facts.person_form.set_value("tax_residency", str(RUK_RESIDENCY))
        facts.person_form.set_value("target_retirement_age", "63")
        facts.person_form.set_value("employment_income", "50000")
        facts.spending_form.set_value("annual_spending_real", "18000")
        wrapper_form = facts.wrappers.add_entry()
        wrapper_form.set_value("kind", str(ISA_KIND))
        wrapper_form.set_value("balance", "300000")
        facts.submit_button.click()
        return window

    def test_retirement_search_through_the_window(self, window: MainWindow) -> None:
        """Enter a rate, press find: the answer lands on the card (9.14)."""
        pane = window.charts_pane
        pane.rate_edit.setText("10")
        pane.retirement_button.click()
        # The search executes off the GUI thread; the button stays
        # disabled and the busy indicator names the search until the
        # finished state is delivered back (9.16).
        assert not pane.retirement_button.isEnabled()
        assert not pane.busy_bar.isHidden()
        assert pane.busy_label.text() == RETIREMENT_RUNNING_MESSAGE
        wait_for_monte_carlo(window)
        assert pane.retirement_button.isEnabled()
        assert pane.busy_bar.isHidden()
        assert pane.retirement_answer_label.text().startswith(RETIREMENT_ANSWER_PREFIX)
        assert not pane.retirement_detail_label.isHidden()
        assert pane.rate_edit.text() == "10"

    def test_a_plan_change_discards_an_in_flight_search(
        self, window: MainWindow
    ) -> None:
        """An answer computed from a replaced state never lands (9.14)."""
        pane = window.charts_pane
        pane.rate_edit.setText("10")
        pane.retirement_button.click()
        window.facts_pane.submit_button.click()
        wait_for_monte_carlo(window)
        assert window.statusBar().currentMessage() == RETIREMENT_STALE_MESSAGE
        assert pane.retirement_message_label.text() == NO_RETIREMENT_MESSAGE
        assert pane.retirement_button.isEnabled()
        assert pane.busy_bar.isHidden()

    def test_the_slow_runs_share_one_in_flight_guard(self, window: MainWindow) -> None:
        """A search request during a Monte Carlo run is ignored.

        Both transitions compute from the state captured at start and
        share one worker pool, so a second launched mid-run could only
        ever be discarded as stale — both actions disable together and
        the ignored click leaves the status line on the running run.
        """
        pane = window.charts_pane
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        buttons["Monte Carlo"].click()
        pane.seed_edit.setText("7")
        pane.paths_edit.setText("2")
        pane.run_button.click()
        assert not pane.run_button.isEnabled()
        assert not pane.retirement_button.isEnabled()
        pane.retirement_button.click()
        assert window.statusBar().currentMessage() == monte_carlo_running_status("2")
        assert pane.busy_label.text() == monte_carlo_running_status("2")
        wait_for_monte_carlo(window)
        assert pane.run_button.isEnabled()
        assert pane.retirement_button.isEnabled()
        assert "Success rate" in pane.metrics_label.text()
        assert pane.retirement_message_label.text() == NO_RETIREMENT_MESSAGE


class TestMainWindowDrawdownFlow:
    """The sustainable-income search runs through the window (9.25)."""

    @pytest.fixture(name="window")
    def window_fixture(self) -> MainWindow:
        """A window with a plan whose retirement-age decision is 63."""
        window = MainWindow(build_shell_view_model())
        facts = window.facts_pane
        facts.person_form.set_value("date_of_birth", "1966-02-01")
        facts.person_form.set_value("tax_residency", str(RUK_RESIDENCY))
        facts.person_form.set_value("target_retirement_age", "63")
        facts.spending_form.set_value("annual_spending_real", "18000")
        wrapper_form = facts.wrappers.add_entry()
        wrapper_form.set_value("kind", str(ISA_KIND))
        wrapper_form.set_value("balance", "300000")
        facts.submit_button.click()
        return window

    def test_drawdown_search_through_the_window(self, window: MainWindow) -> None:
        """Choose an age, press find: the answer lands on the card (9.25)."""
        pane = window.charts_pane
        assert pane.drawdown_age_edit.text() == "63"
        pane.drawdown_button.click()
        # The search executes off the GUI thread; every slow-run
        # action disables and the busy indicator names the search
        # until the finished state is delivered back (9.16).
        assert not pane.drawdown_button.isEnabled()
        assert not pane.retirement_button.isEnabled()
        assert not pane.busy_bar.isHidden()
        assert pane.busy_label.text() == DRAWDOWN_RUNNING_MESSAGE
        wait_for_monte_carlo(window)
        assert pane.drawdown_button.isEnabled()
        assert pane.busy_bar.isHidden()
        assert pane.drawdown_answer_label.text().startswith(DRAWDOWN_ANSWER_PREFIX)
        assert not pane.drawdown_detail_label.isHidden()
        assert pane.drawdown_age_edit.text() == "63"

    def test_a_plan_change_discards_an_in_flight_search(
        self, window: MainWindow
    ) -> None:
        """An answer computed from a replaced state never lands (9.25)."""
        pane = window.charts_pane
        pane.drawdown_button.click()
        window.facts_pane.submit_button.click()
        wait_for_monte_carlo(window)
        assert window.statusBar().currentMessage() == DRAWDOWN_STALE_MESSAGE
        assert pane.drawdown_message_label.text() == NO_DRAWDOWN_MESSAGE
        assert pane.drawdown_button.isEnabled()
        assert pane.busy_bar.isHidden()


class TestMainWindowBacktestFlow:
    """The historical backtest runs through the window (9.18)."""

    @pytest.fixture(name="window")
    def window_fixture(self) -> MainWindow:
        """A window with a just-retired saver's plan already saved.

        Born 1966, so each rolling window projects a few decades at
        most and the full backtest stays quick.
        """
        window = MainWindow(build_shell_view_model())
        facts = window.facts_pane
        facts.person_form.set_value("date_of_birth", "1966-02-01")
        facts.person_form.set_value("tax_residency", str(RUK_RESIDENCY))
        facts.person_form.set_value("target_retirement_age", "60")
        facts.spending_form.set_value("annual_spending_real", "12000")
        wrapper_form = facts.wrappers.add_entry()
        wrapper_form.set_value("kind", str(ISA_KIND))
        wrapper_form.set_value("balance", "50000")
        facts.submit_button.click()
        return window

    def test_backtest_run_through_the_window(self, window: MainWindow) -> None:
        """Acceptance criterion: press Run backtest, read the metrics.

        The shell reports the share of historical starting years the
        plan survives, the worst window, and the outcome range as
        bands over the balances chart.
        """
        pane = window.charts_pane
        pane.backtest_button.click()
        # The run executes off the GUI thread; the button stays
        # disabled and the busy indicator shows until the finished
        # state is delivered back (9.16).
        assert not pane.backtest_button.isEnabled()
        assert not pane.busy_bar.isHidden()
        assert pane.busy_label.text() == BACKTEST_RUNNING_MESSAGE
        wait_for_monte_carlo(window)
        assert pane.backtest_button.isEnabled()
        assert pane.busy_bar.isHidden()
        text = pane.backtest_metrics_label.text()
        assert "Success rate" in text
        assert "Worst starting year" in text
        assert "Best starting year" in text
        view = pane.chart_tabs.widget(0)
        assert isinstance(view, QChartView)
        assert len(view.chart().series()) == 3
        # Picking a starting year draws its own trajectory and the
        # selection survives the refresh.
        pane.backtest_year_edit.setText("1973")
        pane.backtest_year_edit.editingFinished.emit()
        picked_view = pane.chart_tabs.widget(0)
        assert isinstance(picked_view, QChartView)
        assert len(picked_view.chart().series()) == 4
        assert pane.backtest_year_edit.text() == "1973"

    def test_a_plan_change_discards_an_in_flight_backtest(
        self, window: MainWindow
    ) -> None:
        """A result computed from a replaced state never lands (9.18)."""
        pane = window.charts_pane
        pane.backtest_button.click()
        window.facts_pane.submit_button.click()
        wait_for_monte_carlo(window)
        assert window.statusBar().currentMessage() == BACKTEST_STALE_MESSAGE
        assert pane.backtest_metrics_label.isHidden()
        assert pane.backtest_button.isEnabled()
        # The staleness-discard path must not leave a spinner running.
        assert pane.busy_bar.isHidden()

    def test_the_backtest_shares_the_in_flight_guard(self, window: MainWindow) -> None:
        """A Monte Carlo request during a backtest is ignored (9.16).

        Every slow-run action disables together, and the ignored click
        leaves the status line on the running backtest.
        """
        pane = window.charts_pane
        pane.backtest_button.click()
        assert not pane.run_button.isEnabled()
        assert not pane.retirement_button.isEnabled()
        pane.run_button.click()
        assert window.statusBar().currentMessage() == BACKTEST_RUNNING_MESSAGE
        wait_for_monte_carlo(window)
        assert pane.backtest_button.isEnabled()
        assert pane.run_button.isEnabled()
        assert "Success rate" in pane.backtest_metrics_label.text()


def example_projected_state() -> PlanState:
    """A projected session over the launch example's richer household."""
    parsed = parse_facts_form(
        example_facts_form_data(), recorded_on=RECORDED, today=TODAY
    )
    assert parsed.household is not None
    return state_with_household(initial_plan_state(), parsed.household, today=TODAY)


class TestChartImage:
    """The report rasteriser draws real content (issue #133; 9.19)."""

    def test_every_chart_renders_at_report_size_with_content(self) -> None:
        """Each chart rasterises to the fixed report size, non-blank.

        The PDF export embeds these images; before the never-shown
        view's layout fix, the chart rendered at its default size in a
        corner of the image, and any blank or mis-scaled render passed
        as long as the PDF file was non-empty. The dimensions pin
        ``_REPORT_CHART_SIZE``; the colour count over a sampled grid
        rejects an un-laid-out render (a healthy chart samples 30+
        distinct colours, a broken one under 10).
        """
        view_model = build_charts_view_model(example_projected_state())
        assert view_model.charts
        for chart in view_model.charts:
            image = gui_charts.chart_image(chart, view_model.categories)
            assert not image.isNull()
            assert image.width() == 880
            assert image.height() == 460
            colours = {
                image.pixel(x, y)
                for x in range(0, image.width(), 8)
                for y in range(0, image.height(), 8)
            }
            assert len(colours) >= 16, chart.title

    def test_the_fan_chart_renders_with_content_too(self) -> None:
        """The fan tab rasterises non-blank for the report (9.24).

        The fan chart carries no bar sets, so this also pins that a
        category axis without bars still lays out the fills.
        """
        state = state_with_monte_carlo(example_projected_state(), "7", "3", today=TODAY)
        view_model = build_charts_view_model(state, mode=RunMode.MONTE_CARLO)
        chart = view_model.charts[-1]
        assert chart.title == MONTE_CARLO_CHART_TITLE
        image = gui_charts.chart_image(chart, view_model.categories)
        assert not image.isNull()
        colours = {
            image.pixel(x, y)
            for x in range(0, image.width(), 8)
            for y in range(0, image.height(), 8)
        }
        assert len(colours) >= 16
