"""Historical backtest in the shell: transition and panel (issue 9.18).

The acceptance criterion: the shell reports the share of historical
starting years in which the plan succeeds, identifies the worst
window, and charts the range of outcomes; results are reproducible —
no randomness involved. The tests keep runs fast by shrinking the
horizon — a just-retired saver with the planning age overridden down
to 65 — so each window is a handful of periods.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    BACKTEST_NO_PLAN_MESSAGE,
    NO_BACKTEST_MESSAGE,
    ChartsViewModel,
    PlanState,
    ReportBasis,
    build_charts_view_model,
    initial_plan_state,
    state_with_backtest,
    state_with_household,
    state_with_override,
)
from glidepath.app.backtest import (
    BACKTEST_FAILED_PREFIX,
    BEST_WINDOW_LABEL,
    WINDOWS_LABEL,
    WORST_WINDOW_LABEL,
)
from glidepath.app.montecarlo import SUCCESS_RATE_LABEL
from glidepath.app.plan import replanned_state
from glidepath.core import (
    BacktestResult,
    Decision,
    EntityId,
    Fact,
    HistoricalSeries,
    HistoricalYear,
    Household,
    Money,
    Person,
    RunConfig,
    RunMode,
    RunProvenance,
    SpendingPlan,
    WindowOutcome,
    Wrapper,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, load_returns_history

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household(balance_as_of: date = AS_OF) -> Household:
    """A just-retired ISA saver, so the horizon stays a few periods."""
    wrapper = Wrapper(
        id=EntityId("bt-isa"),
        kind=ISA_KIND,
        balance=Fact(
            value=Money(Decimal(50000)), as_of=balance_as_of, recorded_on=RECORDED
        ),
    )
    person = Person(
        id=EntityId("bt-person"),
        date_of_birth=Fact(value=date(1966, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(wrapper,),
    )
    return Household(
        persons=(person,),
        spending=SpendingPlan(annual_spending_real=money_fact("12000")),
    )


def short_horizon_state() -> PlanState:
    """A fresh session with the planning age overridden down to 65."""
    outcome = state_with_override(
        initial_plan_state(),
        "horizon.planning_age",
        "65",
        recorded_on=RECORDED,
        today=TODAY,
    )
    assert outcome.error is None
    return outcome.state


@pytest.fixture(scope="module", name="projected")
def projected_fixture() -> PlanState:
    """One projected short-horizon session."""
    return state_with_household(short_horizon_state(), household(), today=TODAY)


@pytest.fixture(scope="module", name="bt_state")
def bt_state_fixture(projected: PlanState) -> PlanState:
    """The projected session after a full historical backtest."""
    return state_with_backtest(projected, today=TODAY)


@pytest.fixture(scope="module", name="bt_view_model")
def bt_view_model_fixture(bt_state: PlanState) -> ChartsViewModel:
    """The charts screen over the backtest state, default run mode."""
    return build_charts_view_model(bt_state)


class TestStateWithBacktest:
    """The explicit backtest transition and its failure modes."""

    def test_runs_every_complete_window_of_the_series(
        self, bt_state: PlanState
    ) -> None:
        """Acceptance criterion: one window per historical starting year."""
        assert bt_state.backtest is not None
        assert bt_state.backtest_error is None
        assert bt_state.result is not None
        series = load_returns_history().series
        periods = len(bt_state.result.snapshots)
        assert bt_state.backtest.window_count == series.length - periods + 1
        assert bt_state.backtest.outcomes[0].start_year == series.first_year

    def test_reruns_reproduce_identical_results(
        self, projected: PlanState, bt_state: PlanState
    ) -> None:
        """Acceptance criterion: reproducible, no randomness involved."""
        rerun = state_with_backtest(projected, today=TODAY)
        assert rerun.backtest is not None
        assert bt_state.backtest is not None
        assert rerun.backtest.outcomes == bt_state.backtest.outcomes

    def test_without_a_plan_reports_no_plan(self) -> None:
        """A backtest needs a captured household first."""
        state = state_with_backtest(initial_plan_state(), today=TODAY)
        assert state.backtest is None
        assert state.backtest_error == BACKTEST_NO_PLAN_MESSAGE

    def test_an_engine_rejection_folds_into_the_error(self) -> None:
        """A future-dated balance fails the run, never raises (§4.8)."""
        future_dated = household(balance_as_of=date(2027, 1, 1))
        state = state_with_household(short_horizon_state(), future_dated, today=TODAY)
        outcome = state_with_backtest(state, today=TODAY)
        assert outcome.backtest is None
        assert outcome.backtest_error is not None
        assert outcome.backtest_error.startswith(BACKTEST_FAILED_PREFIX)

    def test_a_runner_failure_folds_into_the_error(
        self, projected: PlanState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OSError past the engine's ValueErrors must never escape.

        An exception escaping the transition would hold the shell's
        shared in-flight guard forever — buttons disabled, the 9.16
        spinner running — so any failure folds into the state (§4.7).
        """

        def broken_runner(*_args: object, **_kwargs: object) -> None:
            msg = "could not read the shipped series"
            raise OSError(msg)

        monkeypatch.setattr("glidepath.app.backtest.run_windows", broken_runner)
        state = state_with_backtest(projected, today=TODAY)
        assert state.backtest is None
        assert state.backtest_error is not None
        assert state.backtest_error.startswith(BACKTEST_FAILED_PREFIX)
        assert "could not read the shipped series" in state.backtest_error

    def test_a_failure_drops_the_held_result(
        self, bt_state: PlanState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rejected re-run never leaves stale metrics on screen."""

        def broken_runner(*_args: object, **_kwargs: object) -> None:
            msg = "worker lost"
            raise OSError(msg)

        monkeypatch.setattr("glidepath.app.backtest.run_windows", broken_runner)
        state = state_with_backtest(bt_state, today=TODAY)
        assert state.backtest is None
        assert state.backtest_error is not None

    def test_a_success_clears_a_previous_error(self, projected: PlanState) -> None:
        """The error and the result never show together."""
        failed = replace(projected, backtest_error="an earlier failure")
        state = state_with_backtest(failed, today=TODAY)
        assert state.backtest is not None
        assert state.backtest_error is None

    def test_replanning_drops_the_held_result(self, bt_state: PlanState) -> None:
        """Any plan change invalidates the backtest surface."""
        state = state_with_household(bt_state, household(), today=TODAY)
        assert state.backtest is None
        assert state.backtest_error is None

    def test_the_run_re_anchors_the_base_projection(self, projected: PlanState) -> None:
        """The bands and the chart they overlay share one anchor date.

        In a session left open across a date boundary the held
        deterministic projection is anchored on the earlier day; the
        transition recomputes it at the run's own ``today`` so the
        roll-forwards and partial periods can never diverge.
        """
        later = date(2026, 9, 15)
        state = state_with_backtest(projected, today=later)
        assert state.backtest is not None
        assert state.backtest.config.today == later
        re_anchored = replanned_state(
            projected.assumptions, projected.household, (), today=later, modified=True
        )
        assert state.result == re_anchored.result
        assert state.result != projected.result


class TestBacktestPanel:
    """The backtest card's readout mirrors the Monte Carlo metrics."""

    def test_reports_the_success_rate_over_windows(
        self, bt_view_model: ChartsViewModel
    ) -> None:
        """Acceptance criterion: the share of starting years that succeed."""
        panel = bt_view_model.backtest
        assert panel.message == ""
        labels = [row.label for row in panel.metrics]
        assert SUCCESS_RATE_LABEL in labels
        success = next(r for r in panel.metrics if r.label == SUCCESS_RATE_LABEL)
        assert success.value.endswith("%")

    def test_names_the_window_span(
        self, bt_state: PlanState, bt_view_model: ChartsViewModel
    ) -> None:
        """The windows row counts the starting years and their span."""
        assert bt_state.backtest is not None
        windows = next(
            r for r in bt_view_model.backtest.metrics if r.label == WINDOWS_LABEL
        )
        count = bt_state.backtest.window_count
        first = bt_state.backtest.outcomes[0].start_year
        last = bt_state.backtest.outcomes[-1].start_year
        assert windows.value == f"{count} starting years ({first} to {last})"

    def test_identifies_the_worst_starting_year(
        self, bt_state: PlanState, bt_view_model: ChartsViewModel
    ) -> None:
        """Acceptance criterion: the worst window is named on the card."""
        assert bt_state.backtest is not None
        worst = next(
            r for r in bt_view_model.backtest.metrics if r.label == WORST_WINDOW_LABEL
        )
        assert worst.value.startswith(str(bt_state.backtest.worst_window.start_year))

    def test_identifies_the_best_starting_year(
        self, bt_state: PlanState, bt_view_model: ChartsViewModel
    ) -> None:
        """The best window is named alongside the worst."""
        assert bt_state.backtest is not None
        best = next(
            r for r in bt_view_model.backtest.metrics if r.label == BEST_WINDOW_LABEL
        )
        assert best.value.startswith(str(bt_state.backtest.best_window.start_year))

    def test_ending_pot_labels_carry_the_basis(self, bt_state: PlanState) -> None:
        """The nominal basis names itself on the pot rows."""
        view_model = build_charts_view_model(bt_state, basis=ReportBasis.NOMINAL)
        pots = [row for row in view_model.backtest.metrics if "Ending pot" in row.label]
        assert len(pots) == 3
        assert all(row.label.endswith("(nominal)") for row in pots)

    def test_the_year_picker_carries_its_explanation(
        self, bt_state: PlanState, bt_view_model: ChartsViewModel
    ) -> None:
        """The picker names its expected input and what it will draw."""
        assert bt_state.backtest is not None
        first = bt_state.backtest.outcomes[0].start_year
        last = bt_state.backtest.outcomes[-1].start_year
        panel = bt_view_model.backtest
        assert panel.year_label
        assert panel.year_placeholder == f"{first}-{last}"
        assert f"{first} to {last}" in panel.year_tooltip
        assert "balances chart" in panel.year_tooltip

    def test_without_a_result_the_picker_says_to_run_first(
        self, projected: PlanState
    ) -> None:
        """Before any run the tooltip explains the prerequisite."""
        panel = build_charts_view_model(projected).backtest
        assert panel.year_placeholder == ""
        assert "Run the backtest first" in panel.year_tooltip

    def test_no_run_shows_the_empty_state_copy(self, projected: PlanState) -> None:
        """Before any run the card carries the no-backtest message."""
        view_model = build_charts_view_model(projected)
        assert view_model.backtest.metrics == ()
        assert view_model.backtest.message == NO_BACKTEST_MESSAGE

    def test_an_error_shows_on_the_card(self) -> None:
        """A failure message displaces the metrics."""
        state = state_with_backtest(initial_plan_state(), today=TODAY)
        view_model = build_charts_view_model(state)
        assert view_model.backtest.metrics == ()
        assert view_model.backtest.message == BACKTEST_NO_PLAN_MESSAGE


class TestBacktestBands:
    """The outcome range charts as actual window trajectories."""

    def test_a_held_backtest_draws_the_worst_and_best_paths(
        self, bt_state: PlanState, bt_view_model: ChartsViewModel
    ) -> None:
        """Acceptance criterion: the range charts as real trajectories.

        The worst and best starting years' actual balance paths, each
        labelled with its year — not pointwise percentile bands, which
        follow no single history.
        """
        assert bt_state.backtest is not None
        worst_year = bt_state.backtest.worst_window.start_year
        best_year = bt_state.backtest.best_window.start_year
        labels = [band.label for band in bt_view_model.charts[0].bands]
        assert labels == [
            f"Worst start · {worst_year}",
            f"Best start · {best_year}",
        ]

    def test_a_picked_starting_year_adds_its_path(self, bt_state: PlanState) -> None:
        """Typing a starting year draws that window's own trajectory."""
        view_model = build_charts_view_model(bt_state, backtest_year="1973")
        labels = [band.label for band in view_model.charts[0].bands]
        assert labels[2] == "Start · 1973"
        assert view_model.backtest.year_value == "1973"
        assert view_model.backtest.year_message == ""

    def test_a_missed_starting_year_says_the_range(self, bt_state: PlanState) -> None:
        """A year outside the windows draws nothing and names the span."""
        assert bt_state.backtest is not None
        first = bt_state.backtest.outcomes[0].start_year
        last = bt_state.backtest.outcomes[-1].start_year
        view_model = build_charts_view_model(bt_state, backtest_year="1066")
        assert len(view_model.charts[0].bands) == 2
        message = view_model.backtest.year_message
        assert "1066" in message
        assert f"{first} to {last}" in message

    def test_an_unparseable_year_says_the_range_too(self, bt_state: PlanState) -> None:
        """Non-numeric text is a miss, not an error."""
        view_model = build_charts_view_model(bt_state, backtest_year="dunkirk")
        assert len(view_model.charts[0].bands) == 2
        assert "dunkirk" in view_model.backtest.year_message

    def test_trajectories_survive_the_monte_carlo_mode(
        self, bt_state: PlanState
    ) -> None:
        """With no Monte Carlo run held, the backtest paths still draw."""
        view_model = build_charts_view_model(bt_state, mode=RunMode.MONTE_CARLO)
        assert len(view_model.charts[0].bands) == 2

    def test_misaligned_hand_built_outcomes_draw_no_lines(
        self, projected: PlanState
    ) -> None:
        """A drawn outcome with the wrong period count draws nothing.

        ``BacktestResult`` permits hand-built outcomes with differing
        balance counts; only the runner guarantees alignment. Every
        drawn trajectory is validated, so a misaligned best window
        can never crash view-model construction.
        """
        assert projected.result is not None
        periods = len(projected.result.snapshots)
        aligned = tuple(Money(Decimal(0)) for _ in range(periods))
        worst = WindowOutcome(
            window=0,
            start_year=1900,
            first_shortfall_period=projected.result.snapshots[0].period,
            ending_balance=aligned[-1],
            closing_balances=aligned,
        )
        best = WindowOutcome(
            window=1,
            start_year=1901,
            first_shortfall_period=None,
            ending_balance=Money(Decimal(100)),
            closing_balances=(Money(Decimal(100)),),
        )
        result = BacktestResult(
            outcomes=(worst, best),
            config=RunConfig(today=TODAY),
            provenance=RunProvenance(
                facts=(),
                decisions=(),
                assumptions=(),
                region_data_version="test",
                seed=None,
            ),
            series=HistoricalSeries(
                years=(
                    HistoricalYear(
                        year=1900,
                        equity=Decimal(0),
                        bonds=Decimal(0),
                        cash=Decimal(0),
                        cpi=Decimal(0),
                    ),
                    HistoricalYear(
                        year=1901,
                        equity=Decimal(0),
                        bonds=Decimal(0),
                        cash=Decimal(0),
                        cpi=Decimal(0),
                    ),
                )
            ),
        )
        state = replace(projected, backtest=result)
        view_model = build_charts_view_model(state)
        assert view_model.charts[0].bands == ()

    def test_no_backtest_no_bands(self, projected: PlanState) -> None:
        """Without a held result the balances chart draws bars alone."""
        view_model = build_charts_view_model(projected)
        assert view_model.charts[0].bands == ()
