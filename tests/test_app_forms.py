"""Facts entry form tests (issue 8.2, §1, §5.1).

The acceptance criterion — every §5.1 fact enterable with its
``as_of`` date — is pinned by the spec sweep and the happy-path
round trip into `Fact`/`Decision`-wrapped domain objects.
"""

from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from glidepath.app import (
    ENTITY_ID_KEY,
    FactsFormData,
    FieldKind,
    build_facts_form_view_model,
    example_facts_form_data,
    facts_form_data_from_household,
    form_cannot_represent,
    format_form_errors,
    parse_facts_form,
)
from glidepath.core import (
    AnnuityBasis,
    AnnuityType,
    AssetAllocation,
    AssumptionKey,
    AssumptionSet,
    Decision,
    DecisionTarget,
    EntityId,
    FeeSchedule,
    GlidePathConfig,
    GlidePathPoint,
    Household,
    LifeStage,
    Money,
    Override,
    PlannedOutflow,
    Rate,
    ReliefMechanic,
    RevaluationReference,
    Scenario,
    Sex,
    TaxResidencyId,
    WrapperKindId,
    scenario_orphans,
)
from glidepath.regions.uk import (
    CASH_KIND,
    ISA_KIND,
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    WORKPLACE_DC_KIND,
)

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
    result = parse_facts_form(data, recorded_on=RECORDED, today=TODAY)
    assert result.errors == ()
    assert result.household is not None
    return result.household


class TestFormSpec:
    """Every §5.1 fact is enterable; only statement-dated facts offer as_of."""

    def test_person_section_covers_the_person_facts(self) -> None:
        """DOB, sex, income, and the pre-existing access facts are present."""
        keys = {spec.key for spec in build_facts_form_view_model().person.fields}
        assert keys == {
            "date_of_birth",
            "sex_for_longevity",
            "tax_residency",
            "employment_income",
            "target_retirement_age",
            "mpaa_triggered_on",
            "lsa_used",
        }

    def test_wrapper_section_covers_balances_and_contributions(self) -> None:
        """Balances (with as_of) and the contribution terms are present."""
        keys = {spec.key for spec in build_facts_form_view_model().wrapper.fields}
        assert keys == {
            "kind",
            "balance",
            "crystallised_balance",
            "balances_as_of",
            "equity_percent",
            "employee_contribution",
            "employer_contribution",
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
            "accrual_rate",
            "pensionable_salary",
            "active_until_age",
        }

    def test_annuity_section_covers_the_purchase_decisions(self) -> None:
        """Age, pot fraction, and product type are present — all choices."""
        form = build_facts_form_view_model()
        keys = {spec.key for spec in form.annuity_purchase.fields}
        assert keys == {"at_age", "fraction_of_pot", "annuity_type"}

    def test_state_pension_section_covers_the_forecast(self) -> None:
        """The forecast facts and the deferral choice are present (#97)."""
        keys = {spec.key for spec in build_facts_form_view_model().state_pension.fields}
        assert keys == {
            "forecast_weekly_amount",
            "protected_payment",
            "forecast_as_of",
            "deferral_years",
        }

    def test_date_fields_are_marked_for_date_entry(self) -> None:
        """Exactly the date-valued fields carry ``FieldKind.DATE``."""
        form = build_facts_form_view_model()
        marked = {
            spec.key
            for section in form.sections
            for spec in section.fields
            if spec.kind is FieldKind.DATE
        }
        assert marked == {
            "date_of_birth",
            "mpaa_triggered_on",
            "balances_as_of",
            "forecast_as_of",
            "statement_date",
        }

    def test_required_choices_start_blank(self) -> None:
        """No required choice pre-selects a real value — never a guess (§1)."""
        form = build_facts_form_view_model()
        for section in form.sections:
            for spec in section.fields:
                if spec.choices and spec.required:
                    assert spec.choices[0].value == ""

    def test_repeatable_sections_are_marked(self) -> None:
        """Wrappers, DB pensions, and annuity purchases repeat; others do not."""
        form = build_facts_form_view_model()
        assert form.wrapper.repeatable
        assert form.db_pension.repeatable
        assert form.annuity_purchase.repeatable
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
        """Optional person facts parse, dated the day they were entered."""
        household = parse(
            FactsFormData(
                person=person_values(
                    sex_for_longevity="female",
                    employment_income="£52,000",
                    mpaa_triggered_on="2024-01-15",
                    lsa_used="1250.50",
                )
            )
        )
        [person] = household.persons
        assert person.date_of_birth.as_of == TODAY
        assert person.sex_for_longevity is not None
        assert person.sex_for_longevity.value is Sex.FEMALE
        assert person.sex_for_longevity.as_of == TODAY
        assert person.employment_income is not None
        assert person.employment_income.value == Money(Decimal(52000))
        assert person.employment_income.as_of == TODAY
        assert person.mpaa_triggered_on is not None
        assert person.mpaa_triggered_on.value == date(2024, 1, 15)
        assert person.mpaa_triggered_on.as_of == TODAY
        assert person.lsa_used is not None
        assert person.lsa_used.value == Money(Decimal("1250.50"))
        assert person.lsa_used.as_of == TODAY

    def test_scottish_residency_is_enterable(self) -> None:
        """Scotland parses into the Scottish residency id."""
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

    def test_a_stated_equity_percent_becomes_the_allocation(self) -> None:
        """Entering 100 states full equity; the remainder convention gives 0 bonds."""
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": str(ISA_KIND),
                        "balance": "45000",
                        "equity_percent": "100",
                    },
                ),
            )
        )
        [wrapper] = household.persons[0].wrappers
        assert wrapper.allocation == AssetAllocation(
            equity=Decimal(1), bonds=Decimal(0)
        )

    def test_a_fractional_equity_percent_round_trips(self) -> None:
        """A fractional percent parses exactly and echoes back unchanged."""
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": str(ISA_KIND),
                        "balance": "45000",
                        "equity_percent": "62.5",
                    },
                ),
            )
        )
        [wrapper] = household.persons[0].wrappers
        assert wrapper.allocation == AssetAllocation(
            equity=Decimal("0.625"), bonds=Decimal("0.375")
        )
        values = facts_form_data_from_household(household)
        assert values.wrappers[0]["equity_percent"] == "62.5"

    def test_blank_equity_percent_follows_the_glide_path(self) -> None:
        """No entry keeps the wrapper on the default glide path."""
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=({"kind": str(ISA_KIND), "balance": "45000"},),
            )
        )
        [wrapper] = household.persons[0].wrappers
        assert wrapper.allocation is None

    def test_an_equity_percent_on_a_cash_account_is_rejected(self) -> None:
        """Cash accounts always hold cash; an entry is an error, not ignored."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": str(CASH_KIND),
                        "balance": "5000",
                        "equity_percent": "50",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "wrapper"
        assert error.field_key == "equity_percent"

    @pytest.mark.parametrize("percent", ["101", "-1"])
    def test_an_out_of_range_equity_percent_is_rejected(self, percent: str) -> None:
        """The share is a percentage from 0 to 100."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": str(ISA_KIND),
                        "balance": "45000",
                        "equity_percent": percent,
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "equity_percent"

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

    def test_blank_as_of_defaults_to_the_callers_today(self) -> None:
        """A blank date takes the run's civil day, never the UTC date.

        In a zone west of UTC the evening's ``recorded_on`` timestamp
        already carries tomorrow's UTC date; defaulting a balance to
        it would sit one day after the projection's ``today`` and be
        rejected as future-dated by the §4.8 roll-forward check.
        """
        late_evening = datetime(2026, 8, 4, 3, 0, tzinfo=UTC)
        local_today = date(2026, 8, 3)
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=({"kind": str(WORKPLACE_DC_KIND), "balance": "45000"},),
            ),
            recorded_on=late_evening,
            today=local_today,
        )
        assert result.errors == ()
        assert result.household is not None
        [wrapper] = result.household.persons[0].wrappers
        assert wrapper.balance.as_of == local_today
        assert wrapper.balance.recorded_on == late_evening

    def test_cash_wrapper_defaults_to_a_cash_only_allocation(self) -> None:
        """A cash account holds cash, never the glide path (9.2)."""
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {"kind": "uk.cash", "balance": "5000"},
                    {"kind": "uk.gia", "balance": "5000"},
                ),
            )
        )
        cash, gia = household.persons[0].wrappers
        assert cash.allocation is not None
        assert cash.allocation.cash == Decimal(1)
        assert cash.allocation.equity == Decimal(0)
        assert gia.allocation is None  # investment accounts follow the glide path

    def test_crystallised_balance_is_rejected_on_non_pension_kinds(self) -> None:
        """Only pensions hold drawdown funds — a LISA must not (9.2).

        Accepting one would let pre-60 LISA money bypass the access
        gate through the always-open crystallised sub-balance.
        """
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        "kind": "uk.lisa",
                        "balance": "5000",
                        "crystallised_balance": "1000",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "wrapper"
        assert error.field_key == "crystallised_balance"

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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "db_pension"

    def test_active_membership_parses_with_statement_dated_facts(self) -> None:
        """The accrual rate and salary become facts dated by the statement."""
        household = parse(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "cpi",
                        "accrual_rate": "0.0166667",
                        "pensionable_salary": "42000",
                        "active_until_age": "60",
                    },
                ),
            )
        )
        [pension] = household.persons[0].db_pensions
        membership = pension.active_membership
        assert membership is not None
        statement = date(2025, 11, 30)
        assert membership.accrual_rate.value == Decimal("0.0166667")
        assert membership.accrual_rate.as_of == statement
        assert membership.pensionable_salary.value == Money(Decimal(42000))
        assert membership.pensionable_salary.as_of == statement
        assert membership.active_until_age is not None
        assert membership.active_until_age.value == 60

    def test_blank_membership_fields_mean_deferred(self) -> None:
        """Leaving the accrual block empty keeps the pension deferred."""
        household = parse(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "none",
                    },
                ),
            )
        )
        [pension] = household.persons[0].db_pensions
        assert pension.active_membership is None

    def test_salary_without_a_rate_is_rejected(self) -> None:
        """A filled sibling with no accrual rate is an error, not a guess."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "none",
                        "pensionable_salary": "42000",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "accrual_rate"

    def test_a_rate_without_a_salary_is_rejected(self) -> None:
        """Active membership needs both scheme facts."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "none",
                        "accrual_rate": "0.02",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "pensionable_salary"

    def test_active_until_beyond_the_taken_age_is_rejected(self) -> None:
        """The core cross-field rule surfaces as a section-level error."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                db_pensions=(
                    {
                        "accrued_annual_pension": "8500",
                        "statement_date": "2025-11-30",
                        "normal_pension_age": "65",
                        "revaluation_reference": "none",
                        "accrual_rate": "0.02",
                        "pensionable_salary": "42000",
                        "active_until_age": "70",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "db_pension"
        assert "active_until_age" in error.message

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
            today=TODAY,
        )
        assert result.household is None
        assert any(error.field_key == "early_late_factors" for error in result.errors)


class TestAnnuityPurchaseParsing:
    """Annuity purchases round-trip as wholly-decision records (§5.1)."""

    def test_purchase_parses_into_decisions(self) -> None:
        """Age and fraction are decisions; the form writes single-life."""
        household = parse(
            FactsFormData(
                person=person_values(),
                annuity_purchases=(
                    {
                        "at_age": "68",
                        "fraction_of_pot": "0.5",
                        "annuity_type": "level",
                    },
                ),
            )
        )
        [purchase] = household.persons[0].annuity_purchases
        assert purchase.at_age.value == 68
        assert purchase.at_age.recorded_on == RECORDED
        assert purchase.fraction_of_pot.value == Decimal("0.5")
        assert purchase.annuity_type is AnnuityType.LEVEL
        assert purchase.basis is AnnuityBasis.SINGLE

    @pytest.mark.parametrize(
        ("key", "annuity_type"),
        [
            ("escalating", AnnuityType.ESCALATING),
            ("inflation_linked", AnnuityType.INFLATION_LINKED),
        ],
    )
    def test_every_product_type_is_enterable(
        self, key: str, annuity_type: AnnuityType
    ) -> None:
        """The escalating and inflation-linked products parse too."""
        household = parse(
            FactsFormData(
                person=person_values(),
                annuity_purchases=(
                    {"at_age": "68", "fraction_of_pot": "1", "annuity_type": key},
                ),
            )
        )
        [purchase] = household.persons[0].annuity_purchases
        assert purchase.annuity_type is annuity_type

    def test_blank_instance_reports_its_required_fields(self) -> None:
        """An added-but-untouched purchase is field-addressed."""
        result = parse_facts_form(
            FactsFormData(person=person_values(), annuity_purchases=({},)),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        missing = {
            error.field_key
            for error in result.errors
            if error.section == "annuity_purchase"
        }
        assert missing == {"at_age", "fraction_of_pot", "annuity_type"}

    def test_out_of_range_fraction_surfaces_the_core_message(self) -> None:
        """The (0, 1] fraction rule lands as a section-level error."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                annuity_purchases=(
                    {
                        "at_age": "68",
                        "fraction_of_pot": "1.5",
                        "annuity_type": "level",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "annuity_purchase"
        assert error.field_key == ""
        assert "fraction_of_pot" in error.message

    def test_unknown_product_type_is_rejected(self) -> None:
        """A type value outside the option list is rejected on its field."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                annuity_purchases=(
                    {
                        "at_age": "68",
                        "fraction_of_pot": "0.5",
                        "annuity_type": "with_profits",
                    },
                ),
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "annuity_purchase"
        assert error.field_key == "annuity_type"


class TestStatePensionParsing:
    """The state pension section is optional as a whole."""

    def test_forecast_facts_parse(self) -> None:
        """The forecast pair parses with its shared as_of date."""
        household = parse(
            FactsFormData(
                person=person_values(),
                state_pension={
                    "forecast_weekly_amount": "230.25",
                    "protected_payment": "12.50",
                    "forecast_as_of": "2026-06-01",
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
        assert record.deferral_years.value == Decimal("1.25")

    def test_blank_section_means_not_modelled(self) -> None:
        """All-blank state pension values leave the record unset."""
        household = parse(
            FactsFormData(
                person=person_values(),
                state_pension={"forecast_weekly_amount": "", "deferral_years": " "},
            )
        )
        assert household.persons[0].state_pension is None

    def test_deferral_defaults_to_zero(self) -> None:
        """Deferral defaults to none when blank."""
        household = parse(
            FactsFormData(
                person=person_values(),
                state_pension={"forecast_weekly_amount": "230.25"},
            )
        )
        record = household.persons[0].state_pension
        assert record is not None
        assert record.deferral_years.value == Decimal(0)

    def test_entered_section_without_a_forecast_is_refused(self) -> None:
        """The DWP forecast is the only route (§5.1) — demanded by field."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                state_pension={"deferral_years": "1"},
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "state_pension"
        assert error.field_key == "forecast_weekly_amount"
        assert "check-state-pension" in error.message


class TestSpendingParsing:
    """Household spending is a fact in today's money."""

    def test_spending_round_trips(self) -> None:
        """The spending need parses, dated the day it was entered."""
        household = parse(
            FactsFormData(
                person=person_values(),
                spending={"annual_spending_real": "28000"},
            )
        )
        assert household.spending is not None
        fact = household.spending.annual_spending_real
        assert fact.value == Money(Decimal(28000))
        assert fact.as_of == TODAY
        assert household.spending.stage_multipliers is None

    def test_stage_multipliers_parse_per_retirement_phase(self) -> None:
        """The go-go/slow-go/no-go fields bind to their stages (#114)."""
        household = parse(
            FactsFormData(
                person=person_values(),
                spending={
                    "annual_spending_real": "28000",
                    "go_go_multiplier": "1.2",
                    "slow_go_multiplier": "0.9",
                    "no_go_multiplier": "0.8",
                },
            )
        )
        assert household.spending is not None
        assert household.spending.stage_multipliers == {
            LifeStage.GO_GO: Decimal("1.2"),
            LifeStage.SLOW_GO: Decimal("0.9"),
            LifeStage.NO_GO: Decimal("0.8"),
        }

    def test_a_blank_multiplier_stays_absent(self) -> None:
        """Only the entered stages appear; blank means a multiplier of 1."""
        household = parse(
            FactsFormData(
                person=person_values(),
                spending={
                    "annual_spending_real": "28000",
                    "go_go_multiplier": "1.2",
                },
            )
        )
        assert household.spending is not None
        assert household.spending.stage_multipliers == {LifeStage.GO_GO: Decimal("1.2")}

    def test_multipliers_without_spending_are_rejected(self) -> None:
        """A multiplier with nothing to scale asks for the annual need."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                spending={"go_go_multiplier": "1.2"},
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "spending"
        assert error.field_key == "annual_spending_real"

    def test_unparsable_spending_with_a_multiplier_errors_once(self) -> None:
        """Bad amount text keeps its own message; no second prompt piles on."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                spending={
                    "annual_spending_real": "lots",
                    "go_go_multiplier": "1.2",
                },
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.field_key == "annual_spending_real"
        assert "amount of money" in error.message

    def test_non_positive_multiplier_surfaces_the_core_message(self) -> None:
        """The spending plan's own multiplier validation lands on the section."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                spending={
                    "annual_spending_real": "28000",
                    "slow_go_multiplier": "0",
                },
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "spending"
        assert "positive" in error.message


class TestValidationMessages:
    """Rejections are field-addressed and human-readable."""

    def test_missing_required_fields(self) -> None:
        """A blank person section reports every missing required field."""
        result = parse_facts_form(FactsFormData(), recorded_on=RECORDED, today=TODAY)
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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
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
                annuity_purchases=({},),
            ),
            recorded_on=RECORDED,
            today=TODAY,
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
        annuity_fields = {
            error.field_key
            for error in result.errors
            if error.section == "annuity_purchase"
        }
        assert annuity_fields == {"at_age", "fraction_of_pot", "annuity_type"}

    def test_negative_spending_surfaces_the_core_message(self) -> None:
        """The spending plan's own validation lands on its section."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                spending={"annual_spending_real": "-1"},
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "spending"
        assert error.field_key == ""

    def test_protected_payment_without_forecast_is_rejected(self) -> None:
        """A protected payment alone still demands the forecast (§5.1)."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                state_pension={"protected_payment": "12.50"},
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "state_pension"
        assert error.field_key == "forecast_weekly_amount"

    def test_protected_payment_beyond_the_forecast_is_rejected(self) -> None:
        """The record's cross-field rule surfaces on the section (§5.1)."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                state_pension={
                    "forecast_weekly_amount": "200.00",
                    "protected_payment": "250.00",
                },
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        [error] = result.errors
        assert error.section == "state_pension"
        assert error.field_key == ""

    def test_bad_state_pension_numbers_are_field_addressed(self) -> None:
        """Unparsable state pension numbers land on their fields."""
        result = parse_facts_form(
            FactsFormData(
                person=person_values(),
                state_pension={
                    "forecast_weekly_amount": "two hundred",
                    "deferral_years": "a bit",
                },
            ),
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert result.household is None
        by_field = {error.field_key for error in result.errors}
        assert by_field == {"forecast_weekly_amount", "deferral_years"}

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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
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
            today=TODAY,
        )
        text = format_form_errors(form, result.errors)
        assert "Savings wrapper 2" in text
        assert "Balance" in text
        assert "amount of money" in text


class TestEntityIdReuse:
    """Entity ids ride their form rows, so scenario overrides survive (§4.3)."""

    @staticmethod
    def submission() -> FactsFormData:
        """One valid submission with a person and two wrappers."""
        return FactsFormData(
            person=person_values(),
            wrappers=(
                {"kind": str(WORKPLACE_DC_KIND), "balance": "45000"},
                {"kind": str(WORKPLACE_DC_KIND), "balance": "12000"},
            ),
        )

    def test_rendered_rows_carry_their_entity_ids(self) -> None:
        """Rendering a household writes each entity's id into its row."""
        household = parse(self.submission())
        rendered = facts_form_data_from_household(household)
        assert [values[ENTITY_ID_KEY] for values in rendered.wrappers] == [
            str(wrapper.id) for wrapper in household.persons[0].wrappers
        ]

    def test_rows_carrying_ids_keep_them_in_every_section(self) -> None:
        """A row's carried id becomes the parsed entity's id.

        No ``previous`` is passed: identity rides the row itself.
        """
        first = parse(_maximal_submission())
        result = parse_facts_form(
            facts_form_data_from_household(first), recorded_on=RECORDED, today=TODAY
        )
        second = result.household
        assert second is not None
        person, prior = second.persons[0], first.persons[0]
        assert [w.id for w in person.wrappers] == [w.id for w in prior.wrappers]
        assert [p.id for p in person.db_pensions] == [p.id for p in prior.db_pensions]
        assert [a.id for a in person.annuity_purchases] == [
            a.id for a in prior.annuity_purchases
        ]

    def test_carried_ids_are_preserved_verbatim(self) -> None:
        """No normalisation: persistence allows any non-empty id string.

        Scenario overrides target the exact stored id, so even
        stripping whitespace would change identity and orphan them.
        """
        household = parse(
            FactsFormData(
                person=person_values(),
                wrappers=(
                    {
                        ENTITY_ID_KEY: " wrapper-a ",
                        "kind": str(WORKPLACE_DC_KIND),
                        "balance": "45000",
                    },
                    {
                        ENTITY_ID_KEY: " ",
                        "kind": str(WORKPLACE_DC_KIND),
                        "balance": "12000",
                    },
                ),
            )
        )
        padded, whitespace_only = household.persons[0].wrappers
        assert padded.id == EntityId(" wrapper-a ")
        assert whitespace_only.id == EntityId(" ")

    def test_person_reuses_the_prior_plans_id(self) -> None:
        """The (single) person still pairs with the plan being replaced."""
        first = parse(self.submission())
        result = parse_facts_form(
            self.submission(), recorded_on=RECORDED, today=TODAY, previous=first
        )
        second = result.household
        assert second is not None
        assert second.persons[0].id == first.persons[0].id

    def test_deleting_a_row_keeps_the_survivors_id(self) -> None:
        """Dropping row one leaves row two's entity — and its id — intact."""
        first = parse(self.submission())
        rendered = facts_form_data_from_household(first)
        result = parse_facts_form(
            FactsFormData(person=rendered.person, wrappers=rendered.wrappers[1:]),
            recorded_on=RECORDED,
            today=TODAY,
            previous=first,
        )
        second = result.household
        assert second is not None
        [survivor] = second.persons[0].wrappers
        assert survivor.id == first.persons[0].wrappers[1].id

    def test_deleting_a_row_orphans_only_its_override(self) -> None:
        """Scenario targeting: the survivor's override stays addressable."""
        first = parse(
            FactsFormData(
                person=person_values(),
                wrappers=({"kind": str(WORKPLACE_DC_KIND), "balance": "45000"},),
                annuity_purchases=(
                    {"at_age": "68", "fraction_of_pot": "0.5", "annuity_type": "level"},
                    {
                        "at_age": "70",
                        "fraction_of_pot": "0.25",
                        "annuity_type": "level",
                    },
                ),
            )
        )
        deleted, _survivor = first.persons[0].annuity_purchases
        scenario = Scenario(
            name="buy later",
            overrides=tuple(
                Override(
                    target=DecisionTarget(entity_id=purchase.id, field_path="at_age"),
                    value=72,
                )
                for purchase in first.persons[0].annuity_purchases
            ),
        )
        rendered = facts_form_data_from_household(first)
        result = parse_facts_form(
            FactsFormData(
                person=rendered.person,
                wrappers=rendered.wrappers,
                annuity_purchases=rendered.annuity_purchases[1:],
            ),
            recorded_on=RECORDED,
            today=TODAY,
            previous=first,
        )
        second = result.household
        assert second is not None
        orphans = scenario_orphans(scenario, second, AssumptionSet(()))
        assert [override.target for override in orphans] == [
            DecisionTarget(entity_id=deleted.id, field_path="at_age")
        ]

    def test_rows_without_ids_mint_fresh_ids(self) -> None:
        """A hand-typed row is a new entity even when a prior plan exists."""
        first = parse(self.submission())
        result = parse_facts_form(
            self.submission(), recorded_on=RECORDED, today=TODAY, previous=first
        )
        second = result.household
        assert second is not None
        prior_ids = {wrapper.id for wrapper in first.persons[0].wrappers}
        minted_ids = {wrapper.id for wrapper in second.persons[0].wrappers}
        assert prior_ids.isdisjoint(minted_ids)

    def test_without_previous_every_id_is_fresh(self) -> None:
        """Two independent parses share no entity ids."""
        first = parse(self.submission())
        second = parse(self.submission())
        assert second.persons[0].id != first.persons[0].id


def _maximal_submission() -> FactsFormData:
    """A submission exercising every optional field the form offers."""
    return FactsFormData(
        person=person_values(
            sex_for_longevity="male",
            employment_income="52000",
            mpaa_triggered_on="2024-01-15",
            lsa_used="1250.50",
        ),
        spending={
            "annual_spending_real": "28000",
            "go_go_multiplier": "1.2",
            "slow_go_multiplier": "0.9",
            "no_go_multiplier": "0.8",
        },
        state_pension={
            "forecast_weekly_amount": "230.25",
            "protected_payment": "12.40",
            "forecast_as_of": "2026-05-01",
            "deferral_years": "1.25",
        },
        wrappers=(
            {
                "kind": str(WORKPLACE_DC_KIND),
                "balance": "45000",
                "crystallised_balance": "1500",
                "balances_as_of": "2026-07-31",
                "employee_contribution": "6000",
                "employer_contribution": "2500",
                "relief_mechanic": "relief_at_source",
                "escalation": "earnings",
            },
            {"kind": str(WORKPLACE_DC_KIND), "balance": "20000"},
        ),
        db_pensions=(
            {
                "accrued_annual_pension": "8500",
                "statement_date": "2026-05-01",
                "normal_pension_age": "65",
                "revaluation_reference": "cpi",
                "revaluation_cap": "0.05",
                "early_late_factors": "60:0.75, 67:1.1",
                "commutation_factor": "12",
                "taken_at_age": "60",
                "commuted_fraction": "0.25",
                "accrual_rate": "0.0166667",
                "pensionable_salary": "48000",
                "active_until_age": "58",
            },
        ),
        annuity_purchases=(
            {"at_age": "68", "fraction_of_pot": "0.5", "annuity_type": "level"},
        ),
    )


def _altered[T](entity: T, **changes: object) -> T:
    """A copy of a frozen dataclass with ``changes`` applied.

    Explicit reconstruction rather than ``dataclasses.replace`` so the
    result keeps a concrete type for every analyser.
    """
    source = cast("Any", entity)
    values = {spec.name: getattr(source, spec.name) for spec in fields(source)}
    values.update(changes)
    return cast("T", type(source)(**values))


def _with_person(base: Household, **changes: object) -> Household:
    """The household with its (single) person altered."""
    return _altered(base, persons=(_altered(base.persons[0], **changes),))


def _with_first_wrapper(base: Household, **changes: object) -> Household:
    """The household with its first wrapper altered."""
    person = base.persons[0]
    wrappers = (_altered(person.wrappers[0], **changes), *person.wrappers[1:])
    return _with_person(base, wrappers=wrappers)


class TestFormCannotRepresent:
    """Plans richer than the form is refused an edit, never reduced."""

    @pytest.fixture(name="base")
    def base_fixture(self) -> Household:
        """A maximal but fully form-representable household."""
        return parse(_maximal_submission())

    def test_representable_household_passes(self, base: Household) -> None:
        """Everything the form itself produced is representable."""
        assert form_cannot_represent(base) is None

    def test_second_person_is_flagged(self, base: Household) -> None:
        """The v1 form edits exactly one person."""
        person = base.persons[0]
        second = _altered(
            person,
            id=EntityId("forms-second-person"),
            wrappers=(),
            db_pensions=(),
            annuity_purchases=(),
            state_pension=None,
        )
        household = _altered(base, persons=(person, second))
        assert form_cannot_represent(household) == "more than one person"

    def test_planned_outflows_are_flagged(self, base: Household) -> None:
        """Planned outflows have no form section yet."""
        outflow = PlannedOutflow(
            id=EntityId("forms-outflow"),
            label="New roof",
            amount_real=Decision(value=Money(Decimal(20000)), recorded_on=RECORDED),
            at_age_of=(base.persons[0].id, 60),
        )
        household = _altered(base, planned_outflows=(outflow,))
        assert form_cannot_represent(household) == "planned outflows"

    def test_joint_life_annuity_purchase_is_flagged(self, base: Household) -> None:
        """The single-person form offers no basis choice (9.4 spike)."""
        [purchase] = base.persons[0].annuity_purchases
        joint = _altered(purchase, basis=AnnuityBasis.JOINT)
        household = _with_person(base, annuity_purchases=(joint,))
        assert form_cannot_represent(household) == "a joint-life annuity purchase"

    def test_personal_glide_path_is_flagged(self, base: Household) -> None:
        """A per-person glide path has no form field yet."""
        glide = GlidePathConfig(
            points=(
                GlidePathPoint(
                    years_to_retirement=0,
                    allocation=AssetAllocation(
                        equity=Decimal("0.4"), bonds=Decimal("0.6")
                    ),
                ),
            )
        )
        household = _with_person(base, glide_path=glide)
        assert form_cannot_represent(household) == "a personal glide path"

    def test_whole_retirement_multiplier_is_flagged(self, base: Household) -> None:
        """The DECUMULATION fallback key has no form field (issue #114)."""
        spending = _altered(
            base.spending,
            stage_multipliers={LifeStage.DECUMULATION: Decimal("0.9")},
        )
        household = _altered(base, spending=spending)
        assert (
            form_cannot_represent(household)
            == "spending stage multipliers beyond the go-go/slow-go/no-go fields"
        )

    def test_sub_stage_multipliers_are_representable(self, base: Household) -> None:
        """The go-go/slow-go/no-go keys land in form fields (issue #114)."""
        spending = _altered(
            base.spending,
            stage_multipliers={
                LifeStage.GO_GO: Decimal("1.2"),
                LifeStage.SLOW_GO: Decimal("0.9"),
                LifeStage.NO_GO: Decimal("0.8"),
            },
        )
        household = _altered(base, spending=spending)
        assert form_cannot_represent(household) is None

    def test_unknown_residency_is_flagged(self, base: Household) -> None:
        """A residency the form does not list cannot be re-picked."""
        household = _with_person(base, tax_residency=TaxResidencyId("uk-nowhere"))
        assert form_cannot_represent(household) == "the tax residency 'uk-nowhere'"

    def test_unknown_wrapper_kind_is_flagged(self, base: Household) -> None:
        """A wrapper kind the form does not list cannot be re-picked."""
        household = _with_first_wrapper(base, kind=WrapperKindId("offshore_trust"))
        assert form_cannot_represent(household) == "the wrapper kind 'offshore_trust'"

    def test_wrapper_fees_are_flagged(self, base: Household) -> None:
        """A per-wrapper fee schedule has no form field yet."""
        fees = FeeSchedule(platform=Rate(Decimal("0.002")), fund=Rate(Decimal("0.001")))
        household = _with_first_wrapper(base, fees=fees)
        assert form_cannot_represent(household) == "a wrapper fee schedule"

    def test_an_equity_bond_allocation_is_representable(self, base: Household) -> None:
        """A stated equity/bond split now round-trips through the form."""
        allocation = AssetAllocation(equity=Decimal(1), bonds=Decimal(0))
        household = _with_first_wrapper(base, allocation=allocation)
        assert form_cannot_represent(household) is None

    def test_a_cash_bearing_allocation_is_flagged(self, base: Household) -> None:
        """The equity-percent field cannot express a cash slice."""
        allocation = AssetAllocation(
            equity=Decimal("0.5"), bonds=Decimal("0.3"), cash=Decimal("0.2")
        )
        household = _with_first_wrapper(base, allocation=allocation)
        assert form_cannot_represent(household) == "an asset allocation holding cash"

    def test_split_dated_balances_are_flagged(self, base: Household) -> None:
        """The form shares one as_of between the two balances."""
        wrapper = base.persons[0].wrappers[0]
        assert wrapper.crystallised_balance is not None
        moved = _altered(wrapper.crystallised_balance, as_of=date(2026, 6, 1))
        household = _with_first_wrapper(base, crystallised_balance=moved)
        assert (
            form_cannot_represent(household)
            == "wrapper balances dated on different days"
        )

    def test_unknown_escalation_is_flagged(self, base: Household) -> None:
        """Only the earnings escalation is offered by the form."""
        wrapper = base.persons[0].wrappers[0]
        assert wrapper.contributions is not None
        contributions = _altered(
            wrapper.contributions, escalation=AssumptionKey.INFLATION_CPI
        )
        household = _with_first_wrapper(base, contributions=contributions)
        assert (
            form_cannot_represent(household)
            == "the contribution escalation 'inflation.cpi'"
        )

    def test_split_dated_forecast_is_flagged(self, base: Household) -> None:
        """The form shares one as_of across the forecast pair."""
        record = base.persons[0].state_pension
        assert record is not None
        assert record.protected_payment is not None
        moved = _altered(record.protected_payment, as_of=date(2026, 6, 1))
        household = _with_person(
            base, state_pension=_altered(record, protected_payment=moved)
        )
        assert (
            form_cannot_represent(household)
            == "state pension forecast facts dated on different days"
        )

    def test_off_statement_db_fact_is_flagged(self, base: Household) -> None:
        """The statement date must date every DB scheme fact."""
        person = base.persons[0]
        pension = person.db_pensions[0]
        moved = _altered(pension.accrued_annual_pension, as_of=date(2026, 6, 1))
        household = _with_person(
            base,
            db_pensions=(_altered(pension, accrued_annual_pension=moved),),
        )
        assert (
            form_cannot_represent(household)
            == "DB scheme facts dated off the statement date"
        )

    def test_noted_fact_is_flagged(self, base: Household) -> None:
        """A note on a fact has no form field, so a resave would drop it."""
        person = base.persons[0]
        noted = _altered(person.date_of_birth, note="from my passport")
        household = _with_person(base, date_of_birth=noted)
        assert form_cannot_represent(household) == "notes on facts or decisions"

    def test_noted_decision_is_flagged(self, base: Household) -> None:
        """A note on a decision is refused exactly like one on a fact."""
        person = base.persons[0]
        noted = _altered(person.target_retirement_age, note="union deal")
        household = _with_person(base, target_retirement_age=noted)
        assert form_cannot_represent(household) == "notes on facts or decisions"

    def test_deeply_nested_note_is_flagged(self, base: Household) -> None:
        """The scan reaches facts inside optional sub-records too."""
        pension = base.persons[0].db_pensions[0]
        membership = pension.active_membership
        assert membership is not None
        noted = _altered(membership.accrual_rate, note="scheme booklet")
        household = _with_person(
            base,
            db_pensions=(
                _altered(
                    pension,
                    active_membership=_altered(membership, accrual_rate=noted),
                ),
            ),
        )
        assert form_cannot_represent(household) == "notes on facts or decisions"


def _plan_with_historical_dates() -> Household:
    """A "loaded plan" whose undated facts carry historical ``as_of`` dates."""
    base = parse(_maximal_submission())
    person = base.persons[0]
    wrapper = person.wrappers[0]
    contributions = wrapper.contributions
    assert contributions is not None
    assert contributions.employer_amount is not None
    assert person.employment_income is not None
    dated_wrapper = _altered(
        wrapper,
        contributions=_altered(
            contributions,
            employer_amount=_altered(
                contributions.employer_amount, as_of=date(2026, 7, 1)
            ),
        ),
    )
    household = _with_person(
        base,
        date_of_birth=_altered(person.date_of_birth, as_of=date(2020, 1, 15)),
        employment_income=_altered(person.employment_income, as_of=date(2026, 4, 6)),
        wrappers=(dated_wrapper, *person.wrappers[1:]),
    )
    spending = household.spending
    assert spending is not None
    return _altered(
        household,
        spending=_altered(
            spending,
            annual_spending_real=_altered(
                spending.annual_spending_real, as_of=date(2026, 5, 1)
            ),
        ),
    )


class TestFormDataFromHousehold:
    """The inverse mapping repopulates the form from a loaded plan."""

    def test_round_trips_a_maximal_household(self) -> None:
        """Render → reparse reproduces the household exactly, ids included.

        The reparse passes the original as ``previous`` — exactly how
        the shell resubmits after a plan load — so entity ids are
        stable and the two households compare equal field for field.
        """
        household = parse(_maximal_submission())
        rendered = facts_form_data_from_household(household)
        result = parse_facts_form(
            rendered, recorded_on=RECORDED, today=TODAY, previous=household
        )
        assert result.errors == ()
        assert result.household == household

    def test_round_trips_the_launch_example(self) -> None:
        """The launch example survives the same render → reparse cycle."""
        first = parse_facts_form(
            example_facts_form_data(), recorded_on=RECORDED, today=TODAY
        )
        household = first.household
        assert household is not None
        rendered = facts_form_data_from_household(household)
        result = parse_facts_form(
            rendered, recorded_on=RECORDED, today=TODAY, previous=household
        )
        assert result.errors == ()
        assert result.household == household

    def test_unchanged_values_keep_their_stored_as_of_dates(self) -> None:
        """A load → resubmit round trip re-dates no undated fact (§4.5).

        The form offers no ``as_of`` input for these facts, so the
        parse carries the stored dates forward from ``previous`` —
        full household equality proves nothing was silently re-dated.
        """
        stored = _plan_with_historical_dates()
        rendered = facts_form_data_from_household(stored)
        result = parse_facts_form(
            rendered, recorded_on=RECORDED, today=TODAY, previous=stored
        )
        assert result.errors == ()
        assert result.household == stored

    def test_an_edited_value_is_re_dated_to_the_submission_day(self) -> None:
        """A changed fact is a fresh statement; the rest keep their dates."""
        stored = _plan_with_historical_dates()
        rendered = facts_form_data_from_household(stored)
        edited = FactsFormData(
            person={**rendered.person, "employment_income": "60000"},
            spending=rendered.spending,
            state_pension=rendered.state_pension,
            wrappers=rendered.wrappers,
            db_pensions=rendered.db_pensions,
        )
        result = parse_facts_form(
            edited, recorded_on=RECORDED, today=TODAY, previous=stored
        )
        assert result.errors == ()
        assert result.household is not None
        person = result.household.persons[0]
        assert person.employment_income is not None
        assert person.employment_income.value == Money(Decimal(60000))
        assert person.employment_income.as_of == TODAY
        assert person.date_of_birth.as_of == date(2020, 1, 15)

    def test_blank_optionals_render_blank(self) -> None:
        """A minimal household renders empty strings, not literal Nones."""
        household = parse(FactsFormData(person=person_values()))
        rendered = facts_form_data_from_household(household)
        assert rendered.person["employment_income"] == ""
        assert rendered.person["sex_for_longevity"] == ""
        assert rendered.spending == {}
        assert rendered.state_pension == {}
        assert rendered.wrappers == ()
        assert rendered.db_pensions == ()
        assert rendered.annuity_purchases == ()
