"""Tests for the core DB pension model (issue 4.2, planning §5.1)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    DBActiveMembership,
    DBPension,
    Decision,
    EntityId,
    Fact,
    FactorTable,
    Money,
    Rate,
    RevaluationBasis,
    RevaluationReference,
    db_early_late_factor,
    db_service_end_date,
    db_start_date,
    db_taken_age,
    revaluation_factor_for_months,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

CPI_CAPPED_5 = RevaluationBasis(
    reference=RevaluationReference.CPI, cap=Rate(Decimal("0.05"))
)
NO_REVALUATION = RevaluationBasis(reference=RevaluationReference.NONE)


def membership_of(
    *,
    rate: str = "0.0166667",
    salary: str = "42000",
    until: int | None = None,
) -> DBActiveMembership:
    """An active membership built from compact test parameters."""
    return DBActiveMembership(
        accrual_rate=Fact(value=Decimal(rate), as_of=AS_OF, recorded_on=RECORDED),
        pensionable_salary=Fact(
            value=Money(Decimal(salary)), as_of=AS_OF, recorded_on=RECORDED
        ),
        active_until_age=None
        if until is None
        else Decision(value=until, recorded_on=RECORDED),
    )


def pension_of(
    *,
    accrued: str = "8000",
    npa: int = 65,
    basis: RevaluationBasis = NO_REVALUATION,
    factors: dict[int, Decimal] | None = None,
    commuted: str = "0",
    commutation_factor: str | None = None,
    taken_at: int | None = None,
    membership: DBActiveMembership | None = None,
) -> DBPension:
    """A DB pension built from compact test parameters."""
    return DBPension(
        id=EntityId("db-1"),
        accrued_annual_pension=Fact(
            value=Money(Decimal(accrued)), as_of=AS_OF, recorded_on=RECORDED
        ),
        statement_date=date(2024, 8, 1),
        normal_pension_age=Fact(value=npa, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=basis,
        early_late_factors=FactorTable(factors=factors or {}),
        commuted_fraction=Decision(value=Decimal(commuted), recorded_on=RECORDED),
        commutation_factor=None
        if commutation_factor is None
        else Fact(value=Decimal(commutation_factor), as_of=AS_OF, recorded_on=RECORDED),
        taken_at_age=None
        if taken_at is None
        else Decision(value=taken_at, recorded_on=RECORDED),
        active_membership=membership,
    )


class TestRevaluationBasis:
    """The scheme basis: reference, cap, and per-year rate."""

    def test_cpi_rate_is_capped(self) -> None:
        """CPI above the cap revalues at the cap (e.g. CPI max 5%)."""
        assert CPI_CAPPED_5.annual_rate(Decimal("0.08")) == Decimal("0.05")

    def test_cpi_rate_below_the_cap_passes_through(self) -> None:
        """CPI below the cap revalues at CPI."""
        assert CPI_CAPPED_5.annual_rate(Decimal("0.02")) == Decimal("0.02")

    def test_negative_cpi_is_floored_at_zero(self) -> None:
        """Statutory revaluation never reduces the entitlement."""
        assert CPI_CAPPED_5.annual_rate(Decimal("-0.01")) == Decimal(0)

    def test_uncapped_cpi_basis_tracks_cpi(self) -> None:
        """Without a cap the basis follows CPI exactly."""
        basis = RevaluationBasis(reference=RevaluationReference.CPI)
        assert basis.annual_rate(Decimal("0.08")) == Decimal("0.08")

    def test_fixed_rate_ignores_cpi(self) -> None:
        """A fixed basis revalues at its own rate, whatever CPI does."""
        basis = RevaluationBasis(
            reference=RevaluationReference.FIXED, fixed_rate=Rate(Decimal("0.03"))
        )
        assert basis.annual_rate(Decimal("0.10")) == Decimal("0.03")

    def test_none_basis_never_revalues(self) -> None:
        """A frozen entitlement has a zero rate."""
        assert NO_REVALUATION.annual_rate(Decimal("0.10")) == Decimal(0)

    def test_fixed_reference_requires_its_rate(self) -> None:
        """FIXED without a rate is rejected."""
        with pytest.raises(ValueError, match="fixed_rate"):
            RevaluationBasis(reference=RevaluationReference.FIXED)

    def test_fixed_rate_on_other_references_is_rejected(self) -> None:
        """A fixed rate on a CPI basis is contradictory."""
        rate = Rate(Decimal("0.03"))
        with pytest.raises(ValueError, match="fixed_rate"):
            RevaluationBasis(reference=RevaluationReference.CPI, fixed_rate=rate)

    def test_cap_on_non_cpi_references_is_rejected(self) -> None:
        """A cap is meaningless without a CPI reference."""
        cap = Rate(Decimal("0.05"))
        with pytest.raises(ValueError, match="cap"):
            RevaluationBasis(reference=RevaluationReference.NONE, cap=cap)

    def test_negative_cap_is_rejected(self) -> None:
        """A negative cap is a data error."""
        cap = Rate(Decimal("-0.01"))
        with pytest.raises(ValueError, match="cap"):
            RevaluationBasis(reference=RevaluationReference.CPI, cap=cap)


class TestFactorTable:
    """Early/late factor tables are validated scheme facts."""

    def test_rejects_non_positive_ages(self) -> None:
        """A factor for age zero is a data error."""
        factors = {0: Decimal("0.8")}
        with pytest.raises(ValueError, match="ages"):
            FactorTable(factors=factors)

    def test_rejects_non_positive_factors(self) -> None:
        """A zero or negative factor is a data error."""
        factors = {60: Decimal(0)}
        with pytest.raises(ValueError, match="factors"):
            FactorTable(factors=factors)


class TestDBPensionValidation:
    """Scheme facts drive results: inconsistent ones fail at construction."""

    def test_negative_accrued_pension_is_rejected(self) -> None:
        """The accrued entitlement is a non-negative fact."""
        with pytest.raises(ValueError, match="accrued_annual_pension"):
            pension_of(accrued="-1")

    def test_non_positive_normal_pension_age_is_rejected(self) -> None:
        """An NPA of zero is a data error."""
        with pytest.raises(ValueError, match="normal_pension_age"):
            pension_of(npa=0)

    def test_commuted_fraction_beyond_one_is_rejected(self) -> None:
        """More than the whole pension cannot be commuted."""
        with pytest.raises(ValueError, match="commuted_fraction"):
            pension_of(commuted="1.5", commutation_factor="12")

    def test_commutation_requires_the_scheme_factor(self) -> None:
        """Commuting without a stated factor would guess scheme terms."""
        with pytest.raises(ValueError, match="commutation_factor"):
            pension_of(commuted="0.25")

    def test_commutation_factor_must_be_positive(self) -> None:
        """A zero factor would erase pension for nothing."""
        with pytest.raises(ValueError, match="commutation_factor"):
            pension_of(commuted="0.25", commutation_factor="0")

    def test_taking_early_without_a_factor_is_rejected(self) -> None:
        """An early age missing from the factor table is never defaulted."""
        with pytest.raises(ValueError, match="no factor for age 60"):
            pension_of(taken_at=60)

    def test_non_positive_taken_age_is_rejected(self) -> None:
        """A taken-at age of zero is a data error."""
        with pytest.raises(ValueError, match="taken_at_age"):
            pension_of(taken_at=0)

    def test_taking_at_npa_needs_no_factor(self) -> None:
        """The normal pension age carries an implicit factor of 1."""
        pension = pension_of(taken_at=65)
        assert db_early_late_factor(pension) == Decimal(1)


class TestDBHelpers:
    """Resolved age, start date, and factor lookups."""

    def test_taken_age_defaults_to_the_npa(self) -> None:
        """No taken-at decision means benefits start at the NPA."""
        assert db_taken_age(pension_of(npa=65)) == 65

    def test_taken_age_decision_wins(self) -> None:
        """A stated taken-at age overrides the NPA."""
        pension = pension_of(taken_at=60, factors={60: Decimal("0.8")})
        assert db_taken_age(pension) == 60

    def test_start_date_is_the_taken_birthday(self) -> None:
        """Benefits start on the exact birthday (an income entitlement)."""
        pension = pension_of(taken_at=60, factors={60: Decimal("0.8")})
        assert db_start_date(pension, date(1970, 6, 15)) == date(2030, 6, 15)

    def test_early_factor_comes_from_the_table(self) -> None:
        """The stated early-retirement factor is applied verbatim."""
        pension = pension_of(taken_at=60, factors={60: Decimal("0.8")})
        assert db_early_late_factor(pension) == Decimal("0.8")

    def test_a_table_entry_at_npa_overrides_the_implicit_factor(self) -> None:
        """Schemes may state a factor even at NPA; the table wins."""
        pension = pension_of(npa=65, factors={65: Decimal("1.05")})
        assert db_early_late_factor(pension) == Decimal("1.05")


class TestActiveMembership:
    """CARE-style active accrual on a DB pension (roadmap 9.6, §5.1)."""

    def test_zero_accrual_rate_is_rejected(self) -> None:
        """A zero rate accrues nothing — a data error, not a membership."""
        with pytest.raises(ValueError, match="accrual_rate"):
            membership_of(rate="0")

    def test_accrual_rate_beyond_one_is_rejected(self) -> None:
        """No scheme accrues more than the whole salary per year."""
        with pytest.raises(ValueError, match="accrual_rate"):
            membership_of(rate="1.5")

    def test_negative_pensionable_salary_is_rejected(self) -> None:
        """Pensionable salary is a non-negative fact."""
        with pytest.raises(ValueError, match="pensionable_salary"):
            membership_of(salary="-1")

    def test_non_positive_active_until_age_is_rejected(self) -> None:
        """An active-until age of zero is a data error."""
        with pytest.raises(ValueError, match="active_until_age"):
            membership_of(until=0)

    def test_active_until_beyond_the_taken_age_is_rejected(self) -> None:
        """Service cannot outlast the benefits start (planning §5.1)."""
        membership = membership_of(until=66)
        with pytest.raises(ValueError, match="active_until_age"):
            pension_of(npa=65, membership=membership)

    def test_active_until_the_taken_age_is_accepted(self) -> None:
        """Working right up to the benefits start is the boundary case."""
        pension = pension_of(npa=65, membership=membership_of(until=65))
        assert db_service_end_age_matches(pension, date(1970, 6, 15), 65)

    def test_service_ends_at_the_active_until_birthday(self) -> None:
        """The leave-and-defer decision dates the service end exactly."""
        pension = pension_of(npa=65, membership=membership_of(until=60))
        assert db_service_end_date(pension, date(1970, 6, 15)) == date(2030, 6, 15)

    def test_service_defaults_to_the_benefits_start(self) -> None:
        """No leave decision means service runs to the taken-at date."""
        pension = pension_of(npa=65, membership=membership_of())
        start = db_start_date(pension, date(1970, 6, 15))
        assert db_service_end_date(pension, date(1970, 6, 15)) == start


def db_service_end_age_matches(
    pension: DBPension, date_of_birth: date, age: int
) -> bool:
    """Whether service ends on the birthday ``age`` is attained."""
    expected = date(date_of_birth.year + age, date_of_birth.month, date_of_birth.day)
    return db_service_end_date(pension, date_of_birth) == expected


class TestRevaluationFactorForMonths:
    """The pre-run revaluation factor: exact Decimal, whole months."""

    def test_zero_months_is_unity(self) -> None:
        """No time deferred means no revaluation."""
        assert revaluation_factor_for_months(Decimal("0.02"), 0) == Decimal(1)

    def test_whole_years_compound_with_an_integer_exponent(self) -> None:
        """24 months at 2% is exactly 1.02 squared."""
        expected = Decimal("1.02") ** 2
        assert revaluation_factor_for_months(Decimal("0.02"), 24) == expected

    def test_remaining_months_scale_linearly(self) -> None:
        """30 months at 2% is 1.02 squared times (1 + 0.02 x 6/12)."""
        expected = (Decimal("1.02") ** 2) * (
            Decimal(1) + Decimal("0.02") * Decimal(6) / Decimal(12)
        )
        assert revaluation_factor_for_months(Decimal("0.02"), 30) == expected

    def test_negative_months_are_rejected(self) -> None:
        """A negative deferment span is a caller error."""
        rate = Decimal("0.02")
        with pytest.raises(ValueError, match="non-negative"):
            revaluation_factor_for_months(rate, -1)
