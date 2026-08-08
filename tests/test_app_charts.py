"""Projection chart view-model tests (issue 8.4; planning §5.2, §4.7).

The acceptance criterion: real terms (today's money) is the default
presentation with a nominal toggle. The three chart surfaces —
wrapper balances, income composition, tax — aggregate the core
report to household level, one category per projected period.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from factories import money_fact
from glidepath.app import (
    NO_PROJECTION_MESSAGE,
    RUN_FAILED_PREFIX,
    ChartBand,
    ChartFill,
    ChartSeries,
    ChartSpec,
    ChartsViewModel,
    PlanState,
    ReportBasis,
    bar_tooltip,
    basis_from_key,
    build_charts_view_model,
    chart_table,
    format_money,
    initial_plan_state,
    state_with_household,
    state_with_override,
)
from glidepath.app.charts import PERIOD_COLUMN_LABEL, _category_label
from glidepath.core import (
    AnnuityPurchase,
    AssetAllocation,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    SpendingPlan,
    Wrapper,
    WrapperKindId,
    build_report,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, SIPP_KIND

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def wrapper(entity_id: str, kind: WrapperKindId, balance: str) -> Wrapper:
    """A wrapper holding one stated balance."""
    return Wrapper(id=EntityId(entity_id), kind=kind, balance=money_fact(balance))


def _series_values(
    view_model: ChartsViewModel, chart_title: str, series_label: str
) -> tuple[Decimal, ...]:
    """The values of one named series in one named chart."""
    chart = next(entry for entry in view_model.charts if entry.title == chart_title)
    series = next(entry for entry in chart.series if entry.label == series_label)
    return series.values


def household(
    wrappers: tuple[Wrapper, ...],
    employment: str | None,
    annuity_purchases: tuple[AnnuityPurchase, ...] = (),
) -> Household:
    """A projectable household: one saver retiring at 60."""
    person = Person(
        id=EntityId("charts-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=None if employment is None else money_fact(employment),
        wrappers=wrappers,
        annuity_purchases=annuity_purchases,
    )
    return Household(
        persons=(person,),
        spending=SpendingPlan(annual_spending_real=money_fact("18000")),
    )


@pytest.fixture(scope="module", name="projected")
def projected_fixture() -> PlanState:
    """One projected session — ISA + SIPP saver with employment income."""
    plan = household(
        (
            wrapper("charts-isa", ISA_KIND, "25000"),
            wrapper("charts-sipp", SIPP_KIND, "40000"),
        ),
        employment="42000",
    )
    return state_with_household(initial_plan_state(), plan, today=TODAY)


@pytest.fixture(scope="module", name="view_model")
def view_model_fixture(projected: PlanState) -> ChartsViewModel:
    """The charts screen over the projected state, default basis."""
    return build_charts_view_model(projected)


class TestBasisKeys:
    """The toggle keys map to the core report bases."""

    def test_real_key_maps_to_real_basis(self) -> None:
        """The real option key selects the real basis."""
        assert basis_from_key("real") is ReportBasis.REAL

    def test_nominal_key_maps_to_nominal_basis(self) -> None:
        """The nominal option key selects the nominal basis."""
        assert basis_from_key("nominal") is ReportBasis.NOMINAL

    def test_unknown_key_is_rejected(self) -> None:
        """A key the app layer never issued is an error."""
        with pytest.raises(ValueError, match="unknown chart basis key"):
            basis_from_key("imaginary")


class TestEmptyStates:
    """Without a projection the screen carries only the message."""

    def test_no_projection_shows_the_no_run_message(self) -> None:
        """A fresh session has no charts, only the no-run copy."""
        view_model = build_charts_view_model(initial_plan_state())
        assert view_model.message == NO_PROJECTION_MESSAGE
        assert view_model.charts == ()
        assert view_model.categories == ()

    def test_run_failure_is_reported(self) -> None:
        """A failed run surfaces the engine's message."""
        state = PlanState(
            assumptions=initial_plan_state().assumptions,
            run_error="balance dated in the future",
        )
        view_model = build_charts_view_model(state)
        assert view_model.message == RUN_FAILED_PREFIX + "balance dated in the future"
        assert view_model.charts == ()

    def test_empty_state_keeps_the_basis_toggle(self) -> None:
        """The toggle renders (real selected) even with nothing to chart."""
        view_model = build_charts_view_model(initial_plan_state())
        assert view_model.selected_basis_key == "real"
        assert [option.key for option in view_model.basis_options] == [
            "real",
            "nominal",
        ]


class TestProjectedCharts:
    """A projected state charts balances, income, and tax per period."""

    def test_three_charts_in_order(self, view_model: ChartsViewModel) -> None:
        """Balances, income composition, and tax — the §5.2 surfaces."""
        titles = [chart.title for chart in view_model.charts]
        assert titles == ["Wrapper balances", "Income composition", "Tax"]
        assert view_model.message == ""

    def test_real_is_the_default_basis(self, view_model: ChartsViewModel) -> None:
        """Acceptance criterion: today's money unless toggled."""
        assert view_model.selected_basis_key == "real"
        for chart in view_model.charts:
            assert "today's money" in chart.y_axis_label

    def test_one_category_per_period_with_year_and_age(
        self, projected: PlanState, view_model: ChartsViewModel
    ) -> None:
        """Each x-axis label carries the year and the age at its start (9.11)."""
        assert projected.result is not None
        expected = [
            f"{row.period.start.year} · {row.age_at_period_start}"
            for row in build_report(projected.result).rows
        ]
        assert list(view_model.categories) == expected
        assert view_model.categories[0] == f"{TODAY.year} · 35"

    def test_categories_carry_the_age_in_both_bases(
        self, projected: PlanState, view_model: ChartsViewModel
    ) -> None:
        """The year-and-age labels are basis-independent (9.11)."""
        nominal = build_charts_view_model(projected, basis=ReportBasis.NOMINAL)
        assert nominal.categories == view_model.categories

    def test_a_two_person_period_labels_with_the_year_alone(
        self, projected: PlanState
    ) -> None:
        """No single age exists to show until couples activate (9.4)."""
        assert projected.result is not None
        row = build_report(projected.result).rows[0]
        label = _category_label(row.period, [row, row])
        assert label == str(row.period.start.year)

    def test_every_series_spans_every_category(
        self, view_model: ChartsViewModel
    ) -> None:
        """Series values align one-to-one with the categories."""
        expected_length = len(view_model.categories)
        for chart in view_model.charts:
            for series in chart.series:
                assert len(series.values) == expected_length

    def test_balances_chart_has_one_series_per_wrapper(
        self, view_model: ChartsViewModel
    ) -> None:
        """Each wrapper charts under its kind's display name."""
        labels = [series.label for series in view_model.charts[0].series]
        assert labels == ["ISA", "SIPP"]

    def test_balances_stack_matches_the_report(
        self, projected: PlanState, view_model: ChartsViewModel
    ) -> None:
        """The stacked total is the report's closing balance, per period."""
        assert projected.result is not None
        report = build_report(projected.result)
        first_period = report.rows[0].period
        first_rows = [row for row in report.rows if row.period == first_period]
        report_total = sum(
            (row.closing_balance.amount for row in first_rows), Decimal(0)
        )
        chart_total = sum(
            (series.values[0] for series in view_model.charts[0].series), Decimal(0)
        )
        assert chart_total == report_total

    def test_income_chart_drops_sources_never_drawn_on(
        self, view_model: ChartsViewModel
    ) -> None:
        """No annuities or DB pensions in the plan — no empty series."""
        labels = [series.label for series in view_model.charts[1].series]
        assert "Employment" in labels
        assert "Withdrawals (gross)" in labels
        assert "Annuity income" not in labels
        assert "DB pension" not in labels

    def test_annuity_purchase_charts_its_income_series(self) -> None:
        """A planned purchase surfaces as the annuity income series (9.12)."""
        purchase = AnnuityPurchase(
            id=EntityId("charts-annuity"),
            at_age=Decision(value=65, recorded_on=RECORDED),
            fraction_of_pot=Decision(value=Decimal("0.5"), recorded_on=RECORDED),
        )
        plan = household(
            (wrapper("charts-annuity-sipp", SIPP_KIND, "40000"),),
            employment="42000",
            annuity_purchases=(purchase,),
        )
        state = state_with_household(initial_plan_state(), plan, today=TODAY)
        view_model = build_charts_view_model(state)
        values = _series_values(view_model, "Income composition", "Annuity income")
        assert any(value > 0 for value in values)

    def test_income_sources_never_overlap(self, view_model: ChartsViewModel) -> None:
        """The stack draws only from non-overlapping income sources.

        Pension and annuity tax-free cash already sits inside the
        wrappers' gross withdrawals, so the person-level lump-sum
        columns must never chart alongside them (they are views of
        the same cash); only the DB commutation lump sum is extra.
        """
        labels = {series.label for series in view_model.charts[1].series}
        assert labels <= {
            "Employment",
            "DB pension",
            "DB lump sum",
            "State pension",
            "Annuity income",
            "Withdrawals (gross)",
        }

    def test_tax_chart_is_a_single_series(self, view_model: ChartsViewModel) -> None:
        """Household tax due, one stacked series."""
        tax_chart = view_model.charts[2]
        assert [series.label for series in tax_chart.series] == ["Tax due"]
        peak = max(tax_chart.series[0].values)
        assert peak > 0
        assert tax_chart.y_axis_max >= peak

    def test_nominal_toggle_re_presents_the_same_ledger(
        self, projected: PlanState, view_model: ChartsViewModel
    ) -> None:
        """Nominal amounts exceed real ones once inflation has run."""
        nominal = build_charts_view_model(projected, basis=ReportBasis.NOMINAL)
        assert nominal.selected_basis_key == "nominal"
        for chart in nominal.charts:
            assert "nominal" in chart.y_axis_label
        real_employment = _series_values(view_model, "Income composition", "Employment")
        nominal_employment = _series_values(nominal, "Income composition", "Employment")
        assert nominal_employment[0] == real_employment[0]
        assert nominal_employment[1] > real_employment[1]


@pytest.fixture(scope="module", name="two_isas")
def two_isas_fixture() -> ChartsViewModel:
    """A projected state whose person holds two ISAs and no salary."""
    plan = household(
        (
            wrapper("isa-a", ISA_KIND, "150000"),
            wrapper("isa-b", ISA_KIND, "150000"),
        ),
        employment=None,
    )
    state = state_with_household(initial_plan_state(), plan, today=TODAY)
    return build_charts_view_model(state)


class TestAllocationNote:
    """The charts screen states what each wrapper is invested in."""

    def test_glide_path_followers_name_the_default_shape(
        self, view_model: ChartsViewModel
    ) -> None:
        """The note names the glide path and its provenance.

        With nothing stated, the modelled mix must never be silent.
        """
        note = view_model.allocation_note
        assert note.startswith("Invested as — ")
        assert "ISA: glide path" in note
        assert "80% equity de-risking to 40%" in note
        assert "(shipped default)" in note

    def test_a_stated_allocation_reads_stated(self) -> None:
        """A wrapper with its own split names the split, marked stated."""
        stated = Wrapper(
            id=EntityId("alloc-isa"),
            kind=ISA_KIND,
            balance=money_fact("25000"),
            allocation=AssetAllocation(equity=Decimal(1), bonds=Decimal(0)),
        )
        state = state_with_household(
            initial_plan_state(), household((stated,), employment=None), today=TODAY
        )
        note = build_charts_view_model(state).allocation_note
        assert "ISA: 100% equity (stated)" in note

    def test_an_overridden_glide_shape_reads_your_override(self) -> None:
        """Overriding the shape re-labels the glide provenance."""
        override_text = (
            "equity_start = 1.0\n"
            "derisk_years_before_retirement = 1\n"
            "equity_at_retirement = 1.0\n"
            "transition = linear\n"
            "in_drawdown = hold"
        )
        outcome = state_with_override(
            initial_plan_state(),
            "glidepath.default_shape",
            override_text,
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        state = state_with_household(
            outcome.state,
            household((wrapper("alloc-isa2", ISA_KIND, "25000"),), employment=None),
            today=TODAY,
        )
        note = build_charts_view_model(state).allocation_note
        assert "100% equity throughout (your override)" in note

    def test_a_wrapperless_household_has_no_note(self) -> None:
        """DB/state-pension-only plans invest nothing — no dangling prefix."""
        state = state_with_household(
            initial_plan_state(), household((), employment="42000"), today=TODAY
        )
        assert state.result is not None
        assert build_charts_view_model(state).allocation_note == ""

    def test_no_projection_no_note(self) -> None:
        """The empty state carries no allocation line."""
        view_model = build_charts_view_model(initial_plan_state())
        assert view_model.allocation_note == ""


class TestWrapperLabelDisambiguation:
    """Two wrappers of one kind chart under distinguishable labels."""

    def test_duplicate_kinds_are_numbered_in_first_seen_order(
        self, two_isas: ChartsViewModel
    ) -> None:
        """Repeated kinds are numbered, never labelled by entity id.

        The kind name alone would be ambiguous, and entity ids are
        generated UUIDs — not copy a legend should ever show.
        """
        labels = [series.label for series in two_isas.charts[0].series]
        assert labels == ["ISA 1", "ISA 2"]

    def test_all_zero_tax_still_renders_an_axis(
        self, two_isas: ChartsViewModel
    ) -> None:
        """ISA-only funding is tax-free; the axis floor keeps a range."""
        tax_chart = two_isas.charts[2]
        assert all(value == 0 for value in tax_chart.series[0].values)
        assert tax_chart.y_axis_max == 1


class TestBarTooltip:
    """The bar hover copy shells bind to their plotting widgets."""

    def test_names_the_series_and_period_with_the_formatted_amount(self) -> None:
        """Series label, category, and pounds-and-pence amount."""
        text = bar_tooltip("2043", "State pension", Decimal("12345.67"))
        assert text == "State pension\n2043: £12,345.67"


class TestChartTable:
    """Every chart's numbers re-read as a table (roadmap 9.26)."""

    def test_a_projected_chart_tables_its_series_by_period(
        self, view_model: ChartsViewModel
    ) -> None:
        """Period column first, one money column per stacked series."""
        chart = view_model.charts[0]
        table = chart_table(chart, view_model.categories)
        assert table.columns == (
            PERIOD_COLUMN_LABEL,
            *(entry.label for entry in chart.series),
        )
        assert len(table.rows) == len(view_model.categories)
        assert table.rows[0][0] == view_model.categories[0]
        assert table.rows[0][1] == format_money(Money(chart.series[0].values[0]))

    def test_fills_and_bands_column_in_legend_order(self) -> None:
        """Fills state their intervals; overlay lines column as money.

        The column order matches the chart legend's reading order —
        series, then fills, then bands — so the table reads like the
        graph it mirrors.
        """
        chart = ChartSpec(
            title="fan",
            y_axis_label="y",
            y_axis_max=Decimal(10),
            series=(ChartSeries(label="Series", values=(Decimal(1), Decimal(2))),),
            bands=(ChartBand(label="Median", values=(Decimal(3), Decimal(4))),),
            fills=(
                ChartFill(
                    label="5th-95th",
                    lower=(Decimal(5), Decimal(6)),
                    upper=(Decimal(7), Decimal(8)),
                ),
            ),
        )
        table = chart_table(chart, ("2026", "2027"))
        assert table.columns == (PERIOD_COLUMN_LABEL, "Series", "5th-95th", "Median")
        assert table.rows == (
            ("2026", "£1.00", "£5.00 to £7.00", "£3.00"),
            ("2027", "£2.00", "£6.00 to £8.00", "£4.00"),
        )
