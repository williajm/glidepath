"""Projection chart widget tests, offscreen (issue 8.4, §4.7).

The pane is thin by policy, so these tests only check the bindings:
chart sub-tabs render from the view model, the empty-state message
gates them, and the basis toggle forwards the selected key back.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from PySide6.QtCharts import QBarSet, QChartView, QStackedBarSeries
from PySide6.QtWidgets import QRadioButton

from glidepath.app import (
    ChartsViewModel,
    bar_tooltip,
    build_charts_view_model,
    build_shell_view_model,
    initial_plan_state,
    state_with_household,
)
from glidepath.core import (
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    SpendingPlan,
    Wrapper,
)
from glidepath.gui import charts as gui_charts
from glidepath.gui.charts import ChartsPane
from glidepath.gui.widgets import MainWindow
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def projected_view_model() -> ChartsViewModel:
    """A charts view model over one projected ISA saver."""
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
    state = state_with_household(initial_plan_state(), plan, today=TODAY)
    return build_charts_view_model(state)


class TestChartsPane:
    """The pane binds the view model and forwards the basis toggle."""

    def test_empty_view_model_shows_only_the_message(self) -> None:
        """No charts yet: the message shows and the sub-tabs hide."""
        pane = ChartsPane(lambda _key: None)
        view_model = build_charts_view_model(initial_plan_state())
        pane.refresh(view_model)
        assert pane.message_label.text() == view_model.message
        assert not pane.message_label.isHidden()
        assert pane.chart_tabs.isHidden()
        assert pane.chart_tabs.count() == 0

    def test_projected_view_model_renders_one_tab_per_chart(self) -> None:
        """Each chart binds to a sub-tab titled from the view model."""
        pane = ChartsPane(lambda _key: None)
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
        pane = ChartsPane(lambda _key: None)
        view_model = projected_view_model()
        pane.refresh(view_model)
        pane.refresh(view_model)
        assert pane.chart_tabs.count() == len(view_model.charts)

    def test_refresh_keeps_the_selected_chart(self) -> None:
        """Rebuilding the sub-tabs must not move the user off a chart."""
        pane = ChartsPane(lambda _key: None)
        view_model = projected_view_model()
        pane.refresh(view_model)
        pane.chart_tabs.setCurrentIndex(2)
        pane.refresh(view_model)
        assert pane.chart_tabs.currentIndex() == 2

    def test_basis_toggle_forwards_the_option_key(self) -> None:
        """Clicking a basis radio reports its key to the shell handler."""
        selected: list[str] = []
        pane = ChartsPane(selected.append)
        view_model = projected_view_model()
        pane.refresh(view_model)
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        labels = {option.key: option.label for option in view_model.basis_options}
        assert buttons[labels["real"]].isChecked()
        buttons[labels["nominal"]].click()
        assert selected == ["nominal"]


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
        pane = ChartsPane(lambda _key: None)
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
        pane = ChartsPane(lambda _key: None)
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
        pane = ChartsPane(lambda _key: None)
        pane.refresh(projected_view_model())
        bar_set = _first_bar_set(pane)
        hovering = True
        bar_set.hovered.emit(hovering, -1)
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
