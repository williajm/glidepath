"""The "When can I retire?" card: transition and panel (issue 9.14).

The acceptance criterion: given a plan with employment income, the
shell reports the earliest retirement age meeting the chosen
replacement rate (or that none in range does), consistent with
re-running the plan at that age; the solver is deterministic. The
tests keep runs fast by shrinking the horizon — a 60-year-old saver
with the planning age overridden down to 65, so the search covers
ages 60 to 64.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    MONTE_CARLO_PATHS_MESSAGE,
    MONTE_CARLO_SEED_MESSAGE,
    NO_RETIREMENT_MESSAGE,
    RETIREMENT_NO_INCOME_MESSAGE,
    RETIREMENT_NO_PLAN_MESSAGE,
    RETIREMENT_RATE_MESSAGE,
    RETIREMENT_SUCCESS_MESSAGE,
    PlanState,
    RetirementAnswer,
    RetirementRequest,
    build_charts_view_model,
    build_retirement_panel,
    initial_plan_state,
    state_with_household,
    state_with_override,
    state_with_retirement,
)
from glidepath.app.plan import region_for, replanned_state
from glidepath.app.retirement import (
    DEFAULT_RETIREMENT_RATE_VALUE,
    DEFAULT_RETIREMENT_SUCCESS_VALUE,
    RETIREMENT_ANSWER_PREFIX,
    RETIREMENT_BUDGET_MESSAGE,
    RETIREMENT_DETERMINISTIC_BASIS,
    RETIREMENT_FAILED_PREFIX,
    RETIREMENT_HEADING,
    RETIREMENT_HORIZON_MESSAGE,
)
from glidepath.core import (
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    RetirementAgeSearch,
    RunConfig,
    RunMode,
    SpendingPlan,
    Wrapper,
    earliest_retirement_age,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
DETERMINISTIC_REQUEST = RetirementRequest(mode=RunMode.DETERMINISTIC, rate_text="66")
MONTE_CARLO_REQUEST = RetirementRequest(
    mode=RunMode.MONTE_CARLO,
    rate_text="66",
    seed_text="7",
    paths_text="2",
    success_text="90",
)


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household(
    employment: str | None = "50000", date_of_birth: date = date(1966, 2, 1)
) -> Household:
    """A 60-year-old ISA saver, so the horizon stays a few periods."""
    wrapper = Wrapper(
        id=EntityId("retire-isa"),
        kind=ISA_KIND,
        balance=money_fact("300000"),
    )
    employment_income = None
    if employment is not None:
        employment_income = money_fact(employment)
    person = Person(
        id=EntityId("retire-person"),
        date_of_birth=Fact(value=date_of_birth, as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=63, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=employment_income,
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


@pytest.fixture(scope="module", name="solved")
def solved_fixture(projected: PlanState) -> PlanState:
    """The projected session after a deterministic 66% search."""
    return state_with_retirement(projected, DETERMINISTIC_REQUEST, today=TODAY)


class TestStateWithRetirement:
    """The explicit search transition and its failure modes."""

    def test_holds_the_answer_with_its_inputs(self, solved: PlanState) -> None:
        """Acceptance criterion: the shell reports the earliest age."""
        answer = solved.retirement
        assert answer is not None
        assert solved.retirement_error is None
        assert answer.age is not None
        assert answer.replacement_rate == Decimal("0.66")
        assert answer.employment_income == Money(Decimal(50000))
        assert answer.target_income == Money(Decimal(33000))
        assert answer.minimum_age == 60
        assert answer.maximum_age == 64
        assert answer.mode is RunMode.DETERMINISTIC
        assert answer.seed is None
        assert answer.paths is None
        assert answer.target_success_rate is None

    def test_agrees_with_the_core_solver(self, solved: PlanState) -> None:
        """Acceptance criterion: consistent with re-running at that age.

        The held age is exactly what the core solver answers for the
        same plan, assumptions, and derived search — and the core's
        own tests pin that answer to a re-run at that age.
        """
        assert solved.retirement is not None
        assert solved.household is not None
        search = RetirementAgeSearch(
            target_income=Money(Decimal(33000)), minimum_age=60, maximum_age=64
        )
        expected = earliest_retirement_age(
            solved.household,
            solved.assumptions,
            region_for(solved.assumptions),
            RunConfig(today=TODAY),
            search,
        )
        assert solved.retirement.age == expected

    def test_the_transition_is_deterministic(
        self, projected: PlanState, solved: PlanState
    ) -> None:
        """Acceptance criterion: the same inputs answer the same age."""
        rerun = state_with_retirement(projected, DETERMINISTIC_REQUEST, today=TODAY)
        assert rerun.retirement is not None
        assert solved.retirement is not None
        assert rerun.retirement == solved.retirement

    def test_without_a_plan_reports_no_plan(self) -> None:
        """The search needs a captured household first."""
        state = state_with_retirement(
            initial_plan_state(), DETERMINISTIC_REQUEST, today=TODAY
        )
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_NO_PLAN_MESSAGE

    def test_without_employment_income_reports_what_to_add(self) -> None:
        """A replacement rate of nothing targets nothing."""
        no_income = state_with_household(
            short_horizon_state(), household(employment=None), today=TODAY
        )
        state = state_with_retirement(no_income, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_NO_INCOME_MESSAGE

    def test_zero_employment_income_reports_the_same(self) -> None:
        """A stated zero income cannot anchor a target either."""
        zero_income = state_with_household(
            short_horizon_state(), household(employment="0"), today=TODAY
        )
        state = state_with_retirement(zero_income, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_NO_INCOME_MESSAGE

    def test_a_target_that_quantizes_to_zero_is_rejected(self) -> None:
        """A tiny income times a small rate rounds to no target at all.

        The rejection must happen at the parse boundary: past it the
        core search would refuse the zero target with an exception the
        worker cannot deliver.
        """
        tiny = state_with_household(
            short_horizon_state(), household(employment="0.01"), today=TODAY
        )
        request = replace(DETERMINISTIC_REQUEST, rate_text="1")
        state = state_with_retirement(tiny, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_NO_INCOME_MESSAGE

    @pytest.mark.parametrize("rate_text", ["", "0", "101", "-5", "sixty", "66.5"])
    def test_an_unusable_rate_is_rejected(
        self, projected: PlanState, rate_text: str
    ) -> None:
        """Replacement rates are whole percentages from 1 to 100."""
        request = replace(DETERMINISTIC_REQUEST, rate_text=rate_text)
        state = state_with_retirement(projected, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_RATE_MESSAGE

    def test_a_person_past_the_horizon_leaves_no_ages(self) -> None:
        """A planning age already attained brackets nothing."""
        aged = state_with_household(
            short_horizon_state(),
            household(date_of_birth=date(1960, 2, 1)),
            today=TODAY,
        )
        state = state_with_retirement(aged, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_HORIZON_MESSAGE

    def test_a_monte_carlo_basis_carries_its_inputs(self, projected: PlanState) -> None:
        """The seed, path count, and success target land on the answer."""
        state = state_with_retirement(projected, MONTE_CARLO_REQUEST, today=TODAY)
        answer = state.retirement
        assert answer is not None
        assert answer.mode is RunMode.MONTE_CARLO
        assert answer.seed == 7
        assert answer.paths == 2
        assert answer.target_success_rate == Decimal("0.9")

    def test_an_unparseable_seed_is_rejected(self, projected: PlanState) -> None:
        """The Monte Carlo basis reuses the panel's seed rules."""
        request = replace(MONTE_CARLO_REQUEST, seed_text="lucky")
        state = state_with_retirement(projected, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == MONTE_CARLO_SEED_MESSAGE

    @pytest.mark.parametrize("paths_text", ["many", "0", "-3", "10001"])
    def test_an_unusable_path_count_is_rejected(
        self, projected: PlanState, paths_text: str
    ) -> None:
        """The Monte Carlo basis reuses the panel's path-count rules."""
        request = replace(MONTE_CARLO_REQUEST, paths_text=paths_text)
        state = state_with_retirement(projected, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == MONTE_CARLO_PATHS_MESSAGE

    def test_an_over_budget_monte_carlo_search_is_rejected(
        self, projected: PlanState
    ) -> None:
        """Candidate ages times paths is bounded, not paths alone.

        10,000 paths pass the per-run cap, but across the five
        candidate ages they would project 50,000 paths — over the
        search budget.
        """
        request = replace(MONTE_CARLO_REQUEST, paths_text="10000")
        state = state_with_retirement(projected, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_BUDGET_MESSAGE

    @pytest.mark.parametrize("success_text", ["", "0", "101", "ninety"])
    def test_an_unusable_success_target_is_rejected(
        self, projected: PlanState, success_text: str
    ) -> None:
        """Success targets are whole percentages from 1 to 100."""
        request = replace(MONTE_CARLO_REQUEST, success_text=success_text)
        state = state_with_retirement(projected, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_SUCCESS_MESSAGE

    def test_an_engine_rejection_folds_into_the_error(self) -> None:
        """A future-dated balance fails the search, never raises (§4.8)."""
        future = replace(
            household(),
            persons=(
                replace(
                    household().persons[0],
                    wrappers=(
                        Wrapper(
                            id=EntityId("retire-isa"),
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
        outcome = state_with_retirement(state, DETERMINISTIC_REQUEST, today=TODAY)
        assert outcome.retirement is None
        assert outcome.retirement_error is not None
        assert outcome.retirement_error.startswith(RETIREMENT_FAILED_PREFIX)

    def test_a_failure_drops_the_held_answer(self, solved: PlanState) -> None:
        """A rejected re-run never leaves a stale answer on screen."""
        request = replace(DETERMINISTIC_REQUEST, rate_text="nope")
        state = state_with_retirement(solved, request, today=TODAY)
        assert state.retirement is None
        assert state.retirement_error == RETIREMENT_RATE_MESSAGE

    def test_a_success_clears_a_previous_error(self, projected: PlanState) -> None:
        """The error and the answer never show together."""
        request = replace(DETERMINISTIC_REQUEST, rate_text="nope")
        failed = state_with_retirement(projected, request, today=TODAY)
        state = state_with_retirement(failed, DETERMINISTIC_REQUEST, today=TODAY)
        assert state.retirement is not None
        assert state.retirement_error is None

    def test_replanning_drops_the_held_answer(self, solved: PlanState) -> None:
        """Any plan change invalidates the held answer."""
        state = state_with_household(solved, household(), today=TODAY)
        assert state.retirement is None
        assert state.retirement_error is None

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
        state = state_with_retirement(projected, DETERMINISTIC_REQUEST, today=later)
        assert state.retirement is not None
        re_anchored = replanned_state(
            projected.assumptions, projected.household, (), today=later
        )
        assert state.result == re_anchored.result
        assert state.result != projected.result
        assert state.monte_carlo is None


class TestRetirementPanel:
    """The card view model over the session state."""

    def test_no_answer_yet_shows_the_no_run_message(self, projected: PlanState) -> None:
        """The card before any search explains what to do."""
        panel = build_retirement_panel(projected, RunMode.DETERMINISTIC)
        assert panel.heading == RETIREMENT_HEADING
        assert panel.answer == ""
        assert panel.detail == ""
        assert panel.message == NO_RETIREMENT_MESSAGE
        assert panel.rate_value == DEFAULT_RETIREMENT_RATE_VALUE
        assert panel.success_value == DEFAULT_RETIREMENT_SUCCESS_VALUE

    def test_the_success_target_shows_only_under_monte_carlo(
        self, projected: PlanState
    ) -> None:
        """The deterministic basis has no success target to set."""
        deterministic = build_retirement_panel(projected, RunMode.DETERMINISTIC)
        monte_carlo = build_retirement_panel(projected, RunMode.MONTE_CARLO)
        assert not deterministic.success_visible
        assert monte_carlo.success_visible

    def test_an_answer_reads_out_with_target_and_basis(self, solved: PlanState) -> None:
        """Acceptance criterion: the answer, its target income, basis."""
        panel = build_retirement_panel(solved, RunMode.DETERMINISTIC)
        assert solved.retirement is not None
        assert panel.answer == f"{RETIREMENT_ANSWER_PREFIX}{solved.retirement.age}"
        assert "£33,000.00" in panel.detail
        assert "66.0%" in panel.detail
        assert "£50,000.00" in panel.detail
        assert "ages 60 to 64" in panel.detail
        assert RETIREMENT_DETERMINISTIC_BASIS in panel.detail
        assert panel.message == ""

    def test_a_monte_carlo_answer_names_its_basis(self, projected: PlanState) -> None:
        """The basis line carries the success target, paths, and seed."""
        state = state_with_retirement(projected, MONTE_CARLO_REQUEST, today=TODAY)
        panel = build_retirement_panel(state, RunMode.MONTE_CARLO)
        assert "90.0%" in panel.detail
        assert "2 paths" in panel.detail
        assert "seed 7" in panel.detail
        assert panel.rate_value == "66"
        assert panel.success_value == "90"

    def test_no_age_in_range_reads_out_as_such(self, solved: PlanState) -> None:
        """Acceptance criterion: the card reports when no age meets."""
        assert solved.retirement is not None
        unmet = replace(solved.retirement, age=None)
        state = replace(solved, retirement=unmet)
        panel = build_retirement_panel(state, RunMode.DETERMINISTIC)
        assert panel.answer == "No age from 60 to 64 meets the target."
        assert panel.message == ""

    def test_a_failure_message_reaches_the_panel(self, projected: PlanState) -> None:
        """The stored error is the card's message."""
        request = replace(DETERMINISTIC_REQUEST, rate_text="nope")
        failed = state_with_retirement(projected, request, today=TODAY)
        panel = build_retirement_panel(failed, RunMode.DETERMINISTIC)
        assert panel.message == RETIREMENT_RATE_MESSAGE
        assert panel.answer == ""

    def test_a_held_answer_survives_a_mode_switch(self, solved: PlanState) -> None:
        """The basis is named in the copy, so the answer keeps showing."""
        panel = build_retirement_panel(solved, RunMode.MONTE_CARLO)
        assert panel.answer != ""
        assert RETIREMENT_DETERMINISTIC_BASIS in panel.detail

    def test_the_charts_screen_carries_the_card(self, solved: PlanState) -> None:
        """The card rides the charts view model (planning §4.7)."""
        view_model = build_charts_view_model(solved)
        assert view_model.retirement.heading == RETIREMENT_HEADING
        assert view_model.retirement.answer != ""


class TestRetirementAnswerEcho:
    """The card echoes a held answer's inputs."""

    def test_the_rate_echoes_the_held_answer(self, solved: PlanState) -> None:
        """A held 66% answer echoes 66, not the default."""
        assert solved.retirement is not None
        recoloured = replace(solved.retirement, replacement_rate=Decimal("0.5"))
        state = replace(solved, retirement=recoloured)
        panel = build_retirement_panel(state, RunMode.DETERMINISTIC)
        assert panel.rate_value == "50"

    def test_a_deterministic_answer_echoes_the_default_success(
        self, solved: PlanState
    ) -> None:
        """No success target on the answer leaves the default in place."""
        panel = build_retirement_panel(solved, RunMode.MONTE_CARLO)
        assert panel.success_value == DEFAULT_RETIREMENT_SUCCESS_VALUE


def test_the_answer_dataclass_defaults_are_deterministic() -> None:
    """The Monte Carlo fields default to absent."""
    answer = RetirementAnswer(
        age=62,
        replacement_rate=Decimal("0.66"),
        employment_income=Money(Decimal(50000)),
        target_income=Money(Decimal(33000)),
        minimum_age=60,
        maximum_age=64,
        mode=RunMode.DETERMINISTIC,
    )
    assert answer.seed is None
    assert answer.paths is None
    assert answer.target_success_rate is None
