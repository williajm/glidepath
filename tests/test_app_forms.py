"""Facts entry form tests (issue 8.2, §1, §5.1).

The acceptance criterion — every §5.1 fact enterable with its
``as_of`` date — is pinned by the spec sweep and the happy-path
round trip into `Fact`/`Decision`-wrapped domain objects.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    FactsFormData,
    build_facts_form_view_model,
    format_form_errors,
    parse_facts_form,
)
from glidepath.core import (
    AssumptionKey,
    Household,
    Money,
    ReliefMechanic,
    RevaluationReference,
    Sex,
)
from glidepath.regions.uk import RUK_RESIDENCY, SCOTLAND_RESIDENCY, WORKPLACE_DC_KIND

RECORDED = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
TODAY = RECORDED.date()


def person_values(**overrides: str) -> dict[str, str]:
    """A minimal valid person section, with overrides."""
    values = {
        "date_of_birth": "1984-05-20",
        "tax_residency": str(RUK_RESIDENCY),
        "target_retirement_age": "62",
    }
    values.update(overrides)
    return values


def parse(data: FactsFormData) -> Household:
    """Parse a submission expected to succeed."""
    result = parse_facts_form(data, recorded_on=RECORDED)
    assert result.errors == ()
    assert result.household is not None
    return result.household


class TestFormSpec:
    """Every §5.1 fact is enterable, with its ``as_of`` date (8.2)."""

    def test_person_section_covers_the_person_facts(self) -> None:
        """DOB, sex, income, and the pre-existing access facts are present."""
        keys = {spec.key for spec in build_facts_form_view_model().person.fields}
        assert keys == {
            "date_of_birth",
            "date_of_birth_as_of",
            "sex_for_longevity",
            "sex_as_of",
            "tax_residency",
            "employment_income",
            "employment_income_as_of",
            "target_retirement_age",
            "mpaa_triggered_on",
            "mpaa_as_of",
            "lsa_used",
            "lsa_as_of",
        }

    def test_wrapper_section_covers_balances_and_contributions(self) -> None:
        """Balances (with as_of) and the contribution terms are present."""
        keys = {spec.key for spec in build_facts_form_view_model().wrapper.fields}
        assert keys == {
            "kind",
            "balance",
            "crystallised_balance",
            "balances_as_of",
            "employee_contribution",
            "employer_contribution",
            "contributions_as_of",
            "relief_mechanic",
            "escalation",
        }

    def test_db_section_covers_the_scheme_parameters(self) -> None:
        """Every §5.1 DB scheme parameter is present."""
        keys = {spec.key for spec in build_facts_form_view_model().db_pension.fields}
        assert keys == {
            "accrued_annual_pension",
            "statement_date",
            "normal_pension_age",
            "revaluation_reference",
            "revaluation_cap",
            "revaluation_fixed_rate",
            "early_late_factors",
            "commutation_factor",
            "taken_at_age",
            "commuted_fraction",
        }

    def test_state_pension_section_covers_forecast_and_ni_record(self) -> None:
        """Forecast, protected payment, and the NI record are present."""
        keys = {spec.key for spec in build_facts_form_view_model().state_pension.fields}
        assert keys == {
            "forecast_weekly_amount",
            "protected_payment",
            "forecast_as_of",
            "ni_record_start",
            "qualifying_years",
            "ni_as_of",
            "planned_extra_years",
            "deferral_years",
        }

    def test_required_choices_start_blank(self) -> None:
        """No required choice pre-selects a real value — never a guess (§1)."""
        form = build_facts_form_view_model()
        for section in form.sections:
            for spec in section.fields:
                if spec.choices and spec.required:
                    assert spec.choices[0].value == ""

    def test_repeatable_sections_are_marked(self) -> None:
        """Wrappers and DB pensions repeat; the others do not."""
        form = build_facts_form_view_model()
        assert form.wrapper.repeatable
        assert form.db_pension.repeatable
        assert not form.person.repeatable
        assert not form.spending.repeatable
        assert not form.state_pension.repeatable


class TestPersonParsing:
    """The person section round-trips into provenance-wrapped values."""

    def test_minimal_person(self) -> None:
        """DOB, residency, and retirement age are enough for a household."""
        household = parse(FactsFormData(person=person_values()))
        [person] = household.persons
        assert person.date_of_birth.value == date(1984, 5, 20)
        assert person.date_of_birth.as_of == TODAY
        assert person.date_of_birth.recorded_on == RECORDED
        assert person.target_retirement_age.value == 62
        assert person.tax_residency == RUK_RESIDENCY
        assert person.sex_for_longevity is None
        assert person.employment_income is None
        assert person.state_pension is None
        assert household.spending is None

    def test_full_person_facts(self) -> None:
        """Optional person facts parse with their own as_of dates."""
        household = parse(
            FactsFormData(
                person=person_values(
                    date_of_birth_as_of="2026-07-01",
                    sex_for_longevity="female",
                    sex_as_of="2026-06-15",
                    employment_income="£52,000",
                    employment_income_as_of="2026-04-06",
                    mpaa_triggered_on="2024-01-15",
                    mpaa_as_of="2024-01-20",
                    lsa_used="1250.50",
                    lsa_as_of="2025-03-31",
                )
            )
        )
        [person] = household.persons
        assert person.date_of_birth.as_of == date(2026, 7, 1)
        assert person.sex_for_longevity is not None
        assert person.sex_for_longevity.value is Sex.FEMALE
        assert person.sex_for_longevity.as_of == date(2026, 6, 15)
        assert person.employment_income is not None
        assert person.employment_income.value == Money(Decimal(52000))
        assert person.employment_income.as_of == date(2026, 4, 6)
        assert person.mpaa_triggered_on is not None
        assert person.mpaa_triggered_on.value == date(2024, 1, 15)
        assert person.mpaa_triggered_on.as_of == date(2024, 1, 20)
        assert person.lsa_used is not None
        assert person.lsa_used.value == Money(Decimal("1250.50"))
        assert person.lsa_used.as_of == date(2025, 3, 31)

    def test_scottish_residency_is_enterable(self) -> None:
        """Scotland parses; projection support arrives with roadmap 9.1."""
        household = parse(
            FactsFormData(person=person_values(tax_residency=str(SCOTLAND_RESIDENCY)))
        )
        assert household.persons[0].tax_residency == SCOTLAND_RESIDENCY


class TestWrapperParsing:
    """Wrapper balances and contributions round-trip."""

    def test_wrapper_with_contributions(self) -> None:
        """Balances are facts; the employee contribution is a decision."""
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": str(WORKPLACE_DC_KIND),
                        "balance": "45,000",
                        "crystallised_balance": "1500",
                        "balances_as_of": "2026-07-31",
                        "employee_contribution": "6000",
                        "employer_contribution": "2500",
                        "relief_mechanic": "relief_at_source",
                        "escalation": "earnings",
                    },
                ),
            )
        )
        [wrapper] = household.persons[0].wrappers
        assert wrapper.kind == WORKPLACE_DC_KIND
        assert wrapper.balance.value == Money(Decimal(45000))
        assert wrapper.balance.as_of == date(2026, 7, 31)
        assert wrapper.crystallised_balance is not None
        assert wrapper.crystallised_balance.value == Money(Decimal(1500))
        assert wrapper.contributions is not None
        assert wrapper.contributions.employee_amount.value == Money(Decimal(6000))
        assert wrapper.contributions.employer_amount is not None
        assert wrapper.contributions.employer_amount.value == Money(Decimal(2500))
        assert wrapper.contributions.relief_mechanic is ReliefMechanic.RELIEF_AT_SOURCE
        assert wrapper.contributions.escalation is AssumptionKey.EARNINGS_GROWTH_REAL

    def test_wrapper_without_contributions(self) -> None:
        """Blank contribution fields mean no schedule at all."""
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=({"kind": str(WORKPLACE_DC_KIND), "balance": "45000"},),
            )
        )
        [wrapper] = household.persons[0].wrappers
        assert wrapper.contributions is None
        assert wrapper.balance.as_of == TODAY

    def test_employer_contribution_requires_employee_amount(self) -> None:
        """Employer terms without the employee's own choice are rejected."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": str(WORKPLACE_DC_KIND),
                        "balance": "45000",
                        "employer_contribution": "2500",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "wrapper"
        assert error.field_key == "employee_contribution"


class TestDBPensionParsing:
    """DB scheme parameters round-trip, dated by the statement date."""

    def test_full_scheme(self) -> None:
        """The statement date dates every scheme fact (§5.1)."""
        household = parse(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "cpi",
                        "revaluation_cap": "0.05",
                        "early_late_factors": "60:0.75, 65:1",
                        "commutation_factor": "12",
                        "taken_at_age": "60",
                        "commuted_fraction": "0.25",
                    },
                ),
            )
        )
        [pension] = household.persons[0].db_pensions
        statement = date(2025, 11, 30)
        assert pension.accrued_annual_pension.value == Money(Decimal(8500))
        assert pension.accrued_annual_pension.as_of == statement
        assert pension.statement_date == statement
        assert pension.normal_pension_age.value == 65
        assert pension.normal_pension_age.as_of == statement
        basis = pension.revaluation_basis
        assert basis.reference is RevaluationReference.CPI
        assert basis.cap is not None
        assert basis.cap.value == Decimal("0.05")
        assert pension.early_late_factors.factors == {
            60: Decimal("0.75"),
            65: Decimal(1),
        }
        assert pension.commutation_factor is not None
        assert pension.commutation_factor.value == Decimal(12)
        assert pension.commutation_factor.as_of == statement
        assert pension.taken_at_age is not None
        assert pension.taken_at_age.value == 60
        assert pension.commuted_fraction.value == Decimal("0.25")

    def test_fixed_basis_without_rate_is_rejected(self) -> None:
        """The core cross-field rule surfaces as a section-level error."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "fixed",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "db_pension"
        assert error.field_key == ""
        assert "fixed_rate" in error.message

    def test_uncovered_taken_at_age_is_rejected(self) -> None:
        """Taking at an age the factor table misses is a loud error."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "none",
                        "taken_at_age": "58",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "db_pension"

    def test_garbled_factor_table_is_rejected(self) -> None:
        """Factor pairs must be age:factor."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "cpi",
                        "early_late_factors": "sixty to 0.75",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        assert any(error.field_key == "early_late_factors" for error in result.errors)


class TestStatePensionParsing:
    """The state pension section is optional as a whole."""

    def test_forecast_and_ni_record(self) -> None:
        """Forecast and NI facts parse with their shared as_of dates."""
        household = parse(
            FactsFormData(
                person=person_values(),
                state_pension={
                    "forecast_weekly_amount": "230.25",
                    "protected_payment": "12.50",
                    "forecast_as_of": "2026-06-01",
                    "ni_record_start": "2016-09-01",
                    "qualifying_years": "18",
                    "planned_extra_years": "9",
                    "deferral_years": "1.25",
                },
            )
        )
        record = household.persons[0].state_pension
        assert record is not None
        assert record.forecast_weekly_amount is not None
        assert record.forecast_weekly_amount.value == Money(Decimal("230.25"))
        assert record.forecast_weekly_amount.as_of == date(2026, 6, 1)
        assert record.protected_payment is not None
        assert record.protected_payment.as_of == date(2026, 6, 1)
        assert record.ni_record_start is not None
        assert record.ni_record_start.value == date(2016, 9, 1)
        assert record.qualifying_years is not None
        assert record.qualifying_years.value == 18
        assert record.planned_extra_years.value == 9
        assert record.deferral_years.value == Decimal("1.25")

    def test_blank_section_means_not_modelled(self) -> None:
        """All-blank state pension values leave the record unset."""
        household = parse(
            FactsFormData(
                person=person_values(),
                state_pension={"forecast_weekly_amount": "", "qualifying_years": " "},
            )
        )
        assert household.persons[0].state_pension is None

    def test_decisions_default_to_zero(self) -> None:
        """Extra years and deferral default to none when blank."""
        household = parse(
            FactsFormData(
                person=person_values(),
                state_pension={"forecast_weekly_amount": "230.25"},
            )
        )
        record = household.persons[0].state_pension
        assert record is not None
        assert record.planned_extra_years.value == 0
        assert record.deferral_years.value == Decimal(0)


class TestSpendingParsing:
    """Household spending is a fact in today's money."""

    def test_spending_round_trips(self) -> None:
        """The spending need parses with its as_of date."""
        household = parse(
            FactsFormData(
                person=person_values(),
                spending={
                    "annual_spending_real": "28000",
                    "annual_spending_real_as_of": "2026-05-01",
                },
            )
        )
        assert household.spending is not None
        fact = household.spending.annual_spending_real
        assert fact.value == Money(Decimal(28000))
        assert fact.as_of == date(2026, 5, 1)


class TestValidationMessages:
    """Rejections are field-addressed and human-readable."""

    def test_missing_required_fields(self) -> None:
        """A blank person section reports every missing required field."""
        result = parse_facts_form(FactsFormData(), recorded_on=RECORDED)
        assert result.household is None
        missing = {error.field_key for error in result.errors}
        assert missing == {
            "date_of_birth",
            "tax_residency",
            "target_retirement_age",
        }

    def test_bad_values_are_field_addressed(self) -> None:
        """Unparsable text lands on the field that carried it."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(
                    date_of_birth="20/05/1984",
                    target_retirement_age="sixty",
                    lsa_used="lots",
                )
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        by_field = {error.field_key: error.message for error in result.errors}
        assert "YYYY-MM-DD" in by_field["date_of_birth"]
        assert "whole number" in by_field["target_retirement_age"]
        assert "amount of money" in by_field["lsa_used"]

    def test_unknown_choice_value_is_rejected(self) -> None:
        """A choice value outside the option list is rejected."""
        result = parse_facts_form(
            FactsFormData(person=person_values(sex_for_longevity="other")),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "sex_for_longevity"

    def test_negative_balance_surfaces_the_core_message(self) -> None:
        """Core construction rules surface directly (§5.1 validation)."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=({"kind": str(WORKPLACE_DC_KIND), "balance": "-100"},),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "wrapper"
        assert error.index == 0

    def test_non_finite_amounts_are_rejected(self) -> None:
        """Infinity and NaN never reach the Decimal domain values."""
        result = parse_facts_form(
            FactsFormData(person=person_values(lsa_used="Infinity")),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "lsa_used"
        assert "amount of money" in error.message

    def test_non_finite_decimal_is_rejected(self) -> None:
        """A NaN fraction is rejected at parse time."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "cpi",
                        "commuted_fraction": "NaN",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "commuted_fraction"
        assert "plain number" in error.message

    def test_blank_repeatable_sections_report_their_required_fields(self) -> None:
        """An added-but-untouched wrapper or DB entry is field-addressed."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=({},),
                db_pensions=({},),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        wrapper_fields = {
            error.field_key for error in result.errors if error.section == "wrapper"
        }
        assert wrapper_fields == {"kind", "balance"}
        db_fields = {
            error.field_key for error in result.errors if error.section == "db_pension"
        }
        assert "accrued_annual_pension" in db_fields
        assert "statement_date" in db_fields
        assert "normal_pension_age" in db_fields
        assert "revaluation_reference" in db_fields

    def test_negative_spending_surfaces_the_core_message(self) -> None:
        """The spending plan's own validation lands on its section."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                spending={"annual_spending_real": "-1"},
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "spending"
        assert error.field_key == ""

    def test_protected_payment_without_forecast_is_rejected(self) -> None:
        """The state pension record's cross-field rule surfaces (§5.1)."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                state_pension={"protected_payment": "12.50"},
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "state_pension"
        assert error.field_key == ""

    def test_bad_ni_numbers_are_field_addressed(self) -> None:
        """Unparsable NI-record numbers land on their fields."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                state_pension={
                    "qualifying_years": "eighteen",
                    "deferral_years": "a bit",
                    "ni_record_start": "long ago",
                },
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        by_field = {error.field_key for error in result.errors}
        assert by_field == {"qualifying_years", "deferral_years", "ni_record_start"}

    @pytest.mark.parametrize("factor", ["NaN", "Infinity"])
    def test_non_finite_factors_are_rejected(self, factor: str) -> None:
        """NaN and Infinity factors are form errors, never domain values."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "cpi",
                        "early_late_factors": f"60:{factor}",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "early_late_factors"

    def test_zero_age_factor_table_surfaces_the_core_message(self) -> None:
        """Factor-table validation (positive ages) surfaces on the field."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "cpi",
                        "early_late_factors": "0:1",
                    },
                ),
            ),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "early_late_factors"
        assert "positive" in error.message

    def test_negative_lsa_surfaces_the_person_level_message(self) -> None:
        """Person-level validation lands as a section-level error."""
        result = parse_facts_form(
            FactsFormData(person=person_values(lsa_used="-1")),
            recorded_on=RECORDED,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "person"
        assert error.field_key == ""

    def test_format_form_errors_handles_section_level_errors(self) -> None:
        """A section-level error formats without a field label."""
        form = build_facts_form_view_model()
        result = parse_facts_form(
            FactsFormData(person=person_values(lsa_used="-1")),
            recorded_on=RECORDED,
        )
        text = format_form_errors(form, result.errors)
        assert text.startswith("About you: ")

    def test_format_form_errors_labels_sections_and_fields(self) -> None:
        """Formatted errors carry the section title, index, and field label."""
        form = build_facts_form_view_model()
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {"kind": str(WORKPLACE_DC_KIND), "balance": "45000"},
                    {"kind": str(WORKPLACE_DC_KIND), "balance": "bad"},
                ),
            ),
            recorded_on=RECORDED,
        )
        text = format_form_errors(form, result.errors)
        assert "Savings wrapper 2" in text
        assert "Balance" in text
        assert "amount of money" in text
