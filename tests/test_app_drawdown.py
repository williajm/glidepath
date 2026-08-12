"""The "How much can I draw down?" card: transition and panel (9.25).

The acceptance criterion: given a plan, the shell reports the highest
net annual income (today's money) the plan sustains when retiring at
the chosen age, consistent with the core solver over the same inputs;
the search is deterministic. The tests keep runs fast by shrinking the
horizon — a 60-year-old saver with the planning age overridden down
to 65, so valid retirement ages run 60 to 64.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from factories import money_fact
from glidepath.app import (
    DRAWDOWN_AGE_MESSAGE,
    DRAWDOWN_NO_PLAN_MESSAGE,
    DRAWDOWN_SUCCESS_MESSAGE,
    MONTE_CARLO_PATHS_MESSAGE,
    MONTE_CARLO_SEED_MESSAGE,
    NO_DRAWDOWN_MESSAGE,
    DrawdownAnswer,
    DrawdownRequest,
    PlanState,
    build_charts_view_model,
    build_drawdown_panel,
    initial_plan_state,
    state_with_drawdown,
    state_with_household,
    state_with_override,
)
from glidepath.app.drawdown import (
    DEFAULT_DRAWDOWN_SUCCESS_VALUE,
    DRAWDOWN_ANSWER_PREFIX,
    DRAWDOWN_BUDGET_MESSAGE,
    DRAWDOWN_DETERMINISTIC_BASIS,
    DRAWDOWN_FAILED_PREFIX,
    DRAWDOWN_HEADING,
    DRAWDOWN_HORIZON_MESSAGE,
    DRAWDOWN_PERSON_LABEL,
    DRAWDOWN_PERSON_MESSAGE,
    DRAWDOWN_SEARCH_MAXIMUM,
)
from glidepath.app.plan import region_for, replanned_state
from glidepath.core import (
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    RunConfig,
    RunMode,
    SpendingPlan,
    SustainableIncomeSearch,
    Wrapper,
    sustainable_income_at_age,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
DETERMINISTIC_REQUEST = DrawdownRequest(mode=RunMode.DETERMINISTIC, age_text="63")
MONTE_CARLO_REQUEST = DrawdownRequest(
    mode=RunMode.MONTE_CARLO,
    age_text="63",
    seed_text="7",
    paths_text="2",
    success_text="90",
)


def household(date_of_birth: date = date(1966, 2, 1)) -> Household:
    """A 60-year-old ISA saver, so the horizon stays a few periods."""
    wrapper = Wrapper(
        id=EntityId("drawdown-isa"),
        kind=ISA_KIND,
        balance=money_fact("300000"),
    )
    person = Person(
        id=EntityId("drawdown-person"),
        date_of_birth=Fact(value=date_of_birth, as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=63, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=money_fact("50000"),
        wrappers=(wrapper,),
    )
    return Household(
        persons=(person,),
        spending=SpendingPlan(annual_spending_real=money_fact("12000")),
    )


def couple() -> Household:
    """The saver joined by a 58-year-old partner (planning §4.11)."""
    base = household()
    partner = Person(
        id=EntityId("drawdown-partner"),
        date_of_birth=Fact(value=date(1968, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=63, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
    )
    return Household(persons=(*base.persons, partner), spending=base.spending)


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


@pytest.fixture(scope="module", name="couple_projected")
def couple_projected_fixture() -> PlanState:
    """One projected short-horizon session over the couple."""
    return state_with_household(short_horizon_state(), couple(), today=TODAY)


@pytest.fixture(scope="module", name="solved")
def solved_fixture(projected: PlanState) -> PlanState:
    """The projected session after a deterministic search at age 63."""
    return state_with_drawdown(projected, DETERMINISTIC_REQUEST, today=TODAY)


class TestStateWithDrawdown:
    """The explicit search transition and its failure modes."""

    def test_holds_the_answer_with_its_inputs(self, solved: PlanState) -> None:
        """Acceptance criterion: the shell reports the income."""
        answer = solved.drawdown
        assert answer is not None
        assert solved.drawdown_error is None
        assert answer.income is not None
        assert answer.income > Money(Decimal(0))
        assert answer.age == 63
        assert answer.maximum == DRAWDOWN_SEARCH_MAXIMUM
        assert answer.mode is RunMode.DETERMINISTIC
        assert answer.seed is None
        assert answer.paths is None
        assert answer.target_success_rate is None

    def test_agrees_with_the_core_solver(self, solved: PlanState) -> None:
        """Acceptance criterion: consistent with the core over the inputs.

        The held income is exactly what the core solver answers for
        the same plan, assumptions, and derived search — and the
        core's own tests pin that answer to a re-run at that level.
        """
        assert solved.drawdown is not None
        assert solved.household is not None
        expected = sustainable_income_at_age(
            solved.household,
            solved.assumptions,
            region_for(solved.assumptions),
            RunConfig(today=TODAY),
            age=63,
            search=SustainableIncomeSearch(maximum=DRAWDOWN_SEARCH_MAXIMUM),
        )
        assert solved.drawdown.income == expected

    def test_the_transition_is_deterministic(
        self, projected: PlanState, solved: PlanState
    ) -> None:
        """Acceptance criterion: the same inputs answer the same income."""
        rerun = state_with_drawdown(projected, DETERMINISTIC_REQUEST, today=TODAY)
        assert rerun.drawdown is not None
        assert solved.drawdown is not None
        assert rerun.drawdown == solved.drawdown

    def test_without_a_plan_reports_no_plan(self) -> None:
        """The search needs a captured household first."""
        state = state_with_drawdown(
            initial_plan_state(), DETERMINISTIC_REQUEST, today=TODAY
        )
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_NO_PLAN_MESSAGE

    @pytest.mark.parametrize("age_text", ["", "sixty", "63.5", "59", "65", "-1"])
    def test_an_unusable_age_is_rejected(
        self, projected: PlanState, age_text: str
    ) -> None:
        """Ages are whole numbers from the current age to horizon - 1."""
        request = replace(DETERMINISTIC_REQUEST, age_text=age_text)
        state = state_with_drawdown(projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_AGE_MESSAGE

    def test_a_person_past_the_horizon_leaves_no_ages(self) -> None:
        """A planning age already attained brackets nothing."""
        aged = state_with_household(
            short_horizon_state(),
            household(date_of_birth=date(1960, 2, 1)),
            today=TODAY,
        )
        state = state_with_drawdown(aged, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_HORIZON_MESSAGE

    def test_a_monte_carlo_basis_carries_its_inputs(self, projected: PlanState) -> None:
        """The seed, path count, and success target land on the answer."""
        state = state_with_drawdown(projected, MONTE_CARLO_REQUEST, today=TODAY)
        answer = state.drawdown
        assert answer is not None
        assert answer.mode is RunMode.MONTE_CARLO
        assert answer.seed == 7
        assert answer.paths == 2
        assert answer.target_success_rate == Decimal("0.9")

    def test_an_unparseable_seed_is_rejected(self, projected: PlanState) -> None:
        """The Monte Carlo basis reuses the panel's seed rules."""
        request = replace(MONTE_CARLO_REQUEST, seed_text="lucky")
        state = state_with_drawdown(projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == MONTE_CARLO_SEED_MESSAGE

    @pytest.mark.parametrize("paths_text", ["many", "0", "-3", "10001"])
    def test_an_unusable_path_count_is_rejected(
        self, projected: PlanState, paths_text: str
    ) -> None:
        """The Monte Carlo basis reuses the panel's path-count rules."""
        request = replace(MONTE_CARLO_REQUEST, paths_text=paths_text)
        state = state_with_drawdown(projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == MONTE_CARLO_PATHS_MESSAGE

    def test_an_over_budget_monte_carlo_search_is_rejected(
        self, projected: PlanState
    ) -> None:
        """The probe bound times paths is bounded, not paths alone.

        10,000 paths pass the per-run cap, but across the search's
        probed spending levels they would project far more than the
        budget allows.
        """
        request = replace(MONTE_CARLO_REQUEST, paths_text="10000")
        state = state_with_drawdown(projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_BUDGET_MESSAGE

    @pytest.mark.parametrize("success_text", ["", "0", "101", "ninety"])
    def test_an_unusable_success_target_is_rejected(
        self, projected: PlanState, success_text: str
    ) -> None:
        """Success targets are whole percentages from 1 to 100."""
        request = replace(MONTE_CARLO_REQUEST, success_text=success_text)
        state = state_with_drawdown(projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_SUCCESS_MESSAGE

    def test_an_engine_rejection_folds_into_the_error(self) -> None:
        """A future-dated balance fails the search, never raises (§4.8)."""
        future = replace(
            household(),
            persons=(
                replace(
                    household().persons[0],
                    wrappers=(
                        Wrapper(
                            id=EntityId("drawdown-isa"),
                            kind=ISA_KIND,
                            balance=Fact(
                                value=Money(Decimal(300000)),
                                as_of=date(2027, 1, 1),
                                recorded_on=RECORDED,
                            ),
                        ),
                    ),
                ),
            ),
        )
        state = state_with_household(short_horizon_state(), future, today=TODAY)
        outcome = state_with_drawdown(state, DETERMINISTIC_REQUEST, today=TODAY)
        assert outcome.drawdown is None
        assert outcome.drawdown_error is not None
        assert outcome.drawdown_error.startswith(DRAWDOWN_FAILED_PREFIX)

    def test_a_failure_drops_the_held_answer(self, solved: PlanState) -> None:
        """A rejected re-run never leaves a stale answer on screen."""
        request = replace(DETERMINISTIC_REQUEST, age_text="nope")
        state = state_with_drawdown(solved, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_AGE_MESSAGE

    def test_a_process_boundary_failure_folds_into_the_error(
        self, projected: PlanState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OSError past the engine's ValueErrors must never escape.

        An exception escaping the transition would hold the shell's
        shared in-flight guard forever — buttons disabled, the 9.16
        spinner running — so any failure folds into the state (§4.7).
        """

        def broken_solver(*_args: object, **_kwargs: object) -> int:
            msg = "could not spawn worker processes"
            raise OSError(msg)

        monkeypatch.setattr(
            "glidepath.app.drawdown.sustainable_income_at_age", broken_solver
        )
        state = state_with_drawdown(projected, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error is not None
        assert state.drawdown_error.startswith(DRAWDOWN_FAILED_PREFIX)
        assert "could not spawn worker processes" in state.drawdown_error

    def test_a_success_clears_a_previous_error(self, projected: PlanState) -> None:
        """The error and the answer never show together."""
        request = replace(DETERMINISTIC_REQUEST, age_text="nope")
        failed = state_with_drawdown(projected, request, today=TODAY)
        state = state_with_drawdown(failed, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.drawdown is not None
        assert state.drawdown_error is None

    def test_replanning_drops_the_held_answer(self, solved: PlanState) -> None:
        """Any plan change invalidates the held answer."""
        state = state_with_household(solved, household(), today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error is None

    def test_the_search_re_anchors_the_base_projection(
        self, projected: PlanState
    ) -> None:
        """The answer and the projection share one anchor date.

        In a session left open across a date boundary the held
        projection is anchored on the earlier day; the transition
        recomputes it at the search's own ``today``, dropping any held
        Monte Carlo result with it.
        """
        later = date(2026, 9, 15)
        state = state_with_drawdown(projected, DETERMINISTIC_REQUEST, today=later)
        assert state.drawdown is not None
        re_anchored = replanned_state(
            projected.assumptions, projected.household, (), today=later, modified=True
        )
        assert state.result == re_anchored.result
        assert state.result != projected.result
        assert state.monte_carlo is None


class TestCoupleSelection:
    """Whose retirement age a probe moves (planning §4.11)."""

    def test_person_text_selects_the_partner(self, couple_projected: PlanState) -> None:
        """Selecting "1" tests the partner's age; the saver's holds fixed."""
        request = replace(DETERMINISTIC_REQUEST, person_text="1")
        state = state_with_drawdown(couple_projected, request, today=TODAY)
        answer = state.drawdown
        assert answer is not None
        assert answer.person_position == 1
        assert answer.age == 63
        panel = build_drawdown_panel(state, RunMode.DETERMINISTIC)
        assert "Your partner retiring at age 63" in panel.detail
        assert panel.person_value == "1"

    def test_an_off_household_person_selection_is_rejected(
        self, couple_projected: PlanState
    ) -> None:
        """A selection naming nobody on the plan folds into the error."""
        request = replace(DETERMINISTIC_REQUEST, person_text="2")
        state = state_with_drawdown(couple_projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_PERSON_MESSAGE

    def test_a_single_person_plan_rejects_a_partner_selection(
        self, projected: PlanState
    ) -> None:
        """Selecting "1" names nobody when the plan holds one person."""
        request = replace(DETERMINISTIC_REQUEST, person_text="1")
        state = state_with_drawdown(projected, request, today=TODAY)
        assert state.drawdown is None
        assert state.drawdown_error == DRAWDOWN_PERSON_MESSAGE

    def test_the_age_bracket_anchors_on_the_selected_person(
        self, couple_projected: PlanState
    ) -> None:
        """Age 58 is below the saver's bracket but inside the partner's."""
        for_saver = replace(DETERMINISTIC_REQUEST, age_text="58")
        for_partner = replace(DETERMINISTIC_REQUEST, age_text="58", person_text="1")
        saver_state = state_with_drawdown(couple_projected, for_saver, today=TODAY)
        partner_state = state_with_drawdown(couple_projected, for_partner, today=TODAY)
        assert saver_state.drawdown is None
        assert saver_state.drawdown_error == DRAWDOWN_AGE_MESSAGE
        assert partner_state.drawdown is not None
        assert partner_state.drawdown.person_position == 1

    def test_the_person_selector_shows_only_for_a_couple(
        self, projected: PlanState, couple_projected: PlanState
    ) -> None:
        """One person hides the selector; a couple names them both."""
        single = build_drawdown_panel(projected, RunMode.DETERMINISTIC)
        pair = build_drawdown_panel(couple_projected, RunMode.DETERMINISTIC)
        assert single.person_visible is False
        assert single.person_options == ("You",)
        assert pair.person_visible is True
        assert pair.person_options == ("You", "Your partner")
        assert pair.person_label == DRAWDOWN_PERSON_LABEL
        assert pair.person_value == "0"


class TestDrawdownPanel:
    """The card view model over the session state."""

    def test_no_answer_yet_shows_the_no_run_message(self, projected: PlanState) -> None:
        """The card before any search explains what to do."""
        panel = build_drawdown_panel(projected, RunMode.DETERMINISTIC)
        assert panel.heading == DRAWDOWN_HEADING
        assert panel.answer == ""
        assert panel.detail == ""
        assert panel.message == NO_DRAWDOWN_MESSAGE
        assert panel.age_value == "63"
        assert panel.success_value == DEFAULT_DRAWDOWN_SUCCESS_VALUE

    def test_without_a_plan_the_age_is_blank(self) -> None:
        """No plan means no stated retirement-age decision to echo."""
        panel = build_drawdown_panel(initial_plan_state(), RunMode.DETERMINISTIC)
        assert panel.age_value == ""

    def test_the_success_target_shows_only_under_monte_carlo(
        self, projected: PlanState
    ) -> None:
        """The deterministic basis has no success target to set."""
        deterministic = build_drawdown_panel(projected, RunMode.DETERMINISTIC)
        monte_carlo = build_drawdown_panel(projected, RunMode.MONTE_CARLO)
        assert not deterministic.success_visible
        assert monte_carlo.success_visible

    def test_an_answer_reads_out_with_age_and_basis(self, solved: PlanState) -> None:
        """Acceptance criterion: the answer, its assumed age, basis."""
        panel = build_drawdown_panel(solved, RunMode.DETERMINISTIC)
        assert solved.drawdown is not None
        assert panel.answer.startswith(DRAWDOWN_ANSWER_PREFIX)
        assert "age 63" in panel.detail
        assert "£1,000,000.00" in panel.detail
        assert DRAWDOWN_DETERMINISTIC_BASIS in panel.detail
        assert panel.message == ""

    def test_a_monte_carlo_answer_names_its_basis(self, projected: PlanState) -> None:
        """The basis line carries the success target, paths, and seed."""
        state = state_with_drawdown(projected, MONTE_CARLO_REQUEST, today=TODAY)
        panel = build_drawdown_panel(state, RunMode.MONTE_CARLO)
        assert "90.0%" in panel.detail
        assert "2 paths" in panel.detail
        assert "seed 7" in panel.detail
        assert panel.age_value == "63"
        assert panel.success_value == "90"

    def test_no_sustainable_income_reads_out_as_such(self, solved: PlanState) -> None:
        """Acceptance criterion: the card reports when nothing sustains."""
        assert solved.drawdown is not None
        unmet = replace(solved.drawdown, income=None)
        state = replace(solved, drawdown=unmet)
        panel = build_drawdown_panel(state, RunMode.DETERMINISTIC)
        assert panel.answer == (
            "No income is sustainable retiring at 63 —"
            " the plan's outflows already exhaust it."
        )
        assert panel.message == ""

    def test_a_failure_message_reaches_the_panel(self, projected: PlanState) -> None:
        """The stored error is the card's message."""
        request = replace(DETERMINISTIC_REQUEST, age_text="nope")
        failed = state_with_drawdown(projected, request, today=TODAY)
        panel = build_drawdown_panel(failed, RunMode.DETERMINISTIC)
        assert panel.message == DRAWDOWN_AGE_MESSAGE
        assert panel.answer == ""

    def test_a_held_answer_survives_a_mode_switch(self, solved: PlanState) -> None:
        """The basis is named in the copy, so the answer keeps showing."""
        panel = build_drawdown_panel(solved, RunMode.MONTE_CARLO)
        assert panel.answer != ""
        assert DRAWDOWN_DETERMINISTIC_BASIS in panel.detail

    def test_the_charts_screen_carries_the_card(self, solved: PlanState) -> None:
        """The card rides the charts view model (planning §4.7)."""
        view_model = build_charts_view_model(solved)
        assert view_model.drawdown.heading == DRAWDOWN_HEADING
        assert view_model.drawdown.answer != ""


class TestDrawdownAnswerEcho:
    """The card echoes a held answer's inputs."""

    def test_the_age_echoes_the_held_answer(self, solved: PlanState) -> None:
        """A held age-61 answer echoes 61, not the stated decision."""
        assert solved.drawdown is not None
        moved = replace(solved.drawdown, age=61)
        state = replace(solved, drawdown=moved)
        panel = build_drawdown_panel(state, RunMode.DETERMINISTIC)
        assert panel.age_value == "61"

    def test_a_deterministic_answer_echoes_the_default_success(
        self, solved: PlanState
    ) -> None:
        """No success target on the answer leaves the default in place."""
        panel = build_drawdown_panel(solved, RunMode.MONTE_CARLO)
        assert panel.success_value == DEFAULT_DRAWDOWN_SUCCESS_VALUE


def test_the_answer_dataclass_defaults_are_deterministic() -> None:
    """The Monte Carlo fields default to absent."""
    answer = DrawdownAnswer(
        income=Money(Decimal(20000)),
        age=63,
        maximum=DRAWDOWN_SEARCH_MAXIMUM,
        mode=RunMode.DETERMINISTIC,
    )
    assert answer.seed is None
    assert answer.paths is None
    assert answer.target_success_rate is None
