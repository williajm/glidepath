"""Tests for withdrawal strategies and plans (roadmap 5.1, planning §5.2).

The engine-side execution of plans is covered in ``test_engine.py``;
here the strategies and the plan/state value types are pinned in
isolation: the tax-aware ordering, the net-defined fixed-real plan, the
gross-defined fixed-percent plan, and the validation each type does.
"""

from decimal import Decimal

import pytest

from glidepath.core import (
    EntityId,
    FixedPercentWithdrawalStrategy,
    FixedRealWithdrawalStrategy,
    GrossDraw,
    GrossWithdrawalPlan,
    GuardrailsWithdrawalStrategy,
    Money,
    NaturalYieldWithdrawalStrategy,
    NetWithdrawalPlan,
    Rate,
    WithdrawalSource,
    WithdrawalSourceId,
    WithdrawalState,
    WrapperKindId,
    tax_aware_order,
)

ZERO = Money(Decimal(0))
ONE = Decimal(1)


def source_of(
    wrapper: str,
    *,
    crystallised: bool = False,
    available: str = "10000",
    tax_free_fraction: str = "0",
    access_open: bool = True,
    natural_yield: str = "0",
    growth_taxable: bool = False,
    person: str = "person-1",
) -> WithdrawalSource:
    """One drawable sub-balance view."""
    return WithdrawalSource(
        id=WithdrawalSourceId(wrapper_id=EntityId(wrapper), crystallised=crystallised),
        kind=WrapperKindId("test.kind"),
        available=Money(Decimal(available)),
        tax_free_fraction=Decimal(tax_free_fraction),
        access_open=access_open,
        person_id=EntityId(person),
        natural_yield=Money(Decimal(natural_yield)),
        growth_taxable=growth_taxable,
    )


def state_of(*sources: WithdrawalSource, year_fraction: str = "1") -> WithdrawalState:
    """A withdrawal state over ``sources``."""
    return WithdrawalState(sources=sources, year_fraction=Decimal(year_fraction))


class TestTaxAwareOrder:
    """The v1 default draw order of planning §5.2."""

    def test_free_then_crystallised_then_open_uncrystallised(self) -> None:
        """Wholly tax-free sources lead, gated pension money comes last.

        Wrapper order puts the pension first, but the ordering is
        tax-aware: the ISA-like wrapper's sub-balances lead, then the
        pension's crystallised funds, then its uncrystallised pot.
        """
        pension_uncrystallised = source_of("pension", tax_free_fraction="0.25")
        pension_crystallised = source_of("pension", crystallised=True)
        isa_uncrystallised = source_of("isa", tax_free_fraction="1")
        isa_crystallised = source_of("isa", crystallised=True, tax_free_fraction="1")
        ordered = tax_aware_order(
            (
                pension_uncrystallised,
                pension_crystallised,
                isa_uncrystallised,
                isa_crystallised,
            )
        )
        assert ordered == (
            isa_uncrystallised,
            isa_crystallised,
            pension_crystallised,
            pension_uncrystallised,
        )

    def test_gate_closed_uncrystallised_is_excluded(self) -> None:
        """An unopened access gate keeps the pot out of the order (§4.1)."""
        gated = source_of("pension", tax_free_fraction="0.25", access_open=False)
        crystallised = source_of("pension", crystallised=True)
        ordered = tax_aware_order((gated, crystallised))
        assert ordered == (crystallised,)

    def test_gate_closed_tax_free_source_is_excluded(self) -> None:
        """The gate binds every group — tax-free kinds included.

        Tax treatment says nothing about accessibility: an age-gated
        wholly tax-free account (a LISA-like kind, roadmap 9.2) stays
        out of the order until its gate opens.
        """
        gated_free = source_of("lisa", tax_free_fraction="1", access_open=False)
        open_free = source_of("isa", tax_free_fraction="1")
        assert tax_aware_order((gated_free, open_free)) == (open_free,)

    def test_wrapper_order_is_preserved_within_groups(self) -> None:
        """Two sources of the same group keep their plan order."""
        first = source_of("isa-a", tax_free_fraction="1")
        second = source_of("isa-b", tax_free_fraction="1")
        assert tax_aware_order((first, second)) == (first, second)

    def test_taxable_growth_sources_lead_the_full_ordering(self) -> None:
        """The full default is GIA/cash → ISA → pension (roadmap 9.2).

        A taxable-growth account keeps accruing income tax on every
        pound left in it, so it drains before the tax-free group even
        though the plan lists it last.
        """
        pension = source_of("pension", tax_free_fraction="0.25")
        crystallised = source_of("pension", crystallised=True)
        isa = source_of("isa", tax_free_fraction="1")
        gia = source_of("gia", tax_free_fraction="1", growth_taxable=True)
        cash = source_of("cash", tax_free_fraction="1", growth_taxable=True)
        ordered = tax_aware_order((pension, crystallised, isa, gia, cash))
        assert ordered == (gia, cash, isa, crystallised, pension)


class TestFixedReal:
    """The net-defined v1 default strategy."""

    def test_targets_the_need_over_the_tax_aware_order(self) -> None:
        """The plan is the whole need against the default ordering."""
        isa = source_of("isa", tax_free_fraction="1")
        pension = source_of("pension", tax_free_fraction="0.25")
        need = Money(Decimal(20000))
        plan = FixedRealWithdrawalStrategy().withdraw(state_of(pension, isa), need)
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == need
        assert plan.order == (isa.id, pension.id)

    def test_never_orders_a_gate_closed_source(self) -> None:
        """Access ages are respected by construction (roadmap 5.1)."""
        gated = source_of("pension", tax_free_fraction="0.25", access_open=False)
        plan = FixedRealWithdrawalStrategy().withdraw(
            state_of(gated), Money(Decimal(1000))
        )
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.order == ()


class TestFixedPercent:
    """The gross-defined fixed-percentage strategy."""

    def test_draws_the_rate_share_of_the_accessible_pot(self) -> None:
        """5% of an accessible 9,000 is 450, taken front of the order."""
        isa = source_of("isa", available="4000", tax_free_fraction="1")
        crystallised = source_of("pension", crystallised=True, available="5000")
        strategy = FixedPercentWithdrawalStrategy(rate=Rate(Decimal("0.05")))
        plan = strategy.withdraw(state_of(crystallised, isa), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (GrossDraw(source=isa.id, amount=Money(Decimal(450))),)

    def test_spills_over_sources_in_order_when_one_runs_out(self) -> None:
        """A draw beyond the first source continues down the order."""
        isa = source_of("isa", available="4000", tax_free_fraction="1")
        crystallised = source_of("pension", crystallised=True, available="5000")
        strategy = FixedPercentWithdrawalStrategy(rate=Rate(Decimal("0.5")))
        plan = strategy.withdraw(state_of(crystallised, isa), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (
            GrossDraw(source=isa.id, amount=Money(Decimal(4000))),
            GrossDraw(source=crystallised.id, amount=Money(Decimal(500))),
        )

    def test_gate_closed_funds_are_outside_the_pot(self) -> None:
        """A closed gate hides the pot from both the base and the draws."""
        open_isa = source_of("isa", available="1000", tax_free_fraction="1")
        gated = source_of(
            "pension",
            available="99000",
            tax_free_fraction="0.25",
            access_open=False,
        )
        strategy = FixedPercentWithdrawalStrategy(rate=Rate(Decimal("0.10")))
        plan = strategy.withdraw(state_of(gated, open_isa), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (
            GrossDraw(source=open_isa.id, amount=Money(Decimal(100))),
        )

    def test_scales_by_the_period_year_fraction(self) -> None:
        """A half-active period halves the annual draw (roadmap 4.6)."""
        isa = source_of("isa", available="10000", tax_free_fraction="1")
        strategy = FixedPercentWithdrawalStrategy(rate=Rate(Decimal("0.04")))
        plan = strategy.withdraw(state_of(isa, year_fraction="0.5"), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (GrossDraw(source=isa.id, amount=Money(Decimal(200))),)

    def test_ignores_the_need_and_skips_empty_sources(self) -> None:
        """Gross-defined: the pot sets the draw, and empties are skipped."""
        empty = source_of("isa-empty", available="0", tax_free_fraction="1")
        isa = source_of("isa", available="1000", tax_free_fraction="1")
        strategy = FixedPercentWithdrawalStrategy(rate=Rate(Decimal("0.10")))
        state = state_of(empty, isa)
        small_need = strategy.withdraw(state, Money(Decimal(1)))
        large_need = strategy.withdraw(state, Money(Decimal(1_000_000)))
        assert small_need == large_need
        assert isinstance(small_need, GrossWithdrawalPlan)
        assert small_need.draws == (
            GrossDraw(source=isa.id, amount=Money(Decimal(100))),
        )

    def test_zero_rate_plans_no_draws(self) -> None:
        """A zero rate is a valid strategy that draws nothing."""
        isa = source_of("isa", tax_free_fraction="1")
        strategy = FixedPercentWithdrawalStrategy(rate=Rate(Decimal(0)))
        plan = strategy.withdraw(state_of(isa), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == ()


class TestGuardrails:
    """The net-defined Guyton-Klinger-style strategy (roadmap 5.3)."""

    def test_within_the_guardrails_the_need_passes_unchanged(self) -> None:
        """A 5% withdrawal rate sits between the default 4% and 6% rails."""
        isa = source_of("isa", available="100000", tax_free_fraction="1")
        need = Money(Decimal(5000))
        plan = GuardrailsWithdrawalStrategy().withdraw(state_of(isa), need)
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == need
        assert plan.order == (isa.id,)

    def test_above_the_upper_guardrail_spending_is_cut(self) -> None:
        """A 7% rate crosses the 6% rail: the target drops by 10%."""
        isa = source_of("isa", available="100000", tax_free_fraction="1")
        plan = GuardrailsWithdrawalStrategy().withdraw(
            state_of(isa), Money(Decimal(7000))
        )
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == Money(Decimal(6300))

    def test_below_the_lower_guardrail_spending_rises(self) -> None:
        """A 2% rate crosses the 4% rail: the target rises by 10%."""
        isa = source_of("isa", available="100000", tax_free_fraction="1")
        plan = GuardrailsWithdrawalStrategy().withdraw(
            state_of(isa), Money(Decimal(2000))
        )
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == Money(Decimal(2200))

    def test_configured_rules_drive_the_adjustment(self) -> None:
        """Custom rails and fractions replace the defaults wholesale."""
        isa = source_of("isa", available="100000", tax_free_fraction="1")
        strategy = GuardrailsWithdrawalStrategy(
            upper_guardrail=Rate(Decimal("0.03")),
            lower_guardrail=Rate(Decimal("0.01")),
            cut_fraction=Decimal("0.5"),
            rise_fraction=Decimal("0.25"),
        )
        plan = strategy.withdraw(state_of(isa), Money(Decimal(4000)))
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == Money(Decimal(2000))

    def test_the_rate_is_annualised_by_the_year_fraction(self) -> None:
        """A half period's need annualises before meeting the rails.

        3,500 over half a year on a 100,000 pot is a 7% annual rate —
        above the 6% rail even though the raw ratio is 3.5%.
        """
        isa = source_of("isa", available="100000", tax_free_fraction="1")
        plan = GuardrailsWithdrawalStrategy().withdraw(
            state_of(isa, year_fraction="0.5"), Money(Decimal(3500))
        )
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == Money(Decimal(3150))

    def test_gate_closed_funds_sit_outside_the_pot(self) -> None:
        """The rate is judged against the accessible pot only (§4.1)."""
        isa = source_of("isa", available="10000", tax_free_fraction="1")
        gated = source_of(
            "pension",
            available="990000",
            tax_free_fraction="0.25",
            access_open=False,
        )
        plan = GuardrailsWithdrawalStrategy().withdraw(
            state_of(gated, isa), Money(Decimal(700))
        )
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == Money(Decimal(630))
        assert plan.order == (isa.id,)

    def test_an_empty_pot_passes_the_need_through(self) -> None:
        """No pot means no rate to judge: the plan simply targets the need."""
        need = Money(Decimal(5000))
        plan = GuardrailsWithdrawalStrategy().withdraw(state_of(), need)
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == need
        assert plan.order == ()

    def test_a_zero_fraction_period_passes_the_need_through(self) -> None:
        """A zero-active period has no annual rate; nothing adjusts."""
        isa = source_of("isa", available="100000", tax_free_fraction="1")
        plan = GuardrailsWithdrawalStrategy().withdraw(
            state_of(isa, year_fraction="0"), ZERO
        )
        assert isinstance(plan, NetWithdrawalPlan)
        assert plan.target == ZERO

    def test_unordered_guardrails_are_rejected(self) -> None:
        """The lower rail must sit strictly below the upper."""
        rail = Rate(Decimal("0.05"))
        with pytest.raises(ValueError, match="lower_guardrail < upper_guardrail"):
            GuardrailsWithdrawalStrategy(upper_guardrail=rail, lower_guardrail=rail)

    def test_adjustment_fractions_outside_unit_interval_are_rejected(self) -> None:
        """A cut above 100% would target negative spending."""
        cut = Decimal("1.5")
        with pytest.raises(ValueError, match="cut_fraction"):
            GuardrailsWithdrawalStrategy(cut_fraction=cut)


class TestNaturalYield:
    """The gross-defined income-only strategy (roadmap 5.3)."""

    def test_draws_each_sources_priced_yield(self) -> None:
        """Every accessible source is drawn by exactly its income."""
        isa = source_of(
            "isa", available="10000", tax_free_fraction="1", natural_yield="250"
        )
        crystallised = source_of(
            "pension", crystallised=True, available="20000", natural_yield="500"
        )
        plan = NaturalYieldWithdrawalStrategy().withdraw(
            state_of(crystallised, isa), Money(Decimal(12000))
        )
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (
            GrossDraw(source=isa.id, amount=Money(Decimal(250))),
            GrossDraw(source=crystallised.id, amount=Money(Decimal(500))),
        )

    def test_zero_yield_sources_are_skipped(self) -> None:
        """A source throwing off no income contributes no draw."""
        idle = source_of("isa-idle", tax_free_fraction="1")
        earning = source_of("isa", tax_free_fraction="1", natural_yield="100")
        plan = NaturalYieldWithdrawalStrategy().withdraw(state_of(idle, earning), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (GrossDraw(source=earning.id, amount=Money(Decimal(100))),)

    def test_the_draw_never_exceeds_the_balance(self) -> None:
        """A priced yield beyond the balance is capped at the balance."""
        isa = source_of(
            "isa", available="80", tax_free_fraction="1", natural_yield="100"
        )
        plan = NaturalYieldWithdrawalStrategy().withdraw(state_of(isa), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == (GrossDraw(source=isa.id, amount=Money(Decimal(80))),)

    def test_gate_closed_sources_are_never_drawn(self) -> None:
        """The tax-aware order excludes closed gates, so no draw forms."""
        gated = source_of(
            "pension",
            tax_free_fraction="0.25",
            access_open=False,
            natural_yield="100",
        )
        plan = NaturalYieldWithdrawalStrategy().withdraw(state_of(gated), ZERO)
        assert isinstance(plan, GrossWithdrawalPlan)
        assert plan.draws == ()

    def test_declares_it_uses_natural_yield(self) -> None:
        """Only this strategy carries the opt-in pricing marker.

        The marker is deliberately not a protocol member: a strategy
        that never declares it — every other shipped one included —
        simply gets no yield pricing, so implementations that only
        define ``withdraw`` keep working.
        """
        assert NaturalYieldWithdrawalStrategy().uses_natural_yield is True
        for unmarked in (
            FixedRealWithdrawalStrategy(),
            FixedPercentWithdrawalStrategy(rate=Rate(Decimal(0))),
            GuardrailsWithdrawalStrategy(),
        ):
            assert getattr(unmarked, "uses_natural_yield", False) is False


class TestValidation:
    """The value types reject impossible amounts at construction."""

    def test_source_rejects_negative_balance(self) -> None:
        """A drawable balance cannot be negative."""
        with pytest.raises(ValueError, match="non-negative"):
            source_of("isa", available="-1")

    def test_source_rejects_fraction_outside_unit_interval(self) -> None:
        """The tax-free fraction is a share of a draw."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            source_of("isa", tax_free_fraction="1.5")

    def test_source_rejects_negative_natural_yield(self) -> None:
        """Priced income cannot be negative (roadmap 5.3)."""
        with pytest.raises(ValueError, match="natural_yield"):
            source_of("isa", natural_yield="-1")

    def test_state_rejects_fraction_outside_unit_interval(self) -> None:
        """The year fraction is a share of a period (roadmap 4.6)."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            state_of(year_fraction="2")

    def test_state_rejects_negative_headroom(self) -> None:
        """Remaining tax-free cash headroom cannot be negative."""
        headroom = {EntityId("person-1"): Money(Decimal(-1))}
        year_fraction = Decimal(1)
        with pytest.raises(ValueError, match="non-negative"):
            WithdrawalState(
                sources=(),
                year_fraction=year_fraction,
                tax_free_cash_headroom=headroom,
            )

    def test_state_accepts_uncapped_headroom_entries(self) -> None:
        """A person under a region with no cap maps to ``None``."""
        headroom = {EntityId("person-1"): None}
        state = WithdrawalState(
            sources=(),
            year_fraction=Decimal(1),
            tax_free_cash_headroom=headroom,
        )
        assert state.tax_free_cash_headroom == {EntityId("person-1"): None}

    def test_net_plan_rejects_negative_target(self) -> None:
        """A negative net target is a strategy bug, not a plan."""
        target = Money(Decimal(-1))
        with pytest.raises(ValueError, match="non-negative"):
            NetWithdrawalPlan(target=target, order=())

    def test_gross_draw_rejects_negative_amount(self) -> None:
        """A negative gross draw is a strategy bug, not a plan."""
        source_id = WithdrawalSourceId(wrapper_id=EntityId("isa"), crystallised=False)
        amount = Money(Decimal(-1))
        with pytest.raises(ValueError, match="non-negative"):
            GrossDraw(source=source_id, amount=amount)

    def test_fixed_percent_rejects_rate_outside_unit_interval(self) -> None:
        """The rate is a share of the pot per year."""
        above = Rate(Decimal("1.01"))
        with pytest.raises(ValueError, match="between 0 and 1"):
            FixedPercentWithdrawalStrategy(rate=above)

    def test_fixed_percent_rejects_negative_rate(self) -> None:
        """A negative rate would be a contribution, not a withdrawal."""
        below = Rate(Decimal("-0.01"))
        with pytest.raises(ValueError, match="between 0 and 1"):
            FixedPercentWithdrawalStrategy(rate=below)
