"""The projection engine (roadmap 4.1; planning §4.6, §5.2).

``run(plan, assumptions, region, config)`` is a pure function: no I/O,
no clock reads (``config.today`` is an input), no global state
(planning §4.6). The same step function runs under the deterministic
and Monte Carlo modes; only the return model — resolved from
``config.mode``, or injected — differs (planning §5.2). Within each
period the operation order is part of the spec (planning §5.2,
tested):

1. **Open** — resolve ages, stage, glide-path allocation (§4.1 gate
   convention: retirement is attained only if reached by the period's
   first day).
2. **Income** — employment income while accumulating, escalated by the
   earnings-growth assumption; DB pension income (revalued in
   deferment and increased in payment per the scheme basis, early/late
   factors and commutation applied at start — roadmap 4.2); state
   pension income (region entitlement, uprated per the
   ``policy.state_pension.uprating`` assumption with protected
   payments and deferral increments uprating by CPI only — roadmap
   4.3); and purchased annuity income (priced at purchase from the
   annuity-rate assumptions, escalated per its product type — roadmap
   5.5). Entitlements begin at their exact start dates and are
   pro-rated by whole months within their starting period (§4.1).
3. **Contributions** — employee + employer per schedule, escalation,
   per-kind caps, then the region's relief mechanics.
4. **Withdrawals** — in decumulation, the household's net (after-tax)
   spending need is met from wrappers; net-defined draws gross up
   against the region tax system by fixed-point iteration (capped,
   residual settled at ledger precision).
5. **Tax** — one final assessment per person over the period's full
   categorised income; the gross-up called the same function, so the
   final assessment is consistent by construction.
6. **Fees** — platform + fund on average balances.
7. **Growth** — the period's returns on each wrapper's allocation
   (fees before growth is enforced by ``apply_fees_and_growth``).
8. **Close** — quantize the ledger, emit the period snapshot.

v1 engine conventions, superseded as later phases land:

- Accumulation and decumulation switch together at the target
  retirement age (§4.1 convention): employment income and
  contributions run while years-to-retirement is positive; spending
  withdrawals start once it is not.
- Withdrawals follow the configured strategy (roadmap 5.1): the
  strategy plans net-defined or gross-defined draws over the drawable
  sub-balances and the engine executes the plan, enforcing the
  region's access gates. The default fixed-real strategy meets the
  net need in the tax-aware order of planning §5.2 — taxable-growth
  accounts (GIA/cash) first, then tax-free wrappers, then funds
  already in drawdown (no fresh tax-free cash), then new pension
  access — with every uncrystallised pot, tax-free kinds included,
  subject to the region's access gate. In decumulation the net-of-tax
  DB/state-pension income (and any commutation lump sum) received in
  the period offsets the net spending need before wrappers are drawn;
  income and gross draws beyond the need bank into the person's first
  uncapped taxable wrapper (GIA/cash, roadmap 9.2) and are spent only
  when they hold none.
- Planned outflows (roadmap 5.4) land whole in the period containing
  the date their person attains the stated age — inside the run
  window only — inflated from today's money by the period-start
  price level. They join the period's net need: in decumulation the
  configured strategy funds them after the income offset; before
  decumulation they are funded net-defined in the default tax-aware
  order, since the strategy is a decumulation decision. The income
  offset applies in both phases: retirement income already in payment
  before the target retirement age (an early DB start, a purchased
  annuity, the state pension alongside work) meets the period's
  outflows net of the marginal tax it adds on top of employment
  income, and the remainder banks per roadmap 9.2. Employment income
  itself never offsets or banks — net pay funds working-life
  spending, which the model does not track.
- DB revaluation for the span before ``today`` — which the run never
  models period-by-period — compounds the scheme basis over the whole
  months from the statement date at the assumed CPI (planning §5.1);
  within the run it advances with each period's CPI. A DB start date
  before ``today`` means benefits are already in payment: income flows
  from the run start and the commutation lump sum is treated as
  already spent or banked in the user's stated balances.
- Tax-free cash (roadmap 5.2): pension draws resolve per the run's
  ``TaxFreeCashStrategy`` — split payments (the default), tax-free
  cash first with the residue designated to drawdown, or the whole-pot
  up-front crystallisation event whose lump sum joins the income
  offset. Tax-free elements are capped by the region's lump-sum
  allowance, tracked cumulatively from the ``lsa_used`` fact; an
  in-run DB commutation lump sum consumes the same headroom in the
  income step (ahead of wrapper draws) with its excess taxed as
  income; the first taxable pension draw records the MPAA trigger
  date unless the ``mpaa_triggered_on`` fact already set it.
  Crystallised funds never yield fresh tax-free cash (planning §5.1).
- Annuity purchases (roadmap 5.5) fire in the period containing the
  date their person attains the chosen age — inside the run window
  only; a purchase age already attained is an engine error, since a
  past purchase cannot be priced from a modelled pot. The chosen
  fraction of every pension wrapper's sub-balances — the pot at the
  period's open, before that period's contributions — converts into
  income at the assumption-priced rate (base single-life-at-65 rate,
  per-age multiplier, joint factor). Uncrystallised funds crystallise
  on the way: the region's tax-free fraction is paid out (capped at
  the remaining lump-sum-allowance headroom, the excess buying more
  annuity) and joins the income offset; crystallised funds annuitise
  whole. A lifetime annuity purchase never marks flexible access.
  When an up-front crystallisation event lands in the same period,
  the purchase — a step-2 income event — resolves first.
- Natural-yield pricing (roadmap 5.3): only for a withdrawal strategy
  declaring ``uses_natural_yield`` does the engine price each drawable
  source's period income from the per-asset ``yield.*`` assumptions,
  so those keys enter the run's provenance exactly when a strategy
  spends portfolio income.
- Wrapper balance facts are dated by their statement (planning §4.8):
  each sub-balance rolls forward from its ``as_of`` to ``today`` over
  whole months at the wrapper's expected nominal return net of its
  fee drag — the deterministic composition, whatever the run mode,
  with fees before growth as in every modelled period — with every
  non-zero adjustment reported in the run's provenance; a balance
  dated after ``today`` is an engine error (the DB statement-date
  convention). Annual-allowance measurement joins the loop when the
  AA charge is modelled — the in-run MPAA trigger is recorded now,
  and the region's assessment and carry-forward machinery (roadmap
  9.5) is ready for it.
- Partial first and last periods (roadmap 4.6, planning §5.2): the run
  models only the window from ``config.today`` through the horizon end.
  A period partly outside that window has its flows (employment income,
  contributions, spending need) pro-rated by whole months per §4.1, and
  its annual growth and fee rates scaled linearly by the same fraction —
  exact ``Decimal`` arithmetic, so §4.6 reproducibility holds. The
  cumulative CPI and escalation factors likewise advance between
  periods by the completed period's fraction, so later price and
  earnings levels reflect the time actually modelled. Annual
  caps, allowances, and tax bands stay whole-year: the months already
  elapsed live in the balance facts, not the model, so the partial
  year's pro-rated income meets full-year bands (accepted cost, §5.2).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from glidepath.core.annuities import (
    AnnuityRateTable,
    AnnuityType,
    annuity_base_rate_key,
    annuity_start_date,
)
from glidepath.core.config import EngineError, RunConfig, RunMode
from glidepath.core.contributions import MemberContributionRequest
from glidepath.core.entities import validate_household_v1
from glidepath.core.glide import glide_path_from_shape, years_to_target_retirement
from glidepath.core.investments import FeeSchedule, period_fee
from glidepath.core.money import Money, Rate
from glidepath.core.pensions import (
    db_early_late_factor,
    db_service_end_date,
    db_start_date,
    revaluation_factor_for_months,
)
from glidepath.core.periods import (
    age_on,
    date_age_attained,
    entitlement_active_fraction,
    period_active_fraction,
    service_active_fraction,
    whole_months_between,
)
from glidepath.core.provenance import (
    AssumptionKey,
    AssumptionReadRecorder,
    TrackedAssumptions,
    decimal_assumption_value,
    int_assumption_value,
    mapping_assumption_value,
)
from glidepath.core.results import (
    BalanceRollForward,
    PeriodSnapshot,
    PersonPeriodResult,
    ProjectionResult,
    RunProvenance,
    WrapperPeriodResult,
    collect_plan_decisions,
    collect_plan_facts,
)
from glidepath.core.returns import (
    DeterministicReturnModel,
    StochasticReturnModel,
    nominal_rate,
)
from glidepath.core.state_pension import StatePensionUprating
from glidepath.core.tax import TaxInput, TaxResult
from glidepath.core.withdrawals import (
    FixedRealWithdrawalStrategy,
    NetWithdrawalPlan,
    TaxFreeCashStrategy,
    WithdrawalSource,
    WithdrawalSourceId,
    WithdrawalState,
)
from glidepath.core.wrappers import (
    ContributionTaxTreatment,
    GrowthTaxTreatment,
    WithdrawalTaxTreatment,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import date

    from glidepath.core.annuities import AnnuityPurchase
    from glidepath.core.contributions import ContributionSchedule
    from glidepath.core.entities import Household, Person, SpendingPlan
    from glidepath.core.glide import GlidePathConfig, LifeStage
    from glidepath.core.investments import AssetAllocation
    from glidepath.core.pensions import DBPension, RevaluationBasis
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionSet, Fact
    from glidepath.core.region import Region
    from glidepath.core.returns import PeriodReturns, ReturnModel, ReturnModelFactory
    from glidepath.core.state_pension import StatePensionEntitlement
    from glidepath.core.withdrawals import GrossWithdrawalPlan, WithdrawalStrategy
    from glidepath.core.wrappers import ContributionCap, Wrapper, WrapperTaxTreatment

_ZERO = Money(Decimal(0))
_ONE = Decimal(1)
_MINUS_ONE = Decimal(-1)
_MONTHS_PER_YEAR = Decimal(12)
_GROSS_UP_ITERATION_CAP = 48
"""Fixed-point iteration cap for the net-need gross-up (§5.2 step 4)."""
_NET_TOLERANCE = Money(Decimal("0.005"))
"""Half a penny: residuals below ledger precision are settled, not chased."""
_NO_FEES = FeeSchedule(platform=Rate(Decimal(0)), fund=Rate(Decimal(0)))
"""The schedule of a kind the region exempts from the default fees."""
_OUTFLOW_FUNDING = FixedRealWithdrawalStrategy()
"""Funds planned outflows falling before decumulation (roadmap 5.4).

The configured strategy is a *decumulation* decision; an outflow due
while still accumulating is simply a net cash need, met in the default
tax-aware order.
"""


@dataclass(slots=True)
class _WrapperLedger:
    """One wrapper's mutable working ledger for a single period.

    ``uncrystallised``/``crystallised`` are the running balances as the
    period's flows apply; the ``opening_*`` fields keep the step-1
    values for the snapshot and the average-balance fee base.
    """

    wrapper: Wrapper
    allocation: AssetAllocation
    treatment: WrapperTaxTreatment
    uncrystallised: Money
    crystallised: Money
    opening_uncrystallised: Money
    opening_crystallised: Money
    employee_in: Money = _ZERO
    employer_in: Money = _ZERO
    provider_relief: Money = _ZERO
    bonus_in: Money = _ZERO
    contribution_shortfall: Money = _ZERO
    withdrawn_uncrystallised: Money = _ZERO
    withdrawn_crystallised: Money = _ZERO
    withdrawal_tax_free: Money = _ZERO
    withdrawal_taxable: Money = _ZERO
    annuity_purchase: Money = _ZERO
    taxable_interest: Money = _ZERO
    taxable_dividends: Money = _ZERO
    growth_tax: Money = _ZERO
    growth_tax_unfunded: Money = _ZERO
    banked_in: Money = _ZERO


@dataclass(slots=True)
class _WithdrawalSource:
    """One drawable sub-balance, with the tax-free fraction of a draw.

    ``access_open`` follows the §4.1 gate convention; a crystallised
    sub-balance is always open (already accessed, never re-gated —
    planning §5.1). ``pension`` marks a sub-balance of a
    partially-tax-free (pension) wrapper kind: its tax-free cash
    consumes the region's lump-sum allowance and its taxable draws
    mark flexible access (roadmap 5.2).
    """

    ledger: _WrapperLedger
    crystallised: bool
    tax_free_fraction: Decimal
    access_open: bool = True
    pension: bool = False

    @property
    def source_id(self) -> WithdrawalSourceId:
        """The stable key withdrawal plans reference this source by."""
        return WithdrawalSourceId(
            wrapper_id=self.ledger.wrapper.id, crystallised=self.crystallised
        )

    def view(self, natural_yield: Money = _ZERO) -> WithdrawalSource:
        """The frozen strategy-facing view of this sub-balance.

        ``natural_yield`` is the period income the engine priced for a
        yield-aware strategy (roadmap 5.3); other strategies see zero.
        """
        return WithdrawalSource(
            id=self.source_id,
            kind=self.ledger.wrapper.kind,
            available=self.available,
            tax_free_fraction=self.tax_free_fraction,
            access_open=self.access_open,
            natural_yield=natural_yield,
            growth_taxable=(self.ledger.treatment.growth is GrowthTaxTreatment.TAXABLE),
        )

    @property
    def available(self) -> Money:
        """What the sub-balance currently holds."""
        if self.crystallised:
            return self.ledger.crystallised
        return self.ledger.uncrystallised


@dataclass(frozen=True, slots=True)
class _DrawTranche:
    """One linear slice of a draw on a sub-balance (roadmap 5.2).

    Of every pound of ``gross``, ``free_share`` arrives as tax-free
    cash and ``taxable_share`` as taxable income; the remainder — the
    crystallised residue of a lump-sum-as-needed designation — moves
    into the wrapper's crystallised sub-balance and stays invested.
    ``from_crystallised`` names the sub-balance the gross leaves (a
    phased draw takes its income leg from the residue it just
    designated). Within a tranche the shares are constant, so the
    net-need fixed point of planning §5.2 step 4 converges exactly as
    it does on a whole source; tranche caps are computed lazily at
    draw time, so lump-sum-allowance headroom consumed by an earlier
    draw is never double-counted.
    """

    free_share: Decimal
    taxable_share: Decimal
    max_gross: Money
    from_crystallised: bool


@dataclass(frozen=True, slots=True)
class _PeriodIncome:
    """One person's step-2 income amounts for one period (§5.2).

    The gross entitlements the income step priced — employment,
    DB/state-pension/annuity income in payment, and the period's
    commutation and annuity-purchase lump sums — feeding the income
    offset of steps 3-4 and the period result.
    """

    employment: Money
    db_income: Money
    db_lump_sum: Money
    annuity_income: Money
    annuity_lump_sum: Money
    state_pension: Money


class _NominalFactors:
    """Cumulative nominal escalation factors, one per assumption key.

    Each registered key holds a *real* growth-rate assumption; after
    each completed period its factor advances by the annual nominal
    rate ``(1 + real)(1 + CPI) - 1`` scaled linearly by that period's
    active fraction (planning §5.2, roadmap 4.6), so escalated amounts
    stay nominal and a partial first period advances the level only by
    the months actually modelled — never a whole year.
    """

    __slots__ = ("_factors", "_real_rates")

    def __init__(self, tracked: TrackedAssumptions, keys: set[AssumptionKey]) -> None:
        """Read each key's real rate through the tracked view.

        Keys are read in sorted order so the run's recorded read order
        — and therefore the serialized provenance — is identical across
        processes (set iteration order is hash-salted; planning §4.6
        demands byte-identical results from identical inputs).
        """
        self._real_rates = {
            key: decimal_assumption_value(tracked.get(key)) for key in sorted(keys)
        }
        self._factors = dict.fromkeys(self._real_rates, _ONE)

    def advance(self, cpi: Decimal, fraction: Decimal) -> None:
        """Compound every factor by one completed period's nominal growth."""
        for key, real in self._real_rates.items():
            annual = (_ONE + real) * (_ONE + cpi) - _ONE
            self._factors[key] *= _ONE + annual * fraction

    def factor(self, key: AssumptionKey) -> Decimal:
        """The cumulative nominal factor for ``key`` (1 in period one)."""
        return self._factors[key]


@dataclass(slots=True)
class _DbAccrual:
    """Active CARE-style accrual on a DB stream (roadmap 9.6).

    ``rate`` and ``salary`` are the scheme's accrual rate and stated
    annual pensionable salary; the salary escalates with the
    earnings-growth assumption at credit time like employment income.
    ``service_end`` is the exclusive date service stops (leave-and-defer
    age, else the benefits start); the retirement gate may stop accrual
    earlier (planning §5.1).
    """

    rate: Decimal
    salary: Money
    service_end: date


@dataclass(slots=True)
class _DbStream:
    """One DB pension's income stream through the run (roadmap 4.2, 9.6).

    ``accrued_annual`` is the entitlement revalued to the period open —
    statement date to ``today`` folded in at the assumed CPI pre-run —
    *before* the early/late factor and commutation; ``advance`` carries
    the within-run revaluation forward per period, both in deferment
    and in payment (the single-basis convention, planning §5.1), and an
    active stream's credits join it at each period's open. The payout
    and lump-sum factors apply the early/late factor and the
    commutation split when income or the one-shot lump sum is read.
    """

    basis: RevaluationBasis
    start: date
    accrued_annual: Money
    payout_factor: Decimal
    lump_sum_factor: Decimal
    accrual: _DbAccrual | None = None

    def advance(self, cpi: Decimal, fraction: Decimal) -> None:
        """Compound one completed period's revaluation (§5.2 linear scaling)."""
        self.accrued_annual = self.accrued_annual * (
            _ONE + self.basis.annual_rate(cpi) * fraction
        )

    def credit(self, amount: Money) -> None:
        """Join one period's accrual at the period open (planning §5.1)."""
        self.accrued_annual = self.accrued_annual + amount

    def income_annual(self) -> Money:
        """The annual pension in payment, factors applied."""
        return self.accrued_annual * self.payout_factor

    def lump_sum(self) -> Money:
        """The commutation lump sum as of the benefits start."""
        return self.accrued_annual * self.lump_sum_factor


@dataclass(slots=True)
class _StatePensionStream:
    """The person's state pension income stream (roadmap 4.3).

    The entitlement's slices uprate separately (planning §5.1, §6):
    the main amount by the ``policy.state_pension.uprating`` rule, the
    protected payment by CPI only. State pension rates step by a full
    year's uprating at each period boundary (upratings take effect
    whole each April, which is exactly a UK period boundary), so —
    unlike the continuous price/earnings levels — the advance is never
    scaled by a partial period's active fraction (planning §5.1), and
    the CPI-only step is floored at zero (statutory uprating never
    cuts a rate).

    The deferral ``increment`` is captured in the first paying period —
    the uplift fraction applied to the rate then payable, upratings
    earned through deferment included — and uprates by CPI only from
    that point on (planning §5.1, §6).
    """

    entitlement: StatePensionEntitlement
    uprating: StatePensionUprating
    policy_factor: Decimal = _ONE
    cpi_factor: Decimal = _ONE
    increment: Money | None = None

    def advance(self, cpi: Decimal) -> None:
        """Step one period boundary's uprating, whole (class docstring)."""
        self.policy_factor *= _ONE + self.uprating.annual_rate(cpi)
        cpi_step = _ONE + max(cpi, Decimal(0))
        self.cpi_factor *= cpi_step
        if self.increment is not None:
            self.increment = self.increment * cpi_step

    def annual_amount(self) -> Money:
        """The paying period's full annual state pension, uprated to date."""
        base = (
            self.entitlement.annual_amount * self.policy_factor
            + self.entitlement.cpi_uprated_annual_amount * self.cpi_factor
        )
        if self.increment is None:
            self.increment = base * self.entitlement.deferral_uplift
        return base + self.increment


@dataclass(slots=True)
class _AnnuityStream:
    """One purchased annuity's income stream (roadmap 5.5).

    ``base_annual`` is the income the purchase bought — capital times
    the priced rate, nominal at the purchase date. ``factor`` carries
    the product's escalation forward per completed period under the
    §5.2 linear scaling convention: nothing for a level annuity, the
    table's fixed rate for an escalating one, the run's CPI for an
    inflation-linked one (tracking the index exactly — annuity
    contracts, unlike statutory upratings, are not floored at zero;
    planning §5.1). A stream created mid-run first advances at the
    period after its purchase, so its first period pays the purchased
    rate pro-rated from the exact start date.

    Escalation accrues from that exact start date, not the period
    boundary: ``purchase_period_share`` is the entitlement's share of
    the purchase period (the same share its first income pro-rates
    by), and the first boundary advance consumes it in place of the
    whole period's fraction — a mid-period purchase must not collect a
    full period of escalation (§4.1 linear whole-month convention).
    """

    annuity_type: AnnuityType
    start: date
    base_annual: Money
    escalation: Rate
    purchase_period_share: Decimal | None = None
    factor: Decimal = _ONE

    def advance(self, cpi: Decimal, fraction: Decimal) -> None:
        """Compound one completed period's escalation (§5.2 linear scaling)."""
        share = fraction
        if self.purchase_period_share is not None:
            share = self.purchase_period_share
            self.purchase_period_share = None
        if self.annuity_type is AnnuityType.ESCALATING:
            rate = self.escalation.value
        elif self.annuity_type is AnnuityType.INFLATION_LINKED:
            rate = cpi
        else:
            return
        self.factor *= _ONE + rate * share


def run(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    *,
    return_model_factory: ReturnModelFactory | None = None,
) -> ProjectionResult:
    """Project ``plan`` over the horizon (planning §5.2).

    Pure and deterministic (planning §4.6): identical inputs produce an
    identical result — under ``RunMode.MONTE_CARLO`` the randomness is
    exactly determined by ``config.seed`` and ``config.path``, so any
    single path is individually re-runnable. Every tunable number is
    read through the assumption set and recorded; the result's
    provenance lists the facts used, assumptions read, decisions in
    effect, the region data version, and the seed.

    The same step function runs under every mode; only the return
    model differs (planning §5.2). ``config.mode`` selects it —
    deterministic expected returns, or seeded stochastic draws where
    ``config.path`` names the substream (roadmap 7.3) — unless
    ``return_model_factory`` injects one built from the run's tracked
    assumption view (a scripted sequence fixture, roadmap 7.4).

    Raises:
        EngineError: If the horizon is empty, the plan is not
            projectable (v1: exactly one person), or a ``MONTE_CARLO``
            config carries no seed.
    """
    try:
        validate_household_v1(plan)
    except ValueError as exc:
        raise EngineError(str(exc)) from exc
    recorder = AssumptionReadRecorder()
    tracked = TrackedAssumptions(assumptions=assumptions, recorder=recorder)
    projection = _Projection(
        plan=plan,
        person=plan.persons[0],
        region=region,
        tracked=tracked,
        config=config,
        model=_return_model(config, tracked, return_model_factory),
    )
    snapshots = projection.execute()
    provenance = RunProvenance(
        facts=collect_plan_facts(plan),
        decisions=collect_plan_decisions(plan),
        assumptions=tuple(assumptions.get(key) for key in recorder.keys_read),
        region_data_version=region.data_version,
        seed=config.seed,
        balance_roll_forwards=projection.roll_forwards,
    )
    return ProjectionResult(snapshots=snapshots, provenance=provenance, config=config)


def _return_model(
    config: RunConfig,
    tracked: TrackedAssumptions,
    factory: ReturnModelFactory | None,
) -> ReturnModel:
    """The run's return model — injected, or resolved from the mode.

    The seed requirement binds before any injection: a ``MONTE_CARLO``
    config labels its result a Monte Carlo run, and an unseeded one
    could never be reproduced from its manifest, so it is rejected
    rather than defaulted — whether or not a factory stands in for the
    stochastic model (planning §4.6).

    Raises:
        EngineError: If a ``MONTE_CARLO`` config carries no seed.
    """
    if config.mode is RunMode.MONTE_CARLO:
        if config.seed is None:
            msg = "RunMode.MONTE_CARLO requires RunConfig.seed (planning §4.6)"
            raise EngineError(msg)
        if factory is not None:
            return factory(tracked)
        return StochasticReturnModel(assumptions=tracked, seed=config.seed)
    if factory is not None:
        return factory(tracked)
    return DeterministicReturnModel(assumptions=tracked)


@dataclass(slots=True)
class _Projection:
    """One run's working state: the loop of planning §5.2 over the horizon."""

    plan: Household
    person: Person
    region: Region
    tracked: TrackedAssumptions
    config: RunConfig
    model: ReturnModel
    _balances: dict[str, tuple[Money, Money]] = field(default_factory=dict)
    _taxable_income: Money = _ZERO
    _savings_income: Money = _ZERO
    _dividend_income: Money = _ZERO
    _relief_at_source: Money = _ZERO
    _db_streams: list[_DbStream] = field(default_factory=list)
    _sp_stream: _StatePensionStream | None = None
    _annuity_streams: list[_AnnuityStream] = field(default_factory=list)
    _annuity_table: AnnuityRateTable | None = None
    _lsa_used: Money = _ZERO
    _mpaa_triggered_on: date | None = None
    _roll_forwards: list[BalanceRollForward] = field(default_factory=list)

    @property
    def roll_forwards(self) -> tuple[BalanceRollForward, ...]:
        """The §4.8 balance adjustments recorded while seeding the ledger."""
        return tuple(self._roll_forwards)

    def execute(self) -> tuple[PeriodSnapshot, ...]:
        """Run the period loop and return the snapshots in order."""
        if self.person.lsa_used is not None:
            self._lsa_used = self.person.lsa_used.value
        if self.person.mpaa_triggered_on is not None:
            self._mpaa_triggered_on = self.person.mpaa_triggered_on.value
        self._balances = self._opening_balances()
        factors = _NominalFactors(self.tracked, self._escalation_keys())
        self._build_income_streams()
        inflation = _ONE
        snapshots: list[PeriodSnapshot] = []
        horizon_end = self._horizon_end()
        previous_cpi: Decimal | None = None
        previous_fraction = _ONE
        for period in self.region.calendar.periods(self.config.today, horizon_end):
            fraction = period_active_fraction(period, self.config.today, horizon_end)
            returns = self.model.returns_for(period, self.config.path)
            if previous_cpi is not None:
                # Advance the price/earnings levels by the growth of the
                # period just completed, scaled by its active fraction
                # (§5.2, roadmap 4.6): a partial first period must not
                # fast-forward a whole year of escalation.
                inflation *= _ONE + previous_cpi * previous_fraction
                factors.advance(previous_cpi, previous_fraction)
                for stream in self._db_streams:
                    stream.advance(previous_cpi, previous_fraction)
                for annuity in self._annuity_streams:
                    annuity.advance(previous_cpi, previous_fraction)
                if self._sp_stream is not None:
                    self._sp_stream.advance(previous_cpi)
            snapshots.append(
                self._project_period(period, returns, inflation, factors, fraction)
            )
            previous_cpi = returns.cpi.value
            previous_fraction = fraction
        return tuple(snapshots)

    def _opening_balances(self) -> dict[str, tuple[Money, Money]]:
        """Seed each wrapper's opening sub-balances at ``today`` (§4.8).

        Every balance fact rolls forward from its statement ``as_of``
        over whole months at the wrapper's expected nominal return net
        of its fee drag, and each non-zero adjustment is recorded for
        the run's provenance — an estimate layered on the stated fact
        is never applied silently (planning §4.8).
        """
        balances: dict[str, tuple[Money, Money]] = {}
        for wrapper in self.person.wrappers:
            prefix = f"wrapper[{wrapper.id}]"
            uncrystallised = self._rolled_balance(
                wrapper, wrapper.balance, f"{prefix}.balance"
            )
            crystallised = _ZERO
            if wrapper.crystallised_balance is not None:
                crystallised = self._rolled_balance(
                    wrapper,
                    wrapper.crystallised_balance,
                    f"{prefix}.crystallised_balance",
                )
            balances[wrapper.id] = (uncrystallised, crystallised)
        return balances

    def _rolled_balance(self, wrapper: Wrapper, fact: Fact[Money], label: str) -> Money:
        """One balance fact rolled forward from ``as_of`` to today (§4.8).

        The whole-month convention makes a balance stated within one
        month of ``today`` an exact no-op — no adjustment, no record.
        The annual rate is the expected nominal return net of the
        wrapper's fee drag — ``(1 + nominal)(1 - fees) - 1``, fees
        before growth exactly as every modelled period charges them
        (§5.2 step 6; issue #111) — and the factor compounds like the
        DB statement-date convention: integer-exponent whole years,
        linear remainder months, exact ``Decimal`` arithmetic
        (planning §4.6). A fee-adjusted expected return of -100% per
        year or worse is rejected — the same positive-gross invariant
        the stochastic model enforces on its expectation — so the
        factor is always strictly positive.

        Raises:
            EngineError: If the fact is dated after ``today``, or the
                wrapper's fee-adjusted expected nominal gross return
                is not positive.
        """
        today = self.config.today
        if fact.as_of > today:
            msg = (
                f"{label}: balance as_of {fact.as_of} is after today"
                f" {today} (planning §4.8)"
            )
            raise EngineError(msg)
        months = whole_months_between(fact.as_of, today)
        if months == 0:
            return fact.value
        nominal = self._expected_nominal_rate(self._opening_allocation(wrapper))
        kept_after_fees = _ONE - self._fees_for(wrapper).total_rate.value
        rate = (_ONE + nominal) * kept_after_fees - _ONE
        if rate <= _MINUS_ONE:
            msg = (
                f"{label}: the wrapper's fee-adjusted expected nominal return"
                f" ({rate} per year) is -100% or worse; the roll-forward"
                " needs a positive expected gross return (planning §4.8)"
            )
            raise EngineError(msg)
        factor = revaluation_factor_for_months(rate, months)
        opening = (fact.value * factor).quantized()
        self._roll_forwards.append(
            BalanceRollForward(
                label=label,
                stated=fact.value,
                as_of=fact.as_of,
                months=months,
                factor=factor,
                opening=opening,
            )
        )
        return opening

    def _expected_nominal_rate(self, allocation: AssetAllocation) -> Decimal:
        """The allocation-weighted expected nominal annual return (§4.8).

        The deterministic composition of each class's real-return
        assumption with CPI — the expectation both return models are
        built from — whatever the run mode: the pre-``today`` span is
        never path-modelled, exactly as CPI stays deterministic across
        Monte Carlo paths (planning §4.8).
        """
        cpi = decimal_assumption_value(self.tracked.get(AssumptionKey.INFLATION_CPI))
        weighted = Decimal(0)
        for weight, key in (
            (allocation.equity, AssumptionKey.RETURNS_EQUITY_REAL),
            (allocation.bonds, AssumptionKey.RETURNS_BONDS_REAL),
            (allocation.cash, AssumptionKey.RETURNS_CASH_REAL),
        ):
            real = decimal_assumption_value(self.tracked.get(key))
            weighted += weight * nominal_rate(real, cpi).value
        return weighted

    def _opening_allocation(self, wrapper: Wrapper) -> AssetAllocation:
        """The allocation the wrapper opens the first period with (§4.8).

        The wrapper's own stated split, else the glide path at the
        run-start years-to-retirement — the same resolution step 1 of
        the first period applies, standing in for the whole pre-run
        span (an accepted §4.8 cost).
        """
        if wrapper.allocation is not None:
            return wrapper.allocation
        first_period = next(
            iter(self.region.calendar.periods(self.config.today, self._horizon_end()))
        )
        ytr = years_to_target_retirement(
            self.person.date_of_birth.value,
            self.person.target_retirement_age.value,
            first_period,
        )
        return self._glide().allocation_at(ytr)

    def _build_income_streams(self) -> None:
        """Resolve the person's DB and state pension income streams.

        DB amounts are revalued from the statement date to ``today``
        over whole months at the assumed CPI (the run never models
        time before ``today``; module docstring), with the early/late
        factor and the commutation split applied. The state pension
        entitlement comes from the region's scheme; its uprating rule
        is read (and recorded) only when a record is present.

        Annuity purchases create their streams mid-run — pricing needs
        the pot as it stands at the purchase date — so only their
        dates are validated here: a purchase age already attained
        cannot be priced from a modelled pot and is rejected rather
        than guessed (roadmap 5.5); an annuity already in payment
        belongs in the plan's stated income, not the model.

        Raises:
            EngineError: If a DB statement date lies in the future, or
                an annuity purchase age was attained before ``today``.
        """
        person = self.person
        today = self.config.today
        for purchase in person.annuity_purchases:
            if annuity_start_date(purchase, person.date_of_birth.value) < today:
                msg = (
                    f"annuity purchase {purchase.id}: age"
                    f" {purchase.at_age.value} was attained before today"
                    f" {today}, so the purchase cannot be priced from the"
                    " modelled pot (roadmap 5.5)"
                )
                raise EngineError(msg)
        cpi = decimal_assumption_value(self.tracked.get(AssumptionKey.INFLATION_CPI))
        for pension in person.db_pensions:
            if pension.statement_date > today:
                msg = (
                    f"DB pension {pension.id}: statement date"
                    f" {pension.statement_date} is after today {today}"
                )
                raise EngineError(msg)
            self._db_streams.append(self._db_stream(pension, cpi))
        if person.state_pension is None:
            return
        entitlement = self.region.state_pension.entitlement(
            person.state_pension, person.date_of_birth.value
        )
        if (
            entitlement.annual_amount <= _ZERO
            and entitlement.cpi_uprated_annual_amount <= _ZERO
        ):
            return
        uprating = StatePensionUprating.from_assumption_value(
            self.tracked.get(AssumptionKey.POLICY_STATE_PENSION_UPRATING).value
        )
        self._sp_stream = _StatePensionStream(
            entitlement=entitlement, uprating=uprating
        )

    def _db_stream(self, pension: DBPension, cpi: Decimal) -> _DbStream:
        """Seed one DB pension's stream at ``today`` (planning §5.1).

        The accrued entitlement revalues from the statement date to
        ``today`` (whole-month convention, §4.6). An active membership
        additionally credits the statement→today span's service at the
        stated salary, un-revalued (§5.1), clamped at the earliest of
        the service end and the exact target-retirement date — the
        pre-run counterpart of the in-run retirement gate; service
        still to run carries the accrual parameters into the period
        loop. Service already over just leaves the pension deferred —
        tolerant, like a benefits start already past.
        """
        today = self.config.today
        person = self.person
        months = whole_months_between(pension.statement_date, today)
        annual_rate = pension.revaluation_basis.annual_rate(cpi)
        accrued = pension.accrued_annual_pension.value * revaluation_factor_for_months(
            annual_rate, months
        )
        accrual = None
        membership = pension.active_membership
        if membership is not None:
            retirement = date_age_attained(
                person.date_of_birth.value, person.target_retirement_age.value
            )
            service_end = db_service_end_date(pension, person.date_of_birth.value)
            span_end = min(today, service_end, retirement)
            if span_end > pension.statement_date:
                service_months = whole_months_between(pension.statement_date, span_end)
                accrued = accrued + membership.pensionable_salary.value * (
                    membership.accrual_rate.value
                    * Decimal(service_months)
                    / _MONTHS_PER_YEAR
                )
            if service_end > today:
                accrual = _DbAccrual(
                    rate=membership.accrual_rate.value,
                    salary=membership.pensionable_salary.value,
                    service_end=service_end,
                )
        factor = db_early_late_factor(pension)
        commuted = pension.commuted_fraction.value
        lump_sum_factor = Decimal(0)
        if commuted > Decimal(0) and pension.commutation_factor is not None:
            lump_sum_factor = factor * commuted * pension.commutation_factor.value
        return _DbStream(
            basis=pension.revaluation_basis,
            start=db_start_date(pension, person.date_of_birth.value),
            accrued_annual=accrued,
            payout_factor=factor * (_ONE - commuted),
            lump_sum_factor=lump_sum_factor,
            accrual=accrual,
        )

    def _horizon_end(self) -> date:
        """The configured horizon end, or the planning-age default (§5.2)."""
        if self.config.horizon_end is not None:
            return self.config.horizon_end
        planning_age = int_assumption_value(
            self.tracked.get(AssumptionKey.HORIZON_PLANNING_AGE)
        )
        horizon_end = date_age_attained(self.person.date_of_birth.value, planning_age)
        if horizon_end < self.config.today:
            msg = (
                f"planning age {planning_age} was attained before today"
                f" {self.config.today}; set RunConfig.horizon_end explicitly"
            )
            raise EngineError(msg)
        return horizon_end

    def _escalation_keys(self) -> set[AssumptionKey]:
        """The real-growth assumption keys this plan escalates by."""
        keys: set[AssumptionKey] = set()
        if self.person.employment_income is not None or any(
            pension.active_membership is not None for pension in self.person.db_pensions
        ):
            keys.add(AssumptionKey.EARNINGS_GROWTH_REAL)
        keys.update(
            wrapper.contributions.escalation
            for wrapper in self.person.wrappers
            if wrapper.contributions is not None
            and wrapper.contributions.escalation is not None
        )
        return keys

    def _glide(self) -> GlidePathConfig:
        """The person's glide path, or the default-shape assumption's."""
        if self.person.glide_path is not None:
            return self.person.glide_path
        shape = mapping_assumption_value(
            self.tracked.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE)
        )
        return glide_path_from_shape(shape)

    def _fees_for(self, wrapper: Wrapper) -> FeeSchedule:
        """The wrapper's fee schedule, or the shipped fee assumptions.

        A kind the region exempts from the default fees — a bare cash
        savings account, whose rate already is the whole deal — pays
        nothing unless the wrapper states its own schedule; the fee
        assumption keys are then never read, so they stay out of the
        run's provenance (issue #118).
        """
        if wrapper.fees is not None:
            return wrapper.fees
        if not self.region.wrappers.bears_default_fees(wrapper.kind):
            return _NO_FEES
        return FeeSchedule(
            platform=Rate(
                decimal_assumption_value(self.tracked.get(AssumptionKey.FEES_PLATFORM))
            ),
            fund=Rate(
                decimal_assumption_value(self.tracked.get(AssumptionKey.FEES_FUND))
            ),
        )

    def _project_period(
        self,
        period: Period,
        returns: PeriodReturns,
        inflation: Decimal,
        factors: _NominalFactors,
        fraction: Decimal,
    ) -> PeriodSnapshot:
        """Run the eight steps of planning §5.2 for one period.

        ``fraction`` is the whole-month share of the period inside the
        run window (roadmap 4.6): flows and the annual growth/fee rates
        are scaled by it, so a mid-period ``today`` never re-models
        months already reflected in the balance facts, and the final
        period never models time past the horizon end. Income
        entitlements pro-rate by their own start dates within the same
        window (§4.1).
        """
        person = self.person
        # Step 1 — open.
        age = age_on(person.date_of_birth.value, period.start)
        ytr = years_to_target_retirement(
            person.date_of_birth.value, person.target_retirement_age.value, period
        )
        glide = self._glide()
        stage = glide.stage_at(ytr)
        retired = ytr <= 0
        ledgers = [
            self._open_ledger(wrapper, period, glide, ytr)
            for wrapper in person.wrappers
        ]
        # Step 2 — income. Active DB accrual credits at the period open,
        # gated by retirement like employment income (planning §5.1).
        if not retired:
            self._accrue_db_step(period, factors)
        employment = _ZERO
        if not retired and person.employment_income is not None:
            employment = (
                person.employment_income.value
                * factors.factor(AssumptionKey.EARNINGS_GROWTH_REAL)
                * fraction
            )
        db_income, db_lump_sum = self._db_amounts(period)
        db_lump_sum_excess = self._consume_lump_sum_headroom(db_lump_sum, period)
        annuity_lump_sum = self._annuity_purchase_step(ledgers, period)
        annuity_income = self._annuity_amount(period)
        state_pension = self._state_pension_amount(period)
        self._accrue_portfolio_income(ledgers, fraction)
        # Steps 3-4 — contributions, then withdrawals.
        self._taxable_income = (
            employment + db_income + state_pension + db_lump_sum_excess + annuity_income
        )
        self._relief_at_source = _ZERO
        if not retired:
            self._contribution_step(ledgers, period, employment, factors, fraction)
        need = _ZERO
        outflows = self._outflows_due(period, inflation)
        pension_lump_sum = _ZERO
        income = _PeriodIncome(
            employment=employment,
            db_income=db_income,
            db_lump_sum=db_lump_sum,
            annuity_income=annuity_income,
            annuity_lump_sum=annuity_lump_sum,
            state_pension=state_pension,
        )
        if retired:
            if self.plan.spending is not None:
                need = _spending_need(self.plan.spending, stage, inflation) * fraction
            if self.config.tax_free_cash is TaxFreeCashStrategy.UP_FRONT_LUMP_SUM:
                pension_lump_sum = self._up_front_lump_sums(ledgers, period)
            income_net = self._decumulation_income_net(period, income, pension_lump_sum)
            wrapper_need = max(need + outflows - income_net, _ZERO)
            delivered, spend_target = self._withdrawal_step(
                ledgers,
                period,
                wrapper_need,
                fraction,
                self.config.withdrawal_strategy,
            )
            # Income and gross draws beyond the need bank into the
            # first uncapped taxable wrapper when one exists (roadmap
            # 9.2); with none they are spent, the pre-9.2 behaviour.
            # A net-defined strategy's adjusted target (a guardrails
            # prosperity rise) is spending, never swept back: only
            # delivery beyond that target is surplus.
            surplus = max(income_net - need - outflows, _ZERO) + max(
                delivered - spend_target, _ZERO
            )
            banked = self._bank_surplus(ledgers, surplus)
        else:
            wrapper_need, delivered, banked = self._accumulation_spending(
                ledgers, period, income, outflows, fraction
            )
        # Step 5 — final tax assessment on the full income picture,
        # then the portfolio-income slice charged to its wrappers.
        tax = self.region.tax.assess(period, self._tax_input())
        self._charge_portfolio_tax(ledgers, period, tax)
        # Steps 6-8 — fees, growth, close.
        wrapper_results = tuple(
            self._close_wrapper(ledger, returns, fraction) for ledger in ledgers
        )
        # Portfolio-income tax a drained wrapper could not fund is
        # unmet need: it joins the shortfall so the ledger reconciles
        # and the roadmap-7.3 ruin signal sees it (planning §5.2).
        unfunded_tax = _ZERO
        for ledger in ledgers:
            unfunded_tax = unfunded_tax + ledger.growth_tax_unfunded
        shortfall = max(wrapper_need - delivered, _ZERO) + unfunded_tax
        person_result = PersonPeriodResult(
            person_id=person.id,
            age_at_period_start=age,
            years_to_retirement=ytr,
            stage=stage,
            employment_income=employment.quantized(),
            tax=tax,
            spending_need=need.quantized(),
            net_withdrawn=delivered.quantized(),
            shortfall=shortfall.quantized(),
            wrappers=wrapper_results,
            db_income=db_income.quantized(),
            db_lump_sum=db_lump_sum.quantized(),
            state_pension_income=state_pension.quantized(),
            annuity_income=annuity_income.quantized(),
            annuity_lump_sum=annuity_lump_sum.quantized(),
            planned_outflows=outflows.quantized(),
            pension_lump_sum=pension_lump_sum.quantized(),
            lsa_used=self._lsa_used.quantized(),
            mpaa_triggered_on=self._mpaa_triggered_on,
            banked=banked.quantized(),
        )
        return PeriodSnapshot(
            period=period,
            returns=returns,
            inflation_factor=inflation,
            persons=(person_result,),
            year_fraction=fraction,
        )

    def _accrue_db_step(self, period: Period, factors: _NominalFactors) -> None:
        """Credit each active DB stream's accrual for ``period`` (§5.1).

        The credit is ``accrual rate x escalated pensionable salary``
        scaled by the whole months of service inside the period and the
        run window; it joins the entitlement at the period open and
        revalues with it from this period on. The retirement gate is
        the caller's (the §5.2 period-open convention shared with
        employment income).
        """
        today = self.config.today
        horizon_end = self._horizon_end()
        for stream in self._db_streams:
            accrual = stream.accrual
            if accrual is None:
                continue
            share = service_active_fraction(
                accrual.service_end, period, today, horizon_end
            )
            if share <= Decimal(0):
                continue
            salary = accrual.salary * factors.factor(AssumptionKey.EARNINGS_GROWTH_REAL)
            stream.credit(salary * (accrual.rate * share))

    def _db_amounts(self, period: Period) -> tuple[Money, Money]:
        """Step 2: DB income in payment plus any commutation lump sum.

        Income pro-rates from each pension's exact start date within
        the run window (§4.1). The lump sum lands once, in the period
        containing the start date — and only when that date is inside
        the window: an already-taken lump sum lives in the user's
        stated balances, not the model (module docstring).
        """
        income = _ZERO
        lump_sum = _ZERO
        today = self.config.today
        horizon_end = self._horizon_end()
        for stream in self._db_streams:
            share = entitlement_active_fraction(
                stream.start, period, today, horizon_end
            )
            if share > Decimal(0):
                income = income + stream.income_annual() * share
            if period.contains(stream.start) and today <= stream.start <= horizon_end:
                lump_sum = lump_sum + stream.lump_sum()
        return income, lump_sum

    def _state_pension_amount(self, period: Period) -> Money:
        """Step 2: state pension income in payment for ``period``.

        The uprated annual amount (both slices) pro-rated from the
        entitlement's exact start date — state pension age plus any
        deferral — within the run window (§4.1).
        """
        stream = self._sp_stream
        if stream is None:
            return _ZERO
        share = entitlement_active_fraction(
            stream.entitlement.start_date,
            period,
            self.config.today,
            self._horizon_end(),
        )
        if share <= Decimal(0):
            return _ZERO
        return stream.annual_amount() * share

    def _annuity_purchase_step(
        self, ledgers: list[_WrapperLedger], period: Period
    ) -> Money:
        """Step 2: execute annuity purchases due this period (roadmap 5.5).

        Each due purchase converts its fraction of every pension
        wrapper's sub-balances — the pot as it stands at the period's
        open, before this period's contributions — into a lifetime
        income stream priced from the annuity-rate assumptions.
        Crystallised funds annuitise whole; uncrystallised funds
        crystallise on the way, delivering the region's tax-free
        fraction as cash — capped at the remaining lump-sum-allowance
        headroom, the remainder simply buying more annuity — exactly
        the §5.2 tax-free cash conventions. Buying a lifetime annuity
        is not flexible access, so no MPAA trigger is recorded
        (planning §5.1). Returns the tax-free cash delivered, which
        joins the period's income offset like a commutation lump sum.

        Raises:
            EngineError: If a purchase must crystallise a pot whose
                access gate has not opened (§4.1), or its age lies
                outside the shipped rate table.
        """
        total_lump_sum = _ZERO
        horizon_end = self._horizon_end()
        for purchase in self.person.annuity_purchases:
            due = annuity_start_date(purchase, self.person.date_of_birth.value)
            if period.contains(due) and due <= horizon_end:
                total_lump_sum = total_lump_sum + self._execute_annuity_purchase(
                    purchase, ledgers, period, due
                )
        return total_lump_sum

    def _execute_annuity_purchase(
        self,
        purchase: AnnuityPurchase,
        ledgers: list[_WrapperLedger],
        period: Period,
        due: date,
    ) -> Money:
        """Execute one due purchase; return its tax-free cash (§5.2).

        Annuitises the purchase's fraction of each pension wrapper and,
        when any capital was converted, opens the income stream at the
        priced rate. A zero-pot purchase converts nothing and opens no
        stream — a depleted pot is a legitimate simulation outcome.
        """
        rate = self._annuity_rate_for(purchase.annuity_type, purchase)
        capital = _ZERO
        lump_sum = _ZERO
        for ledger in ledgers:
            partial = (
                ledger.treatment.withdrawals
                is WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
            )
            if not partial:
                continue
            annuitised, tax_free = self._annuitise_wrapper(purchase, ledger, period)
            capital = capital + annuitised
            lump_sum = lump_sum + tax_free
        if capital > _ZERO:
            self._annuity_streams.append(
                _AnnuityStream(
                    annuity_type=purchase.annuity_type,
                    start=due,
                    base_annual=capital * rate,
                    escalation=self._annuity_pricing_table().escalation,
                    purchase_period_share=entitlement_active_fraction(
                        due, period, self.config.today, self._horizon_end()
                    ),
                )
            )
        return lump_sum

    def _annuitise_wrapper(
        self, purchase: AnnuityPurchase, ledger: _WrapperLedger, period: Period
    ) -> tuple[Money, Money]:
        """Annuitise one pension wrapper's share of a purchase.

        Draws the purchase fraction of both sub-balances, pays the
        uncrystallised draw's tax-free element (headroom-capped, §5.2)
        through the wrapper like an up-front lump sum, and returns the
        capital annuitised alongside the tax-free cash delivered.

        Raises:
            EngineError: If the draw must crystallise a pot whose
                access gate has not opened (§4.1).
        """
        fraction = purchase.fraction_of_pot.value
        crystallised_draw = ledger.crystallised * fraction
        uncrystallised_draw = ledger.uncrystallised * fraction
        gate_open = self.region.wrappers.is_access_open(
            ledger.wrapper.kind, self.person.date_of_birth.value, period
        )
        if uncrystallised_draw > _ZERO and not gate_open:
            msg = (
                f"annuity purchase {purchase.id} crystallises wrapper"
                f" {ledger.wrapper.id} before its access gate opens"
            )
            raise EngineError(msg)
        tax_free = _ZERO
        free_fraction = ledger.treatment.tax_free_fraction
        if uncrystallised_draw > _ZERO and free_fraction is not None:
            tax_free = uncrystallised_draw * free_fraction.value
            headroom = self._lsa_headroom(period)
            if headroom is not None:
                tax_free = min(tax_free, headroom)
            self._lsa_used = self._lsa_used + tax_free
        ledger.uncrystallised = ledger.uncrystallised - uncrystallised_draw
        ledger.crystallised = ledger.crystallised - crystallised_draw
        ledger.withdrawn_uncrystallised = ledger.withdrawn_uncrystallised + tax_free
        ledger.withdrawal_tax_free = ledger.withdrawal_tax_free + tax_free
        annuitised = crystallised_draw + uncrystallised_draw - tax_free
        ledger.annuity_purchase = ledger.annuity_purchase + annuitised
        return annuitised, tax_free

    def _annuity_rate_for(
        self, annuity_type: AnnuityType, purchase: AnnuityPurchase
    ) -> Decimal:
        """The annual income per pound of purchase capital (planning §7).

        The single-life-at-65 base rate for the type, shaped by the
        ``annuity.age_adjustment`` table's per-age multiplier and — on
        a joint basis — its joint-life factor. Read through the
        tracked view only when a purchase actually fires, so the rate
        assumptions enter provenance exactly when they enter the
        result.
        """
        table = self._annuity_pricing_table()
        base = decimal_assumption_value(
            self.tracked.get(annuity_base_rate_key(annuity_type))
        )
        multiplier = table.age_multiplier(annuity_type, purchase.at_age.value)
        return base * multiplier * table.basis_factor(purchase.basis)

    def _annuity_pricing_table(self) -> AnnuityRateTable:
        """The parsed age-adjustment table, read once per run on first use."""
        if self._annuity_table is None:
            self._annuity_table = AnnuityRateTable.from_assumption_value(
                mapping_assumption_value(
                    self.tracked.get(AssumptionKey.ANNUITY_AGE_ADJUSTMENT)
                )
            )
        return self._annuity_table

    def _annuity_amount(self, period: Period) -> Money:
        """Step 2: purchased annuity income in payment for ``period``.

        Each stream's bought income, escalated per its type, pro-rated
        from its exact start date within the run window (§4.1). Wholly
        taxable: annuities bought with pension funds pay taxable
        income (planning §5.1).
        """
        total = _ZERO
        horizon_end = self._horizon_end()
        for stream in self._annuity_streams:
            share = entitlement_active_fraction(
                stream.start, period, self.config.today, horizon_end
            )
            if share > Decimal(0):
                total = total + stream.base_annual * (stream.factor * share)
        return total

    def _outflows_due(self, period: Period, inflation: Decimal) -> Money:
        """The planned outflows landing in ``period``, in nominal money.

        A planned outflow is a dated one-off (roadmap 5.4): it hits the
        period containing the date its person attains the stated age —
        whole, never pro-rated, the DB lump-sum convention — and only
        when that date lies inside the run window; an outflow already
        past lives in the stated balances, not the model. The real
        amount is inflated by the period-start price level, the same
        single inflation truth the spending need uses (§5.2).
        """
        total = _ZERO
        if not self.plan.planned_outflows:
            return total
        horizon_end = self._horizon_end()
        births = {person.id: person.date_of_birth.value for person in self.plan.persons}
        for outflow in self.plan.planned_outflows:
            person_id, age = outflow.at_age_of
            due = date_age_attained(births[person_id], age)
            if period.contains(due) and self.config.today <= due <= horizon_end:
                total = total + outflow.amount_real.value * inflation
        return total

    def _open_ledger(
        self, wrapper: Wrapper, period: Period, glide: GlidePathConfig, ytr: int
    ) -> _WrapperLedger:
        """Step 1 for one wrapper: allocation and opening balances.

        A crystallised balance is meaningful only on a partially
        tax-free (pension) kind — funds already designated to drawdown
        (planning §5.1). Any other kind carrying one is an engine
        error: crystallised sub-balances are never re-gated, so
        accepting one on an age-gated kind (a LISA) would let money
        bypass its access gate.
        """
        uncrystallised, crystallised = self._balances[wrapper.id]
        treatment = self.region.wrappers.tax_treatment(wrapper.kind, period)
        if (
            crystallised > _ZERO
            and treatment.withdrawals is not WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
        ):
            msg = (
                f"wrapper {wrapper.id}: kind {wrapper.kind!r} does not take a"
                " crystallised balance — only partially-tax-free (pension)"
                " kinds hold funds designated to drawdown"
            )
            raise EngineError(msg)
        allocation = (
            wrapper.allocation
            if wrapper.allocation is not None
            else glide.allocation_at(ytr)
        )
        return _WrapperLedger(
            wrapper=wrapper,
            allocation=allocation,
            treatment=treatment,
            uncrystallised=uncrystallised,
            crystallised=crystallised,
            opening_uncrystallised=uncrystallised,
            opening_crystallised=crystallised,
        )

    def _contribution_step(
        self,
        ledgers: list[_WrapperLedger],
        period: Period,
        employment: Money,
        factors: _NominalFactors,
        fraction: Decimal,
    ) -> None:
        """Step 3: scheduled contributions through caps and relief rules.

        The region's contribution terms govern each kind (roadmap
        9.2): scheduled amounts scale by the whole-month share of the
        period inside the intersection of the run window and the
        terms' contribution window (a LISA's stops at 50) — one
        overlap, never the product of separate fractions, so a run
        starting after the window closes contributes nothing; caps
        are annual allowances shared
        across wrappers through their allowance groups (LISA inside
        the overall ISA allowance), with employer amounts (employment
        terms, outside the member's control) consuming headroom first;
        a bonus rate (the LISA's 25%) credits the pot on top of the
        member's contribution without consuming any cap. Amounts a cap
        or the region's relief limit keeps out of the pot are recorded
        as the wrapper's contribution shortfall — never rerouted: a
        schedule states intent for one wrapper (§5.1). Caps stay
        whole-year — allowances are annual, and contributions already
        made this year live in the balance facts, not the model.
        """
        used_by_group: dict[str, Money] = {}
        relieved_so_far = _ZERO
        for ledger in ledgers:
            schedule = ledger.wrapper.contributions
            if schedule is None:
                continue
            self._require_permitted_mechanic(ledger.wrapper, schedule)
            terms = self.region.wrappers.contribution_terms(
                ledger.wrapper.kind, self.person.date_of_birth.value, period
            )
            escalation = _ONE
            if schedule.escalation is not None:
                escalation = factors.factor(schedule.escalation)
            flow_fraction = fraction
            if terms.window is not None:
                flow_fraction = self._contribution_window_fraction(terms.window, period)
            scale = escalation * flow_fraction
            employee_intended = schedule.employee_amount.value * scale
            employer = _ZERO
            if schedule.employer_amount is not None:
                employer = schedule.employer_amount.value * scale
            employee, employer = _apply_contribution_caps(
                terms.caps,
                used_by_group,
                employee=employee_intended,
                employer=employer,
            )
            outcome = self.region.contributions.member_contribution(
                MemberContributionRequest(
                    gross=employee,
                    relevant_earnings=employment,
                    date_of_birth=self.person.date_of_birth.value,
                    mechanic=schedule.relief_mechanic,
                    already_relieved_gross=relieved_so_far,
                ),
                period,
            )
            if schedule.relief_mechanic is not None:
                relieved_so_far = relieved_so_far + outcome.gross_to_pot
            self._taxable_income = max(
                self._taxable_income - outcome.taxable_pay_deduction, _ZERO
            )
            self._relief_at_source = (
                self._relief_at_source + outcome.assessment_relief_gross
            )
            bonus = _ZERO
            if terms.bonus_rate is not None:
                bonus = terms.bonus_rate.of(outcome.gross_to_pot)
            ledger.employee_in = outcome.gross_to_pot
            ledger.employer_in = employer
            ledger.provider_relief = outcome.provider_relief
            ledger.bonus_in = bonus
            ledger.contribution_shortfall = (
                employee_intended - employee
            ) + outcome.unrelieved_excess
            ledger.uncrystallised = (
                ledger.uncrystallised + outcome.gross_to_pot + employer + bonus
            )

    def _contribution_window_fraction(self, window: Period, period: Period) -> Decimal:
        """The period's whole-month share inside run ∩ eligibility window.

        The run models only ``[today, horizon_end]`` (roadmap 4.6) and
        the region's contribution window is an exact date span (§4.1),
        so the contributable share of the period is the whole months
        of the *single* three-way overlap over the whole months of the
        period — never the product of separately measured fractions,
        which overstates disjoint windows (a run starting the day the
        window closes must contribute zero).
        """
        start = max(self.config.today, window.start)
        end = min(self._horizon_end(), window.end)
        if end < start:
            return Decimal(0)
        return period_active_fraction(period, start, end)

    def _require_permitted_mechanic(
        self, wrapper: Wrapper, schedule: ContributionSchedule
    ) -> None:
        """Reject a schedule whose relief mechanic the region forbids.

        The region's permitted-mechanics set is the authority (planning
        §4.2): a mechanic outside it would fabricate relief (e.g.
        relief at source into an ISA), and a missing mechanic on a
        kind that operates one would bypass the relief limits entirely.
        """
        permitted = self.region.wrappers.permitted_relief_mechanics(wrapper.kind)
        mechanic = schedule.relief_mechanic
        if mechanic is None and permitted:
            names = ", ".join(sorted(entry.name for entry in permitted))
            msg = (
                f"wrapper {wrapper.id}: contributions to kind {wrapper.kind!r}"
                f" require a relief mechanic (one of: {names})"
            )
            raise EngineError(msg)
        if mechanic is not None and mechanic not in permitted:
            msg = (
                f"wrapper {wrapper.id}: relief mechanic {mechanic.name} is not"
                f" permitted for kind {wrapper.kind!r}"
            )
            raise EngineError(msg)

    def _decumulation_income_net(
        self, period: Period, income: _PeriodIncome, pension_lump_sum: Money
    ) -> Money:
        """The §5.2 decumulation income offset: net cash before draws.

        Net-of-tax pension, state-pension and annuity income, any
        commutation lump sum (gross here; the tax on its over-headroom
        excess is in the assessment), and any up-front or
        annuity-purchase tax-free cash meet the net need — spending
        plus planned outflows — first; only the remainder is drawn
        from wrappers. The offset excludes the portfolio-income
        layers: their tax is charged to the taxable wrappers at
        close, never to the need.
        """
        income_tax = self.region.tax.assess(
            period, self._tax_input(include_portfolio=False)
        ).tax_due
        return (
            income.db_income
            + income.state_pension
            + income.annuity_income
            + income.db_lump_sum
            + income.annuity_lump_sum
            + pension_lump_sum
            - income_tax
        )

    def _accumulation_spending(
        self,
        ledgers: list[_WrapperLedger],
        period: Period,
        income: _PeriodIncome,
        outflows: Money,
        fraction: Decimal,
    ) -> tuple[Money, Money, Money]:
        """Steps 3-4 before retirement: fund outflows, bank the rest.

        Retirement income already in payment before the target
        retirement age — an early DB start or annuity purchase, the
        state pension alongside work — is real cash: net of the
        marginal tax it adds on top of employment income
        (:meth:`_pre_retirement_income_tax`) it meets the period's
        planned outflows first, and the remainder banks like
        decumulation surplus (roadmap 9.2). Employment income itself
        never offsets or banks — net pay funds working-life spending,
        which the model does not track (planning §5.2). An outflow
        beyond the offset is a net cash need met in the default
        tax-aware order; the configured strategy governs decumulation
        only (module docstring). Returns the wrapper need, the net
        cash delivered toward it, and the surplus banked.
        """
        income_net = (
            income.db_income
            + income.state_pension
            + income.annuity_income
            + income.db_lump_sum
            + income.annuity_lump_sum
            - self._pre_retirement_income_tax(period, income.employment)
        )
        wrapper_need = max(outflows - income_net, _ZERO)
        delivered = _ZERO
        spend_target = wrapper_need
        if wrapper_need > _ZERO:
            delivered, spend_target = self._withdrawal_step(
                ledgers, period, wrapper_need, fraction, _OUTFLOW_FUNDING
            )
        surplus = max(income_net - outflows, _ZERO) + max(
            delivered - spend_target, _ZERO
        )
        return wrapper_need, delivered, self._bank_surplus(ledgers, surplus)

    def _withdrawal_step(
        self,
        ledgers: list[_WrapperLedger],
        period: Period,
        need: Money,
        fraction: Decimal,
        strategy: WithdrawalStrategy,
    ) -> tuple[Money, Money]:
        """Step 4: run the withdrawal strategy over the drawable sources.

        The strategy sees every sub-balance — gate-closed ones flagged
        (planning §5.2) — and returns a net-defined or gross-defined
        plan; execution enforces the access gates, so a plan drawing on
        a closed source is an error, never a silent draw. For a
        strategy declaring ``uses_natural_yield`` (roadmap 5.3), each
        source also carries the income its balance throws off — the
        wrapper allocation's weighted ``yield.*`` assumptions, scaled
        by the period's active fraction; the yield keys are read only
        then, so other runs' provenance never lists them. Returns the
        net cash delivered and the plan's net spending target — the
        strategy-adjusted need for a net-defined plan (a guardrails
        rise or cut), the caller's need for a gross-defined one, which
        sets no net target. Delivery toward the target is spending;
        only delivery beyond it is surplus for the caller to bank
        (roadmap 9.2).
        """
        sources = self._withdrawal_sources(ledgers, period)
        # Opt-in marker, deliberately not a protocol member: a strategy
        # that never declares it simply gets no yield pricing (§5.2).
        price_yield = getattr(strategy, "uses_natural_yield", False)
        views: list[WithdrawalSource] = []
        for source in sources.values():
            natural_yield = _ZERO
            if price_yield and source.available > _ZERO:
                natural_yield = source.available * (
                    self._portfolio_yield(source.ledger.allocation) * fraction
                )
            views.append(source.view(natural_yield=natural_yield))
        state = WithdrawalState(
            sources=tuple(views),
            year_fraction=fraction,
            tax_free_cash_headroom=self._lsa_headroom(period),
        )
        plan = strategy.withdraw(state, need)
        if isinstance(plan, NetWithdrawalPlan):
            return self._execute_net_plan(sources, period, plan), plan.target
        return self._execute_gross_plan(sources, period, plan), need

    def _portfolio_yield(self, allocation: AssetAllocation) -> Decimal:
        """The allocation-weighted annual natural yield (roadmap 5.3).

        The income an invested balance throws off per year — dividends,
        coupons, interest — priced from the per-asset ``yield.*``
        assumptions (planning §7); nominal, like the balances it
        applies to.
        """
        return (
            allocation.equity
            * decimal_assumption_value(self.tracked.get(AssumptionKey.YIELD_EQUITY))
            + allocation.bonds
            * decimal_assumption_value(self.tracked.get(AssumptionKey.YIELD_BONDS))
            + allocation.cash
            * decimal_assumption_value(self.tracked.get(AssumptionKey.YIELD_CASH))
        )

    def _withdrawal_sources(
        self, ledgers: list[_WrapperLedger], period: Period
    ) -> dict[WithdrawalSourceId, _WithdrawalSource]:
        """Every sub-balance keyed for plan execution, in wrapper order.

        On every kind — wholly tax-free ones included, since tax
        treatment says nothing about accessibility — the uncrystallised
        pot answers to the region's access gate (§4.1); crystallised
        funds are always drawable (already accessed, never re-gated —
        planning §5.1).
        """
        sources: dict[WithdrawalSourceId, _WithdrawalSource] = {}
        for ledger in ledgers:
            treatment = ledger.treatment
            pension = treatment.withdrawals is WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
            if treatment.withdrawals is WithdrawalTaxTreatment.TAX_FREE:
                free_fraction = _ONE
                crystallised_fraction = _ONE
            else:
                free_fraction = Decimal(0)
                crystallised_fraction = Decimal(0)
                if pension and treatment.tax_free_fraction is not None:
                    free_fraction = treatment.tax_free_fraction.value
            entries = (
                _WithdrawalSource(
                    ledger=ledger,
                    crystallised=False,
                    tax_free_fraction=free_fraction,
                    access_open=self.region.wrappers.is_access_open(
                        ledger.wrapper.kind,
                        self.person.date_of_birth.value,
                        period,
                    ),
                    pension=pension,
                ),
                _WithdrawalSource(
                    ledger=ledger,
                    crystallised=True,
                    tax_free_fraction=crystallised_fraction,
                    pension=pension,
                ),
            )
            for source in entries:
                sources[source.source_id] = source
        return sources

    def _execute_net_plan(
        self,
        sources: dict[WithdrawalSourceId, _WithdrawalSource],
        period: Period,
        plan: NetWithdrawalPlan,
    ) -> Money:
        """Deliver the plan's net target, grossing up source by source.

        Walks the plan's order, drawing from each source until the
        target is met to within ledger tolerance or the listed sources
        are exhausted; the unmet remainder is the caller's shortfall.
        """
        delivered = _ZERO
        for source_id in plan.order:
            remaining = plan.target - delivered
            if remaining <= _NET_TOLERANCE:
                break
            source = _plan_source(sources, source_id)
            if source.available <= _ZERO:
                continue
            delivered = delivered + self._draw_from(source, period, remaining)
        return delivered

    def _execute_gross_plan(
        self,
        sources: dict[WithdrawalSourceId, _WithdrawalSource],
        period: Period,
        plan: GrossWithdrawalPlan,
    ) -> Money:
        """Take the plan's exact gross draws; net is what survives tax.

        No fixed-point iteration (planning §5.2): a gross-defined
        strategy declares itself gross. Each draw is capped at what its
        source holds and resolved as a split payment whatever the
        run's tax-free cash mode — an exact gross amount is a payment
        instruction, not a designation (planning §5.2). The marginal
        tax on the taxable share is priced through the same regional
        assessment the final step-5 pass uses, so the two cannot
        disagree.
        """
        delivered = _ZERO
        for draw in plan.draws:
            source = _plan_source(sources, draw.source)
            remaining = min(draw.amount, source.available)
            for tranche in self._tranches(
                source, period, TaxFreeCashStrategy.SPLIT_EACH_PAYMENT
            ):
                if remaining <= _ZERO:
                    break
                gross = min(remaining, tranche.max_gross)
                if gross <= _ZERO:
                    continue
                delivered = delivered + self._apply_tranche(
                    source, period, tranche, gross
                )
                remaining = remaining - gross
        return delivered

    def _draw_from(
        self, source: _WithdrawalSource, period: Period, need: Money
    ) -> Money:
        """Draw up to ``need`` net from one source, grossing up for tax.

        The source resolves into linear tranches per the run's
        tax-free cash mode (roadmap 5.2) — for a plain source exactly
        one — each drawn through the fixed point of
        :meth:`_draw_tranche` until the need is met to within ledger
        tolerance or the tranches are exhausted.
        """
        mode = self.config.tax_free_cash
        if mode is TaxFreeCashStrategy.UP_FRONT_LUMP_SUM:
            # The up-front mode is a decumulation crystallisation
            # event; any other draw it leaves to make (an outflow
            # funded before retirement) is a split payment (§5.2).
            mode = TaxFreeCashStrategy.SPLIT_EACH_PAYMENT
        delivered = _ZERO
        for tranche in self._tranches(source, period, mode):
            remaining = need - delivered
            if remaining <= _NET_TOLERANCE:
                break
            if tranche.max_gross <= _ZERO:
                continue
            delivered = delivered + self._draw_tranche(
                source, period, tranche, remaining
            )
        return delivered

    def _draw_tranche(
        self,
        source: _WithdrawalSource,
        period: Period,
        tranche: _DrawTranche,
        need: Money,
    ) -> Money:
        """Draw up to ``need`` net from one tranche, grossing up for tax.

        The fixed point of planning §5.2 step 4: iterate gross →
        assess → net until the net matches the need (piecewise-constant
        marginal rates converge in a few rounds), capped at
        ``_GROSS_UP_ITERATION_CAP`` with any sub-penny residual settled
        rather than chased. A draw the tranche cannot cover takes the
        tranche's whole cap instead. ``cash_share`` divides because a
        designating tranche delivers only its tax-free slice as cash:
        meeting one pound of need takes ``1 / cash_share`` pounds
        gross.
        """
        cash_share = tranche.free_share + tranche.taxable_share
        gross = min(Money(need.amount / cash_share), tranche.max_gross)
        for _ in range(_GROSS_UP_ITERATION_CAP):
            extra_tax = self._incremental_tax(period, gross * tranche.taxable_share)
            target = Money((need + extra_tax).amount / cash_share)
            if target >= tranche.max_gross:
                gross = tranche.max_gross
                break
            if (target - gross) < _NET_TOLERANCE and (gross - target) < _NET_TOLERANCE:
                break
            gross = target
        return self._apply_tranche(source, period, tranche, gross)

    def _apply_tranche(
        self,
        source: _WithdrawalSource,
        period: Period,
        tranche: _DrawTranche,
        gross: Money,
    ) -> Money:
        """Execute ``gross`` against one tranche; return the net cash.

        Splits the gross into tax-free cash, taxable income, and the
        designated residue (moved to the wrapper's crystallised
        sub-balance, not withdrawn), updates the ledger, consumes
        lump-sum-allowance headroom, and marks flexible access on a
        taxable pension draw (roadmap 5.2).
        """
        tax_free = gross * tranche.free_share
        taxable = gross * tranche.taxable_share
        designated = gross - tax_free - taxable
        net = tax_free + taxable - self._incremental_tax(period, taxable)
        ledger = source.ledger
        if tranche.from_crystallised:
            ledger.crystallised = ledger.crystallised - gross
            ledger.withdrawn_crystallised = ledger.withdrawn_crystallised + gross
        else:
            ledger.uncrystallised = ledger.uncrystallised - gross
            ledger.crystallised = ledger.crystallised + designated
            ledger.withdrawn_uncrystallised = (
                ledger.withdrawn_uncrystallised + gross - designated
            )
        ledger.withdrawal_tax_free = ledger.withdrawal_tax_free + tax_free
        ledger.withdrawal_taxable = ledger.withdrawal_taxable + taxable
        self._taxable_income = self._taxable_income + taxable
        if source.pension:
            self._lsa_used = self._lsa_used + tax_free
            if taxable > _ZERO:
                self._mark_flexible_access(period)
        return net

    def _tranches(
        self,
        source: _WithdrawalSource,
        period: Period,
        mode: TaxFreeCashStrategy,
    ) -> Iterator[_DrawTranche]:
        """The linear slices a draw on ``source`` resolves into (§5.2).

        A non-pension sub-balance and a crystallised pension pot are a
        single tranche — the latter wholly taxable, never fresh
        tax-free cash (planning §5.1). An uncrystallised pension pot
        splits at the lump-sum-allowance boundary: the free-bearing
        slice runs while headroom lasts, then a wholly taxable slice —
        payments beyond the allowance keep flowing, just without the
        tax-free element. Under the lump-sum-as-needed mode the
        free-bearing slice designates its residue instead of paying it
        out, and the taxable slices draw drawdown income — first from
        the residue this draw just designated (never from pre-existing
        drawdown funds, which answer only to their own crystallised
        source id), then by crystallising the rest of the pot
        outright. Caps are evaluated lazily as the caller resumes the
        iterator, so each slice sees the balances and headroom its
        predecessors left.
        """
        ledger = source.ledger
        if not source.pension or source.crystallised:
            yield _DrawTranche(
                free_share=source.tax_free_fraction,
                taxable_share=_ONE - source.tax_free_fraction,
                max_gross=source.available,
                from_crystallised=source.crystallised,
            )
            return
        fraction = source.tax_free_fraction
        headroom = self._lsa_headroom(period)
        free_cap = ledger.uncrystallised
        if headroom is not None:
            free_cap = min(Money(headroom.amount / fraction), free_cap)
        if mode is TaxFreeCashStrategy.LUMP_SUM_AS_NEEDED:
            crystallised_before = ledger.crystallised
            if free_cap > _ZERO:
                yield _DrawTranche(
                    free_share=fraction,
                    taxable_share=Decimal(0),
                    max_gross=free_cap,
                    from_crystallised=False,
                )
            residue = ledger.crystallised - crystallised_before
            if residue > _ZERO:
                yield _DrawTranche(
                    free_share=Decimal(0),
                    taxable_share=_ONE,
                    max_gross=residue,
                    from_crystallised=True,
                )
            if ledger.uncrystallised > _ZERO:
                yield _DrawTranche(
                    free_share=Decimal(0),
                    taxable_share=_ONE,
                    max_gross=ledger.uncrystallised,
                    from_crystallised=False,
                )
            return
        if free_cap > _ZERO:
            yield _DrawTranche(
                free_share=fraction,
                taxable_share=_ONE - fraction,
                max_gross=free_cap,
                from_crystallised=False,
            )
        if ledger.uncrystallised > _ZERO:
            yield _DrawTranche(
                free_share=Decimal(0),
                taxable_share=_ONE,
                max_gross=ledger.uncrystallised,
                from_crystallised=False,
            )

    def _up_front_lump_sums(
        self, ledgers: list[_WrapperLedger], period: Period
    ) -> Money:
        """The ``UP_FRONT_LUMP_SUM`` crystallisation event (§5.2).

        In a decumulation period, every uncrystallised pension pot
        whose access gate is open crystallises whole: the tax-free
        fraction — capped at the remaining lump-sum-allowance headroom
        — is paid out and joins the period's income offset; the rest
        moves to the crystallised sub-balance. Pure tax-free cash
        never marks flexible access (planning §5.2). Later periods
        find the pots already empty, so the event fires once per
        wrapper — or later, for a pot whose gate opens after
        retirement (the §4.1 NMPA schedule).
        """
        total = _ZERO
        for ledger in ledgers:
            treatment = ledger.treatment
            fraction = treatment.tax_free_fraction
            partial = treatment.withdrawals is WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
            if not partial or fraction is None or ledger.uncrystallised <= _ZERO:
                continue
            if not self.region.wrappers.is_access_open(
                ledger.wrapper.kind, self.person.date_of_birth.value, period
            ):
                continue
            pot = ledger.uncrystallised
            tax_free = pot * fraction.value
            headroom = self._lsa_headroom(period)
            if headroom is not None:
                tax_free = min(tax_free, headroom)
            ledger.uncrystallised = _ZERO
            ledger.crystallised = ledger.crystallised + pot - tax_free
            ledger.withdrawn_uncrystallised = ledger.withdrawn_uncrystallised + tax_free
            ledger.withdrawal_tax_free = ledger.withdrawal_tax_free + tax_free
            self._lsa_used = self._lsa_used + tax_free
            total = total + tax_free
        return total

    def _lsa_headroom(self, period: Period) -> Money | None:
        """Tax-free cash still allowed under the region's lifetime cap.

        ``None`` when the region has no cap. Usage accumulates across
        the run from the person's ``lsa_used`` fact (roadmap 5.2).
        """
        allowance = self.region.wrappers.lump_sum_allowance(period)
        if allowance is None:
            return None
        return max(allowance - self._lsa_used, _ZERO)

    def _consume_lump_sum_headroom(self, lump_sum: Money, period: Period) -> Money:
        """Count an income lump sum against the cap; return the excess.

        A DB commencement (commutation) lump sum consumes the same
        lifetime allowance as wrapper tax-free cash (planning §5.2):
        the part within the remaining headroom is tax-free and recorded
        as used; the excess is returned for the caller to tax as income
        (the UK's pension commencement excess lump sum). Landing in
        step 2, it consumes headroom ahead of the period's wrapper
        draws. A lump sum never marks flexible access.
        """
        if lump_sum <= _ZERO:
            return _ZERO
        headroom = self._lsa_headroom(period)
        tax_free = lump_sum if headroom is None else min(lump_sum, headroom)
        self._lsa_used = self._lsa_used + tax_free
        return lump_sum - tax_free

    def _mark_flexible_access(self, period: Period) -> None:
        """Record the first taxable pension draw as the MPAA trigger.

        The trigger date is the later of the period's first day and
        the run's ``today`` — the first modelled day of the period
        (§5.2). A pre-existing ``mpaa_triggered_on`` fact wins: once a
        date is set it never moves.
        """
        if self._mpaa_triggered_on is None:
            self._mpaa_triggered_on = max(period.start, self.config.today)

    def _pre_retirement_income_tax(self, period: Period, employment: Money) -> Money:
        """The tax the pre-retirement income offset bears (planning §5.2).

        Employment income keeps its own tax — net pay funds
        working-life spending outside the model — so the offset's
        DB/state-pension/annuity income (and any commutation excess)
        is netted of only the marginal tax those layers add on top of
        it: the no-portfolio assessment less one of employment income
        alone. The portfolio layers' interaction stays with the
        wrapper charge, exactly as in decumulation
        (:meth:`_incremental_tax`).
        """
        if self._taxable_income <= employment:
            return _ZERO
        full = self.region.tax.assess(period, self._tax_input(include_portfolio=False))
        base = self.region.tax.assess(
            period,
            self._tax_input(non_savings_override=employment, include_portfolio=False),
        )
        return full.tax_due - base.tax_due

    def _incremental_tax(self, period: Period, taxable: Money) -> Money:
        """The extra tax ``taxable`` adds on top of the period's income.

        Both calls go through the region's one ``assess`` function —
        the same one the final step-5 assessment uses — so the gross-up
        and the final tax picture cannot disagree (planning §5.2).

        Draw pricing deliberately excludes the portfolio-income layers
        (roadmap 9.2): a draw that shifts the taxpayer's band can raise
        the tax on savings/dividend income sitting above it (a PSA tier
        drop, dividends pushed up a rate), and that interaction belongs
        to the wrapper charge — :meth:`_charge_portfolio_tax` measures
        the portfolio layers' full cost against the final income
        picture, so pricing it into the gross-up as well would collect
        it twice. The decomposition is exact: the offset and gross-ups
        collect the no-portfolio assessment, the wrapper charge the
        remainder, and together they sum to the final full assessment.
        """
        if taxable <= _ZERO:
            return _ZERO
        base = self.region.tax.assess(period, self._tax_input(include_portfolio=False))
        with_draw = self.region.tax.assess(
            period, self._tax_input(extra_income=taxable, include_portfolio=False)
        )
        return with_draw.tax_due - base.tax_due

    def _tax_input(
        self,
        extra_income: Money = _ZERO,
        *,
        include_portfolio: bool = True,
        non_savings_override: Money | None = None,
    ) -> TaxInput:
        """The person's categorised income picture for assessment.

        ``include_portfolio=False`` drops the savings/dividend income
        of taxable-growth wrappers: the decumulation income offset and
        the growth-tax attribution both need the picture without those
        top-of-ladder layers, whose tax is charged to the wrappers
        (:meth:`_charge_portfolio_tax`), never to the spending need.
        ``non_savings_override`` replaces the accumulated non-savings
        income — the employment-only baseline of the pre-retirement
        income offset (:meth:`_pre_retirement_income_tax`).
        """
        non_savings = (
            self._taxable_income
            if non_savings_override is None
            else non_savings_override
        )
        return TaxInput(
            residency=self.person.tax_residency,
            non_savings_income=non_savings + extra_income,
            savings_income=self._savings_income if include_portfolio else _ZERO,
            dividend_income=self._dividend_income if include_portfolio else _ZERO,
            relief_at_source_contributions=self._relief_at_source,
        )

    def _accrue_portfolio_income(
        self, ledgers: list[_WrapperLedger], fraction: Decimal
    ) -> None:
        """Step 2 for taxable-growth wrappers: price the period's income.

        A bare account's holdings throw off dividends (the equity
        slice) and interest (the bond and cash slices), priced from
        the per-asset ``yield.*`` assumptions on the opening balance
        and scaled by the period's active fraction (roadmap 9.2). The
        income stays invested — the balance path is untouched — but it
        enters the person's categorised tax picture as the §6 savings
        and dividend layers, and the tax attributable is charged to
        the wrapper at close (:meth:`_charge_portfolio_tax`). The
        yield keys are read only when such a wrapper exists, so other
        runs' provenance never lists them.
        """
        self._savings_income = _ZERO
        self._dividend_income = _ZERO
        for ledger in ledgers:
            if ledger.treatment.growth is not GrowthTaxTreatment.TAXABLE:
                continue
            balance = ledger.opening_uncrystallised + ledger.opening_crystallised
            if balance <= _ZERO:
                continue
            allocation = ledger.allocation
            equity_yield = decimal_assumption_value(
                self.tracked.get(AssumptionKey.YIELD_EQUITY)
            )
            bond_yield = decimal_assumption_value(
                self.tracked.get(AssumptionKey.YIELD_BONDS)
            )
            cash_yield = decimal_assumption_value(
                self.tracked.get(AssumptionKey.YIELD_CASH)
            )
            dividends = balance * (allocation.equity * equity_yield * fraction)
            interest = balance * (
                (allocation.bonds * bond_yield + allocation.cash * cash_yield)
                * fraction
            )
            ledger.taxable_dividends = dividends
            ledger.taxable_interest = interest
            self._dividend_income = self._dividend_income + dividends
            self._savings_income = self._savings_income + interest

    def _charge_portfolio_tax(
        self, ledgers: list[_WrapperLedger], period: Period, tax: TaxResult
    ) -> None:
        """Attribute the savings/dividend layers' tax to their wrappers.

        The attributable tax is the final assessment less an
        assessment of the same picture without the portfolio income —
        the marginal cost of the top-of-ladder savings and dividend
        layers, personal-allowance-taper interactions included. It is
        apportioned across the taxable-growth wrappers pro rata to
        their income (remainder on the last) and deducted from each
        balance at close (:meth:`_close_wrapper`) — the real-world
        drag of paying tax out of taxable savings.
        """
        portfolio_income = self._savings_income + self._dividend_income
        if portfolio_income <= _ZERO:
            return
        base = self.region.tax.assess(period, self._tax_input(include_portfolio=False))
        total_tax = tax.tax_due - base.tax_due
        if total_tax <= _ZERO:
            return
        taxable = [
            ledger
            for ledger in ledgers
            if ledger.taxable_interest + ledger.taxable_dividends > _ZERO
        ]
        charged = _ZERO
        for ledger in taxable[:-1]:
            income = ledger.taxable_interest + ledger.taxable_dividends
            share = Money(total_tax.amount * income.amount / portfolio_income.amount)
            ledger.growth_tax = share
            charged = charged + share
        taxable[-1].growth_tax = total_tax - charged

    def _bank_surplus(self, ledgers: list[_WrapperLedger], surplus: Money) -> Money:
        """Sweep decumulation surplus into the first taxable wrapper.

        Income and gross draws beyond the period's need land in the
        first wrapper (plan order) whose treatment marks it a bare
        taxable account — paid from taxed income, growth taxable,
        withdrawals tax-free (a GIA or cash account) — rather than
        being spent (roadmap 9.2). Banking is not a contribution: no
        cap, relief, or bonus machinery applies. With no such wrapper
        the surplus is spent, the pre-9.2 behaviour (planning §5.2).
        """
        if surplus <= _ZERO:
            return _ZERO
        for ledger in ledgers:
            treatment = ledger.treatment
            if (
                treatment.contributions is ContributionTaxTreatment.FROM_TAXED_INCOME
                and treatment.growth is GrowthTaxTreatment.TAXABLE
                and treatment.withdrawals is WithdrawalTaxTreatment.TAX_FREE
            ):
                ledger.uncrystallised = ledger.uncrystallised + surplus
                ledger.banked_in = ledger.banked_in + surplus
                return surplus
        return _ZERO

    def _close_wrapper(
        self, ledger: _WrapperLedger, returns: PeriodReturns, fraction: Decimal
    ) -> WrapperPeriodResult:
        """Steps 6-8 for one wrapper: fees, growth, quantize, snapshot.

        The fee (step 6) is charged on the wrapper's aggregate average
        balance — a provider charges the account, not its sub-balances,
        and the cannot-exceed-the-holdings cap binds at account level —
        then allocated across the sub-balances pro rata to their
        post-flow values. Growth (step 7) applies to each post-fee
        sub-balance; fees before growth per the §5.2 order. In a
        partial first/last period both annual rates are scaled linearly
        by ``fraction`` (the §5.2 roadmap-4.6 convention). A
        taxable-growth wrapper's attributed portfolio-income tax
        (:meth:`_charge_portfolio_tax`) leaves the balance last —
        settled at the period's close like a real self-assessment
        payment — capped at what the account then holds.
        """
        fees = self._fees_for(ledger.wrapper)
        opening_total = ledger.opening_uncrystallised + ledger.opening_crystallised
        after_total = ledger.uncrystallised + ledger.crystallised
        fee_total = period_fee(opening_total, after_total, fees, fraction)
        fee_uncrystallised = _ZERO
        if after_total > _ZERO:
            fee_uncrystallised = Money(
                fee_total.amount * ledger.uncrystallised.amount / after_total.amount
            )
        fee_crystallised = fee_total - fee_uncrystallised
        growth_rate = (
            returns.assets.portfolio_growth_factor(ledger.allocation) - _ONE
        ) * fraction
        post_fee_uncrystallised = ledger.uncrystallised - fee_uncrystallised
        post_fee_crystallised = ledger.crystallised - fee_crystallised
        growth_uncrystallised = Money(post_fee_uncrystallised.amount * growth_rate)
        growth_crystallised = Money(post_fee_crystallised.amount * growth_rate)
        growth_tax = min(
            ledger.growth_tax,
            max(post_fee_uncrystallised + growth_uncrystallised, _ZERO),
        )
        # A drained account cannot fund its assessed portfolio-income
        # tax; the remainder joins the person's shortfall rather than
        # silently disappearing from the ledger (planning §5.2).
        ledger.growth_tax_unfunded = ledger.growth_tax - growth_tax
        closing_uncrystallised = (
            post_fee_uncrystallised + growth_uncrystallised - growth_tax
        ).quantized()
        closing_crystallised = (post_fee_crystallised + growth_crystallised).quantized()
        self._balances[ledger.wrapper.id] = (
            closing_uncrystallised,
            closing_crystallised,
        )
        return WrapperPeriodResult(
            wrapper_id=ledger.wrapper.id,
            kind=ledger.wrapper.kind,
            allocation=ledger.allocation,
            opening_uncrystallised=ledger.opening_uncrystallised.quantized(),
            opening_crystallised=ledger.opening_crystallised.quantized(),
            employee_contribution=ledger.employee_in.quantized(),
            employer_contribution=ledger.employer_in.quantized(),
            provider_relief=ledger.provider_relief.quantized(),
            contribution_shortfall=ledger.contribution_shortfall.quantized(),
            withdrawal_tax_free=ledger.withdrawal_tax_free.quantized(),
            withdrawal_taxable=ledger.withdrawal_taxable.quantized(),
            annuity_purchase=ledger.annuity_purchase.quantized(),
            fee=fee_total.quantized(),
            growth=(growth_uncrystallised + growth_crystallised).quantized(),
            closing_uncrystallised=closing_uncrystallised,
            closing_crystallised=closing_crystallised,
            contribution_bonus=ledger.bonus_in.quantized(),
            taxable_interest=ledger.taxable_interest.quantized(),
            taxable_dividends=ledger.taxable_dividends.quantized(),
            growth_tax=growth_tax.quantized(),
            banked_in=ledger.banked_in.quantized(),
        )


def _apply_contribution_caps(
    caps: tuple[ContributionCap, ...],
    used_by_group: dict[str, Money],
    *,
    employee: Money,
    employer: Money,
) -> tuple[Money, Money]:
    """Clip a contribution to its allowance groups' shared headroom.

    The binding headroom is the tightest of the caps' remaining
    budgets; employer amounts (employment terms, outside the member's
    control) consume it first and the employee amount fills what
    remains. What fits is recorded against every listed group, so a
    sub-capped kind (a LISA) consumes the overall allowance too
    (planning §5.2). Returns the clipped ``(employee, employer)``.
    """
    if not caps:
        return employee, employer
    headroom = min(
        max(cap.limit - used_by_group.get(cap.group, _ZERO), _ZERO) for cap in caps
    )
    employer = min(employer, headroom)
    employee = min(employee, max(headroom - employer, _ZERO))
    for cap in caps:
        used_by_group[cap.group] = (
            used_by_group.get(cap.group, _ZERO) + employer + employee
        )
    return employee, employer


def _plan_source(
    sources: dict[WithdrawalSourceId, _WithdrawalSource],
    source_id: WithdrawalSourceId,
) -> _WithdrawalSource:
    """Resolve a plan's source reference, enforcing the access gates.

    Raises:
        EngineError: If the plan references a source that does not
            exist or whose access gate has not opened (§4.1).
    """
    source = sources.get(source_id)
    if source is None:
        msg = (
            f"withdrawal plan references unknown source: wrapper"
            f" {source_id.wrapper_id} (crystallised={source_id.crystallised})"
        )
        raise EngineError(msg)
    if not source.access_open:
        msg = (
            f"withdrawal plan draws on wrapper {source_id.wrapper_id}"
            " before its access gate opens"
        )
        raise EngineError(msg)
    return source


def _spending_need(
    spending: SpendingPlan, stage: LifeStage, inflation: Decimal
) -> Money:
    """The period's net spending target in nominal money (§5.2 step 4).

    The real (today's money) need is scaled by the stage multiplier
    when one is configured, then inflated by the run's cumulative CPI
    factor — the same single inflation truth the returns carry.
    """
    multiplier = _ONE
    if spending.stage_multipliers is not None:
        multiplier = spending.stage_multipliers.get(stage, _ONE)
    return spending.annual_spending_real.value * multiplier * inflation
