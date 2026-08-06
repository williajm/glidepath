"""Monte Carlo in the shell: run mode, transition, panel (issue 9.13).

The acceptance criterion: the shell can run the plan in Monte Carlo
mode with a chosen seed and path count, shows success metrics, and
charts percentile bands; same seed + inputs reproduce identical
results (§4.6). The tests keep runs fast by shrinking the horizon —
a just-retired saver with the planning age overridden down to 65.
"""

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    MONTE_CARLO_NO_PLAN_MESSAGE,
    MONTE_CARLO_PATHS_MESSAGE,
    MONTE_CARLO_RUNNING_MESSAGE,
    MONTE_CARLO_SEED_MESSAGE,
    NO_MONTE_CARLO_MESSAGE,
    ChartsViewModel,
    PlanState,
    ReportBasis,
    build_charts_view_model,
    initial_plan_state,
    monte_carlo_running_status,
    path_pool,
    run_mode_from_key,
    run_mode_key,
    state_with_household,
    state_with_monte_carlo,
    state_with_override,
)
from glidepath.app.montecarlo import (
    MAX_POOL_WORKERS,
    MONTE_CARLO_FAILED_PREFIX,
    PARALLEL_PATHS_MIN,
)
from glidepath.app.plan import replanned_state, state_marked_saved
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
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household(balance_as_of: date = AS_OF) -> Household:
    """A just-retired ISA saver, so the horizon stays a few periods."""
    wrapper = Wrapper(
        id=EntityId("mc-isa"),
        kind=ISA_KIND,
        balance=Fact(
            value=Money(Decimal(50000)), as_of=balance_as_of, recorded_on=RECORDED
        ),
    )
    person = Person(
        id=EntityId("mc-person"),
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


@pytest.fixture(scope="module", name="mc_state")
def mc_state_fixture(projected: PlanState) -> PlanState:
    """The projected session after a 3-path seeded Monte Carlo run."""
    return state_with_monte_carlo(projected, "7", "3", today=TODAY)


@pytest.fixture(scope="module", name="mc_view_model")
def mc_view_model_fixture(mc_state: PlanState) -> ChartsViewModel:
    """The charts screen over the Monte Carlo state, Monte Carlo mode."""
    return build_charts_view_model(mc_state, mode=RunMode.MONTE_CARLO)


class TestRunModeKeys:
    """The run-mode control keys map to the core run modes."""

    def test_deterministic_key_maps_to_deterministic(self) -> None:
        """The deterministic option key selects the deterministic mode."""
        assert run_mode_from_key("deterministic") is RunMode.DETERMINISTIC

    def test_monte_carlo_key_maps_to_monte_carlo(self) -> None:
        """The Monte Carlo option key selects the Monte Carlo mode."""
        assert run_mode_from_key("monte_carlo") is RunMode.MONTE_CARLO

    def test_unknown_key_is_rejected(self) -> None:
        """A key the app layer never issued is an error."""
        with pytest.raises(ValueError, match="unknown run mode key"):
            run_mode_from_key("imaginary")

    def test_key_round_trips(self) -> None:
        """``run_mode_key`` is the inverse of ``run_mode_from_key``."""
        assert run_mode_key(RunMode.MONTE_CARLO) == "monte_carlo"
        assert run_mode_from_key(run_mode_key(RunMode.DETERMINISTIC)) is (
            RunMode.DETERMINISTIC
        )


class TestStateWithMonteCarlo:
    """The explicit Monte Carlo transition and its failure modes."""

    def test_runs_the_chosen_seed_and_path_count(self, mc_state: PlanState) -> None:
        """Acceptance criterion: a chosen seed and path count run."""
        assert mc_state.monte_carlo is not None
        assert mc_state.monte_carlo_error is None
        assert mc_state.monte_carlo.path_count == 3
        assert mc_state.monte_carlo.config.seed == 7

    def test_same_seed_and_inputs_reproduce_identical_results(
        self, projected: PlanState, mc_state: PlanState
    ) -> None:
        """Acceptance criterion: reproducibility (§4.6)."""
        rerun = state_with_monte_carlo(projected, "7", "3", today=TODAY)
        assert rerun.monte_carlo is not None
        assert mc_state.monte_carlo is not None
        assert rerun.monte_carlo.outcomes == mc_state.monte_carlo.outcomes

    def test_without_a_plan_reports_no_plan(self) -> None:
        """Monte Carlo needs a captured household first."""
        state = state_with_monte_carlo(initial_plan_state(), "7", "3", today=TODAY)
        assert state.monte_carlo is None
        assert state.monte_carlo_error == MONTE_CARLO_NO_PLAN_MESSAGE

    def test_a_run_carries_the_unsaved_changes_flag_through(
        self, projected: PlanState, mc_state: PlanState
    ) -> None:
        """A run reads the plan, it does not edit it (issue #136).

        Unsaved edits stay unsaved through a run, and a freshly saved
        plan stays clean — the run must never invent a save prompt or
        hide a pending one.
        """
        assert projected.modified is True
        assert mc_state.modified is True
        clean_rerun = state_with_monte_carlo(
            state_marked_saved(projected), "7", "3", today=TODAY
        )
        assert clean_rerun.monte_carlo is not None
        assert clean_rerun.modified is False

    def test_an_unparseable_seed_is_rejected(self, projected: PlanState) -> None:
        """A seed that is not a whole number cannot seed the streams."""
        state = state_with_monte_carlo(projected, "lucky", "3", today=TODAY)
        assert state.monte_carlo is None
        assert state.monte_carlo_error == MONTE_CARLO_SEED_MESSAGE

    @pytest.mark.parametrize("paths_text", ["many", "0", "-3", "10001"])
    def test_an_unusable_path_count_is_rejected(
        self, projected: PlanState, paths_text: str
    ) -> None:
        """Path counts are whole numbers from 1 to the cap."""
        state = state_with_monte_carlo(projected, "7", paths_text, today=TODAY)
        assert state.monte_carlo is None
        assert state.monte_carlo_error == MONTE_CARLO_PATHS_MESSAGE

    def test_an_engine_rejection_folds_into_the_error(self) -> None:
        """A future-dated balance fails the run, never raises (§4.8)."""
        future_dated = household(balance_as_of=date(2027, 1, 1))
        state = state_with_household(short_horizon_state(), future_dated, today=TODAY)
        outcome = state_with_monte_carlo(state, "7", "2", today=TODAY)
        assert outcome.monte_carlo is None
        assert outcome.monte_carlo_error is not None
        assert outcome.monte_carlo_error.startswith(MONTE_CARLO_FAILED_PREFIX)

    def test_a_process_boundary_failure_folds_into_the_error(
        self, projected: PlanState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OSError past the engine's ValueErrors must never escape.

        An exception escaping the transition would hold the shell's
        shared in-flight guard forever — buttons disabled, the 9.16
        spinner running — so any failure folds into the state (§4.7).
        """

        def broken_runner(*_args: object, **_kwargs: object) -> None:
            msg = "could not spawn worker processes"
            raise OSError(msg)

        monkeypatch.setattr("glidepath.app.montecarlo.run_paths", broken_runner)
        state = state_with_monte_carlo(projected, "7", "3", today=TODAY)
        assert state.monte_carlo is None
        assert state.monte_carlo_error is not None
        assert state.monte_carlo_error.startswith(MONTE_CARLO_FAILED_PREFIX)
        assert "could not spawn worker processes" in state.monte_carlo_error

    def test_a_failure_drops_the_held_result(self, mc_state: PlanState) -> None:
        """A rejected re-run never leaves stale metrics on screen."""
        state = state_with_monte_carlo(mc_state, "lucky", "3", today=TODAY)
        assert state.monte_carlo is None
        assert state.monte_carlo_error == MONTE_CARLO_SEED_MESSAGE

    def test_a_success_clears_a_previous_error(self, projected: PlanState) -> None:
        """The error and the result never show together."""
        failed = state_with_monte_carlo(projected, "lucky", "3", today=TODAY)
        state = state_with_monte_carlo(failed, "7", "3", today=TODAY)
        assert state.monte_carlo is not None
        assert state.monte_carlo_error is None

    def test_replanning_drops_the_held_result(self, mc_state: PlanState) -> None:
        """Any plan change invalidates the Monte Carlo surface."""
        state = state_with_household(mc_state, household(), today=TODAY)
        assert state.monte_carlo is None
        assert state.monte_carlo_error is None

    def test_the_run_re_anchors_the_base_projection(self, projected: PlanState) -> None:
        """The bands and the chart they overlay share one anchor date.

        In a session left open across a date boundary the held
        deterministic projection is anchored on the earlier day; the
        transition recomputes it at the run's own ``today`` so the
        roll-forwards and partial periods can never diverge.
        """
        later = date(2026, 9, 15)
        state = state_with_monte_carlo(projected, "7", "2", today=later)
        assert state.monte_carlo is not None
        assert state.monte_carlo.config.today == later
        re_anchored = replanned_state(
            projected.assumptions, projected.household, (), today=later, modified=True
        )
        assert state.result == re_anchored.result
        assert state.result != projected.result
        view_model = build_charts_view_model(state, mode=RunMode.MONTE_CARLO)
        assert len(view_model.charts[0].bands) == 3


class TestMonteCarloPanel:
    """The run-mode control and success-metrics readout."""

    def test_deterministic_mode_hides_the_controls(self, mc_state: PlanState) -> None:
        """The default mode carries no Monte Carlo surface."""
        view_model = build_charts_view_model(mc_state)
        panel = view_model.monte_carlo
        assert panel.selected_mode_key == "deterministic"
        assert not panel.controls_visible
        assert panel.metrics == ()
        assert panel.message == ""

    def test_mode_options_cover_both_run_modes(
        self, mc_view_model: ChartsViewModel
    ) -> None:
        """Deterministic and Monte Carlo, in that order."""
        keys = [option.key for option in mc_view_model.monte_carlo.mode_options]
        assert keys == ["deterministic", "monte_carlo"]

    def test_no_run_yet_shows_the_no_run_message(self, projected: PlanState) -> None:
        """Monte Carlo mode before any run explains what to do."""
        view_model = build_charts_view_model(projected, mode=RunMode.MONTE_CARLO)
        panel = view_model.monte_carlo
        assert panel.controls_visible
        assert panel.metrics == ()
        assert panel.message == NO_MONTE_CARLO_MESSAGE
        assert panel.seed_value == "1"
        assert panel.paths_value == "100"

    def test_metrics_read_out_the_success_signals(
        self, mc_view_model: ChartsViewModel
    ) -> None:
        """Acceptance criterion: the shell shows success metrics."""
        panel = mc_view_model.monte_carlo
        labels = [metric.label for metric in panel.metrics]
        assert labels == [
            "Success rate",
            "Probability of ruin",
            "Ending pot, 10th percentile (today's money)",
            "Ending pot, median (today's money)",
            "Ending pot, 90th percentile (today's money)",
        ]
        assert all(metric.value for metric in panel.metrics)
        assert panel.message == ""

    def test_the_panel_echoes_the_run_inputs(
        self, mc_view_model: ChartsViewModel
    ) -> None:
        """Seed and path count echo the held result's actual inputs."""
        panel = mc_view_model.monte_carlo
        assert panel.seed_value == "7"
        assert panel.paths_value == "3"

    def test_nominal_basis_names_itself_on_the_pot_labels(
        self, mc_state: PlanState
    ) -> None:
        """Ending pots present in either money basis."""
        view_model = build_charts_view_model(
            mc_state, basis=ReportBasis.NOMINAL, mode=RunMode.MONTE_CARLO
        )
        labels = [metric.label for metric in view_model.monte_carlo.metrics]
        assert "Ending pot, median (nominal)" in labels

    def test_a_failure_message_reaches_the_panel(self, projected: PlanState) -> None:
        """The stored error is the panel's message."""
        failed = state_with_monte_carlo(projected, "lucky", "3", today=TODAY)
        view_model = build_charts_view_model(failed, mode=RunMode.MONTE_CARLO)
        assert view_model.monte_carlo.message == MONTE_CARLO_SEED_MESSAGE

    def test_a_result_without_a_projection_reports_no_run(
        self, mc_state: PlanState
    ) -> None:
        """No projection means no deflator to present the pots with."""
        assert mc_state.monte_carlo is not None
        orphaned = replace(initial_plan_state(), monte_carlo=mc_state.monte_carlo)
        view_model = build_charts_view_model(orphaned, mode=RunMode.MONTE_CARLO)
        assert view_model.monte_carlo.metrics == ()
        assert view_model.monte_carlo.message == NO_MONTE_CARLO_MESSAGE


class TestBalanceBands:
    """The percentile bands over the balances chart."""

    def test_monte_carlo_mode_draws_three_bands(
        self, mc_view_model: ChartsViewModel
    ) -> None:
        """Acceptance criterion: the shell charts percentile bands."""
        bands = mc_view_model.charts[0].bands
        assert [band.label for band in bands] == [
            "10th percentile",
            "Median",
            "90th percentile",
        ]
        for band in bands:
            assert len(band.values) == len(mc_view_model.categories)
            assert all(value >= 0 for value in band.values)

    def test_only_the_balances_chart_carries_bands(
        self, mc_view_model: ChartsViewModel
    ) -> None:
        """Income and tax chart nothing but their own stacks."""
        assert mc_view_model.charts[1].bands == ()
        assert mc_view_model.charts[2].bands == ()

    def test_deterministic_mode_draws_no_bands(self, mc_state: PlanState) -> None:
        """Bands appear only under the Monte Carlo presentation."""
        view_model = build_charts_view_model(mc_state)
        assert all(chart.bands == () for chart in view_model.charts)

    def test_the_axis_covers_the_bands(self, mc_view_model: ChartsViewModel) -> None:
        """A band above the deterministic stack still fits the chart."""
        chart = mc_view_model.charts[0]
        peak = max(max(band.values) for band in chart.bands)
        assert chart.y_axis_max >= peak

    def test_bands_present_in_the_nominal_basis_too(self, mc_state: PlanState) -> None:
        """Acceptance criterion: bands in either money basis."""
        view_model = build_charts_view_model(
            mc_state, basis=ReportBasis.NOMINAL, mode=RunMode.MONTE_CARLO
        )
        bands = view_model.charts[0].bands
        assert len(bands) == 3

    def test_zero_volatility_collapses_the_bands_together(self) -> None:
        """With no volatility every path repeats the deterministic run."""
        state = short_horizon_state()
        for key in ("volatility.equity", "volatility.bonds", "volatility.cash"):
            outcome = state_with_override(
                state, key, "0", recorded_on=RECORDED, today=TODAY
            )
            assert outcome.error is None
            state = outcome.state
        state = state_with_household(state, household(), today=TODAY)
        state = state_with_monte_carlo(state, "7", "3", today=TODAY)
        view_model = build_charts_view_model(state, mode=RunMode.MONTE_CARLO)
        low, median, high = view_model.charts[0].bands
        assert low.values == median.values
        assert high.values == median.values

    def test_a_result_over_different_periods_draws_no_bands(
        self, mc_state: PlanState
    ) -> None:
        """A held result must align with the projection it overlays."""
        assert mc_state.monte_carlo is not None
        result = mc_state.monte_carlo
        truncated = tuple(
            replace(
                outcome,
                closing_balances=outcome.closing_balances[:1],
                ending_balance=outcome.closing_balances[0],
            )
            for outcome in result.outcomes
        )
        mismatched = replace(result, outcomes=truncated)
        state = replace(mc_state, monte_carlo=mismatched)
        view_model = build_charts_view_model(state, mode=RunMode.MONTE_CARLO)
        assert view_model.charts[0].bands == ()


class TestMonteCarloRunningStatus:
    """The busy status line for a run just started (issue 9.16)."""

    def test_names_the_path_count(self) -> None:
        """A parseable count lands in the copy, grouped for reading."""
        status = monte_carlo_running_status("1000")
        assert status == "Running Monte Carlo — 1,000 paths…"

    def test_unparseable_text_falls_back_to_the_plain_message(self) -> None:
        """The transition, not the status line, reports unusable input."""
        status = monte_carlo_running_status("many")
        assert status == MONTE_CARLO_RUNNING_MESSAGE

    def test_an_out_of_range_count_falls_back_to_the_plain_message(self) -> None:
        """A count the transition will reject is never echoed as running."""
        status = monte_carlo_running_status("20000")
        assert status == MONTE_CARLO_RUNNING_MESSAGE


class TestPathPool:
    """The app layer's parallelism policy (planning §5.2)."""

    def test_small_runs_stay_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Below the threshold, process startup would outweigh the run."""
        monkeypatch.setattr(os, "process_cpu_count", lambda: 8)
        with path_pool(PARALLEL_PATHS_MIN - 1) as parallelism:
            assert parallelism is None

    def test_a_single_spare_core_stays_serial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With one core left for the GUI there is no pool to fill."""
        monkeypatch.setattr(os, "process_cpu_count", lambda: 2)
        with path_pool(10_000) as parallelism:
            assert parallelism is None

    def test_an_unknown_cpu_count_stays_serial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No core count to size a pool from means no pool."""
        monkeypatch.setattr(os, "process_cpu_count", lambda: None)
        with path_pool(10_000) as parallelism:
            assert parallelism is None

    def test_a_big_run_gets_a_pool_of_the_spare_cores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At the threshold the pool sizes to every core but one.

        The executor spawns workers lazily on first submit, so entering
        and leaving the context without work keeps this test cheap; the
        core suite drives real submissions.
        """
        monkeypatch.setattr(os, "process_cpu_count", lambda: 3)
        with path_pool(PARALLEL_PATHS_MIN) as parallelism:
            assert parallelism is not None
            assert parallelism.workers == 2

    def test_a_many_core_machine_is_capped_at_the_windows_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """63+ logical CPUs must not ask for a pool Windows rejects.

        ``ProcessPoolExecutor`` raises for more than 61 workers on
        Windows; uncapped, every qualifying run on such a machine
        would fail at pool construction.
        """
        monkeypatch.setattr(os, "process_cpu_count", lambda: 128)
        with path_pool(10_000) as parallelism:
            assert parallelism is not None
            assert parallelism.workers == MAX_POOL_WORKERS
