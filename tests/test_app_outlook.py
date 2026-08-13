"""The retirement outlook card: the headline forecast panel (9.27).

The acceptance criterion: a held Monte Carlo run reads back as plain
sentences — the likely pot range at retirement in today's money with
the 1-in-20 tails stated, the pension slice, the level annuity income
it would buy, and the State Pension forecast stacked on top — while
the no-plan and no-run states explain what to do instead. The tests
keep runs fast by shrinking the horizon, exactly as the Monte Carlo
suite does.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from factories import FreeWrapperRules, money_fact, stub_region
from glidepath.app import (
    DETERMINISTIC_BASIS_SENTENCE,
    NO_OUTLOOK_MESSAGE,
    OUTLOOK_HEADING,
    OUTLOOK_NO_PLAN_MESSAGE,
    PlanState,
    build_charts_view_model,
    build_outlook_panel,
    initial_plan_state,
    state_with_household,
    state_with_monte_carlo,
    state_with_override,
)
from glidepath.app.outlook import (
    _CardContext,
    _retirement_reading,
    _snapshot_person,
    _state_pension_quote,
    _tax_free_cash,
)
from glidepath.core import (
    ContributionTaxTreatment,
    Decision,
    EntityId,
    Fact,
    GrowthTaxTreatment,
    Household,
    Money,
    Period,
    Person,
    Rate,
    SpendingPlan,
    StatePensionRecord,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, SIPP_KIND

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def state_pension_record(
    forecast: str | None = "230.25", *, forecast_as_of: date = AS_OF
) -> StatePensionRecord:
    """A record carrying the official forecast, or a legacy one without."""
    amount = None if forecast is None else money_fact(forecast, as_of=forecast_as_of)
    return StatePensionRecord(
        forecast_weekly_amount=amount,
        protected_payment=None,
        deferral_years=Decision(value=Decimal(0), recorded_on=RECORDED),
    )


def household(
    *,
    kinds: tuple[WrapperKindId, ...] = (SIPP_KIND, ISA_KIND),
    retirement_age: int = 63,
    state_pension: StatePensionRecord | None = None,
    dob: date = date(1966, 2, 1),
    crystallised: bool = False,
) -> Household:
    """A saver with one wrapper per requested kind, £50,000 in each.

    ``crystallised`` moves each wrapper's £50,000 into the
    crystallised sub-balance — pension kinds only.
    """
    wrappers = tuple(
        Wrapper(
            id=EntityId(f"outlook-{index}"),
            kind=kind,
            balance=money_fact("0" if crystallised else "50000", as_of=AS_OF),
            crystallised_balance=(
                money_fact("50000", as_of=AS_OF) if crystallised else None
            ),
        )
        for index, kind in enumerate(kinds)
    )
    person = Person(
        id=EntityId("outlook-person"),
        date_of_birth=Fact(value=dob, as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=retirement_age, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=wrappers,
        state_pension=state_pension,
    )
    return Household(
        persons=(person,),
        spending=SpendingPlan(annual_spending_real=money_fact("12000", as_of=AS_OF)),
    )


def partner(
    *, state_pension: StatePensionRecord | None = None, kind: WrapperKindId = SIPP_KIND
) -> Person:
    """A partner born 1969-02-01 retiring at 60 with £50,000 in ``kind``."""
    sipp = Wrapper(
        id=EntityId("outlook-partner-0"),
        kind=kind,
        balance=money_fact("50000", as_of=AS_OF),
    )
    return Person(
        id=EntityId("outlook-partner"),
        date_of_birth=Fact(value=date(1969, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(sipp,),
        state_pension=state_pension,
    )


def couple(
    *, with_forecasts: bool = False, partner_kind: WrapperKindId = SIPP_KIND
) -> Household:
    """The saver joined by the partner — both retire on 2029-02-01.

    The saver attains their target 63 and the partner their target 60
    on the same date, so the reading names ages 63 and 60.
    """
    record = state_pension_record() if with_forecasts else None
    base = household(state_pension=record)
    return Household(
        persons=(*base.persons, partner(state_pension=record, kind=partner_kind)),
        spending=base.spending,
    )


def short_horizon_state(planning_age: str = "65") -> PlanState:
    """A fresh session with the planning age overridden down."""
    outcome = state_with_override(
        initial_plan_state(),
        "horizon.planning_age",
        planning_age,
        recorded_on=RECORDED,
        today=TODAY,
    )
    assert outcome.error is None
    return outcome.state


def outlook_state(plan: Household | None = None, planning_age: str = "65") -> PlanState:
    """A projected session with a held 3-path Monte Carlo run."""
    projected = state_with_household(
        short_horizon_state(planning_age),
        plan if plan is not None else household(),
        today=TODAY,
    )
    return state_with_monte_carlo(projected, "7", "3", today=TODAY)


class TestEmptyStates:
    """The card explains what to do before it has anything to show."""

    def test_without_a_plan_the_card_says_save_facts_first(self) -> None:
        """No household: the message names the Facts tab."""
        panel = build_outlook_panel(initial_plan_state())
        assert panel.heading == OUTLOOK_HEADING
        assert panel.answer == ""
        assert panel.detail == ""
        assert panel.message == OUTLOOK_NO_PLAN_MESSAGE

    def test_without_a_run_the_card_shows_the_single_path_outlook(self) -> None:
        """A projected plan alone reads the deterministic path (10.4)."""
        state = state_with_household(short_horizon_state(), household(), today=TODAY)
        panel = build_outlook_panel(state)
        assert panel.answer.startswith("At age 63, your pots are on course")
        assert "today's money" in panel.answer
        assert DETERMINISTIC_BASIS_SENTENCE in panel.detail
        assert panel.message == ""

    def test_the_charts_view_model_carries_the_panel(self) -> None:
        """The outlook rides the charts screen in either run mode."""
        state = outlook_state()
        deterministic = build_charts_view_model(state)
        assert deterministic.outlook.answer.startswith("At age 63,")
        assert deterministic.outlook.message == ""


class TestHeldRun:
    """A held Monte Carlo run reads back as the headline sentences."""

    def test_the_answer_names_the_age_and_the_likely_range(self) -> None:
        """Acceptance criterion: the headline pot sentence."""
        panel = build_outlook_panel(outlook_state())
        assert panel.answer.startswith("At age 63, your pots could be worth around £")
        assert "likely between £" in panel.answer
        assert "today's money" in panel.answer
        assert panel.message == ""

    def test_the_tails_are_stated_not_hidden(self) -> None:
        """The 1-in-20 chances appear with both bounds."""
        panel = build_outlook_panel(outlook_state())
        assert "roughly a 1-in-20 chance of less than £" in panel.detail

    def test_the_pension_slice_and_annuity_income_appear(self) -> None:
        """A mixed household names the pension slice and its income.

        An uncrystallised pot delivers its tax-free cash first, so
        the quote names both the income and the up-front cash — the
        engine's own whole-pot purchase convention (roadmap 5.5).
        """
        panel = build_outlook_panel(outlook_state())
        assert "Pensions alone" in panel.detail
        assert "level single-life annuity bought at 63" in panel.detail
        assert "a year before tax" in panel.detail
        assert "of up-front tax-free cash" in panel.detail

    def test_the_state_pension_stacks_onto_the_annuity(self) -> None:
        """The forecast, its start age, and the combined income appear.

        £230.25 a week is £11,973 a year, starting at this cohort's
        state pension age of 67.
        """
        plan = household(state_pension=state_pension_record())
        panel = build_outlook_panel(outlook_state(plan))
        assert "State Pension forecast adds £11,973 a year from age 67" in panel.detail
        assert "a year all told" in panel.detail

    def test_the_basis_names_the_run_and_the_percentiles(self) -> None:
        """The §4.6 manifest side: paths, seed, and what "likely" means."""
        panel = build_outlook_panel(outlook_state())
        assert "3 paths (seed 7)" in panel.detail
        assert "25th to 75th percentiles" in panel.detail

    def test_the_engine_marks_pension_wrappers_on_the_snapshots(self) -> None:
        """The SIPP carries the pension marker; the ISA does not."""
        state = outlook_state()
        assert state.result is not None
        by_kind = {
            wrapper.kind: wrapper.pension
            for wrapper in state.result.snapshots[0].persons[0].wrappers
        }
        assert by_kind[SIPP_KIND] is True
        assert by_kind[ISA_KIND] is False


class TestCoupleCopy:
    """A two-person household speaks at household level (9.31)."""

    def test_the_headline_names_both_ages(self) -> None:
        """The anchor carries each person's age at the reading date."""
        panel = build_outlook_panel(outlook_state(couple()))
        assert panel.answer.startswith(
            "At ages 63 and 60, your pots could be worth around £"
        )
        assert panel.message == ""

    def test_the_annuity_buys_each_slice_at_its_own_age(self) -> None:
        """Both pension slices purchase at their owners' ages."""
        panel = build_outlook_panel(outlook_state(couple()))
        assert "As level single-life annuities bought at ages 63 and 60," in (
            panel.detail
        )
        assert "a year before tax" in panel.detail

    def test_each_forecast_stacks_with_one_all_told(self) -> None:
        """One sentence per forecast; the combined figure lands last.

        Both forecasts quote £230.25 a week (£11,973 a year) from this
        cohort's state pension age of 67; only the partner's closing
        sentence combines the annuity income with both forecasts.
        """
        panel = build_outlook_panel(outlook_state(couple(with_forecasts=True)))
        lines = panel.detail.split("\n")
        forecasts = [line for line in lines if "State Pension forecast adds" in line]
        assert len(forecasts) == 2
        assert forecasts[0].startswith(
            "Your State Pension forecast adds £11,973 a year from age 67"
        )
        assert forecasts[1].startswith(
            "Your partner's State Pension forecast adds £11,973 a year from age 67"
        )
        assert "all told" not in forecasts[0]
        assert "all told" in forecasts[1]


class TestSentenceGating:
    """Each sentence appears only when it has something true to say."""

    def test_a_pension_only_household_drops_the_slice_sentence(self) -> None:
        """With nothing beside pensions the slice would repeat the headline."""
        panel = build_outlook_panel(outlook_state(household(kinds=(SIPP_KIND,))))
        assert "Pensions alone" not in panel.detail
        assert "level single-life annuity" in panel.detail

    def test_an_isa_only_household_drops_the_income_sentences(self) -> None:
        """No pension money means no annuity to quote."""
        plan = household(kinds=(ISA_KIND,), state_pension=state_pension_record())
        panel = build_outlook_panel(outlook_state(plan))
        assert "Pensions alone" not in panel.detail
        assert "annuity" not in panel.detail
        assert "State Pension forecast adds £11,973 a year from age 67." in panel.detail
        assert "all told" not in panel.detail

    def test_without_a_forecast_the_state_pension_stays_silent(self) -> None:
        """A legacy record with no official forecast quotes nothing."""
        plan = household(state_pension=state_pension_record(forecast=None))
        panel = build_outlook_panel(outlook_state(plan))
        assert "State Pension" not in panel.detail

    def test_an_age_past_the_rate_table_drops_the_annuity(self) -> None:
        """The shipped rates cover 55-75; the model never extrapolates."""
        plan = household(retirement_age=80, state_pension=state_pension_record())
        panel = build_outlook_panel(outlook_state(plan, planning_age="85"))
        assert panel.answer.startswith("At age 80,")
        assert "level single-life annuity" not in panel.detail
        assert "a year before tax" not in panel.detail
        assert "State Pension forecast adds £11,973 a year from age 67." in panel.detail

    def test_a_person_already_retired_reads_this_tax_years_close(self) -> None:
        """A target age already attained anchors at the first close."""
        panel = build_outlook_panel(outlook_state(household(retirement_age=60)))
        assert panel.answer.startswith("By the end of this tax year,")
        assert panel.message == ""

    def test_a_birthday_since_april_counts_as_already_retired(self) -> None:
        """The boundary is the run's today, not the tax-year start.

        A target-age birthday between 6 April and the run date is in
        the past even though it falls inside the first projected
        period — the copy must not promise "At age X" for an age
        already attained.
        """
        plan = household(retirement_age=60, dob=date(1966, 5, 1))
        panel = build_outlook_panel(outlook_state(plan))
        assert panel.answer.startswith("By the end of this tax year,")

    def test_a_crystallised_pot_quotes_no_tax_free_cash(self) -> None:
        """Already-crystallised funds annuitise whole (roadmap 5.5)."""
        plan = household(kinds=(SIPP_KIND,), crystallised=True)
        panel = build_outlook_panel(outlook_state(plan))
        assert "level single-life annuity bought at 63" in panel.detail
        assert "a year before tax." in panel.detail
        assert "tax-free cash" not in panel.detail

    def test_a_stale_forecast_quotes_the_rolled_amount(self) -> None:
        """The card uprates exactly as the run did (§4.8).

        A forecast stated a year before the run is rolled forward by
        the engine under the uprating assumption and disclosed; the
        card must quote that rolled amount, not the stale £11,973.
        """
        record = state_pension_record(forecast_as_of=date(2025, 8, 1))
        plan = household(state_pension=record)
        state = outlook_state(plan)
        assert state.result is not None
        assert any(
            roll.label.endswith("state_pension.forecast_weekly_amount")
            for roll in state.result.provenance.balance_roll_forwards
        )
        panel = build_outlook_panel(state)
        assert "State Pension forecast adds £" in panel.detail
        assert "adds £11,973" not in panel.detail

    def test_a_transition_cohort_start_age_names_the_months(self) -> None:
        """SPAs between whole years read as years and months.

        A December 1960 birth reaches state pension age at 66 years
        and 9 months (the shipped timetable); truncating to "age 66"
        would imply up to nine months early.
        """
        plan = household(
            retirement_age=63,
            dob=date(1960, 12, 15),
            state_pension=state_pension_record(),
        )
        panel = build_outlook_panel(outlook_state(plan, planning_age="75"))
        assert "from age 66 and 9 months" in panel.detail


class TestStaleness:
    """A held run that no longer matches the projection reads as no run."""

    def test_a_retirement_age_past_the_horizon_reads_as_no_run(self) -> None:
        """A plan edit the result predates must never mis-anchor the card."""
        state = outlook_state()
        drifted = replace(state, household=household(retirement_age=90))
        panel = build_outlook_panel(drifted)
        assert panel.message == NO_OUTLOOK_MESSAGE

    def test_paths_over_differing_periods_fall_back_to_the_single_path(self) -> None:
        """Unaligned paths cannot be summarised; the deterministic card shows."""
        state = outlook_state()
        assert state.monte_carlo is not None
        result = state.monte_carlo
        truncated = replace(
            result.outcomes[0],
            closing_balances=result.outcomes[0].closing_balances[:1],
            ending_balance=result.outcomes[0].closing_balances[0],
        )
        mismatched = replace(result, outcomes=(truncated, *result.outcomes[1:]))
        panel = build_outlook_panel(replace(state, monte_carlo=mismatched))
        assert panel.message == ""
        assert DETERMINISTIC_BASIS_SENTENCE in panel.detail

    def test_a_run_over_fewer_periods_falls_back_to_the_single_path(self) -> None:
        """A uniformly shorter household series misaligns with the charts."""
        state = outlook_state()
        assert state.monte_carlo is not None
        result = state.monte_carlo
        shortened = tuple(
            replace(
                outcome,
                closing_balances=outcome.closing_balances[:1],
                ending_balance=outcome.closing_balances[0],
            )
            for outcome in result.outcomes
        )
        panel = build_outlook_panel(
            replace(state, monte_carlo=replace(result, outcomes=shortened))
        )
        assert panel.message == ""
        assert DETERMINISTIC_BASIS_SENTENCE in panel.detail

    def test_a_shorter_pension_series_alone_falls_back_to_the_single_path(
        self,
    ) -> None:
        """The pension series carries the same alignment rule."""
        state = outlook_state()
        assert state.monte_carlo is not None
        result = state.monte_carlo
        shortened = tuple(
            replace(
                outcome,
                pension_closing_balances=outcome.pension_closing_balances[:1],
            )
            for outcome in result.outcomes
        )
        panel = build_outlook_panel(
            replace(state, monte_carlo=replace(result, outcomes=shortened))
        )
        assert panel.message == ""
        assert DETERMINISTIC_BASIS_SENTENCE in panel.detail


@dataclass(frozen=True)
class UncappedPartialWrapperRules(FreeWrapperRules):
    """A pension-style fraction with no lump-sum allowance anywhere."""

    def tax_treatment(self, kind: WrapperKindId, period: Period) -> WrapperTaxTreatment:
        """A quarter tax-free, the rest taxable income."""
        del kind, period
        return WrapperTaxTreatment(
            contributions=ContributionTaxTreatment.FROM_TAXED_INCOME,
            growth=GrowthTaxTreatment.TAX_FREE,
            withdrawals=WithdrawalTaxTreatment.PARTIALLY_TAX_FREE,
            tax_free_fraction=Rate(Decimal("0.25")),
        )


def card_context() -> _CardContext:
    """The default household's card context, built as the panel builds it."""
    state = state_with_household(short_horizon_state(), household(), today=TODAY)
    result = state.result
    assert result is not None
    saved = state.household
    assert saved is not None
    reading = _retirement_reading(result, saved.persons)
    assert reading is not None
    return _CardContext(
        assumptions=state.assumptions,
        provenance=result.provenance,
        persons=saved.persons,
        snapshot=result.snapshots[reading.index],
        reading=reading,
    )


class TestIncomeGuards:
    """The income sentences' defensive guards, exercised directly (#199)."""

    def test_a_person_missing_from_the_snapshot_is_an_error(self) -> None:
        """The reading snapshot must carry every household person."""
        context = card_context()
        missing = EntityId("outlook-missing")
        with pytest.raises(ValueError, match="missing from the reading snapshot"):
            _snapshot_person(context, missing)

    def test_a_kind_without_a_fraction_pays_no_tax_free_cash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A region whose pension kind states no fraction quotes no cash."""
        context = card_context()
        monkeypatch.setattr(
            "glidepath.app.outlook.region_for", lambda _assumptions: stub_region()
        )
        cash = _tax_free_cash(context, 0, Money(Decimal(10000)))
        assert cash == Money(Decimal(0))

    def test_an_uncapped_region_pays_the_whole_fraction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a lump-sum allowance the tax-free cash goes uncapped."""
        context = card_context()
        region = replace(stub_region(), wrappers=UncappedPartialWrapperRules())
        monkeypatch.setattr(
            "glidepath.app.outlook.region_for", lambda _assumptions: region
        )
        cash = _tax_free_cash(context, 0, Money(Decimal(10000)))
        assert cash == Money(Decimal(2500))

    def test_a_dob_outside_the_timetable_quotes_nothing(self) -> None:
        """A birth date the SPA timetable predates yields no sentence."""
        state = state_with_household(short_horizon_state(), household(), today=TODAY)
        result = state.result
        assert result is not None
        isa = Wrapper(
            id=EntityId("outlook-ancient-0"),
            kind=ISA_KIND,
            balance=money_fact("1000", as_of=AS_OF),
        )
        ancient = Person(
            id=EntityId("outlook-ancient"),
            date_of_birth=Fact(
                value=date(1900, 1, 1), as_of=AS_OF, recorded_on=RECORDED
            ),
            target_retirement_age=Decision(value=65, recorded_on=RECORDED),
            tax_residency=RUK_RESIDENCY,
            wrappers=(isa,),
            state_pension=state_pension_record(),
        )
        quote = _state_pension_quote(ancient, result.provenance)
        assert quote is None
