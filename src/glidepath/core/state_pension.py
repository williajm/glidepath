"""State pension records and the region boundary (roadmap 4.3; planning §5.1).

The core holds the user's :class:`StatePensionRecord` — the official
DWP forecast is the fact and the only route to an amount (planning
§5.1); the model never re-derives what DWP has already computed — and
the :class:`StatePensionScheme` protocol (planning §4.2) a region
implements over its data files. The region answers with a
:class:`StatePensionEntitlement`: an exact start date (state pension
age plus any deferral, an income entitlement per §4.1) and annual
amounts split by uprating treatment, in the rates the forecast states.

Uprating is the engine's concern, governed by the
``policy.state_pension.uprating`` assumption (planning §7) parsed here
as :class:`StatePensionUprating`. The split matters because the two
slices uprate differently (planning §5.1, §6): the main entitlement
follows the policy rule (triple-lock proxy by default), while protected
payments and deferral increments uprate by CPI only.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol

from glidepath.core.config import EngineError
from glidepath.core.money import Money

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.provenance import Decision, Fact

_ZERO = Decimal(0)
_ZERO_MONEY = Money(_ZERO)
_MONTHS_PER_YEAR = 12
_UPRATING_CONTEXT = "policy.state_pension.uprating"


@dataclass(frozen=True, slots=True)
class StatePensionRecord:
    """One person's state pension position (planning §5.1).

    The official DWP forecast is the fact and the only route to an
    amount — free and instant from gov.uk/check-state-pension — so the
    model never re-derives what DWP has already computed;
    ``protected_payment`` is the slice of that forecast that uprates by
    CPI only (a pre-2016 transitional amount), so it may not appear
    without one. ``forecast_weekly_amount`` is optional only so plans
    saved before the forecast became mandatory still load; a region
    refuses to answer for a record without one. ``deferral_years``
    shifts the start past state pension age in whole months and earns
    the region's deferral increments.
    """

    forecast_weekly_amount: Fact[Money] | None
    protected_payment: Fact[Money] | None
    deferral_years: Decision[Decimal]

    def __post_init__(self) -> None:
        """Reject internally inconsistent records loudly."""
        forecast = self.forecast_weekly_amount
        protected = self.protected_payment
        if forecast is not None and forecast.value < _ZERO_MONEY:
            msg = "StatePensionRecord.forecast_weekly_amount must be non-negative"
            raise ValueError(msg)
        if protected is not None:
            if forecast is None:
                msg = (
                    "StatePensionRecord.protected_payment is a slice of the"
                    " official forecast and requires one (planning §5.1)"
                )
                raise ValueError(msg)
            if not _ZERO_MONEY <= protected.value <= forecast.value:
                msg = (
                    "StatePensionRecord.protected_payment must lie between"
                    " zero and the forecast weekly amount"
                )
                raise ValueError(msg)
        deferral = self.deferral_years.value
        if deferral < _ZERO:
            msg = "StatePensionRecord.deferral_years must be non-negative"
            raise ValueError(msg)
        if (deferral * _MONTHS_PER_YEAR) % 1 != 0:
            msg = (
                "StatePensionRecord.deferral_years must be a whole number of"
                " months (the §4.1 whole-month convention)"
            )
            raise ValueError(msg)


def deferral_months(record: StatePensionRecord) -> int:
    """The record's deferral as whole months (validated at construction)."""
    return int(record.deferral_years.value * _MONTHS_PER_YEAR)


@dataclass(frozen=True, slots=True)
class StatePensionEntitlement:
    """A region's answer for one record, in the rates the forecast states.

    ``annual_amount`` uprates by the ``policy.state_pension.uprating``
    assumption; ``cpi_uprated_annual_amount`` (any protected payment)
    uprates by CPI only (planning §5.1, §6). Both begin on
    ``start_date``. ``deferral_uplift`` is the deferral increment as a
    *fraction*: the increase applies to the rate payable **at claim**
    — which includes upratings earned during deferment — so the engine
    computes the increment amount in the starting period and uprates
    it by CPI only from then on (planning §5.1, §6).
    """

    start_date: date
    annual_amount: Money
    cpi_uprated_annual_amount: Money
    deferral_uplift: Decimal = _ZERO

    def __post_init__(self) -> None:
        """Reject negative entitlements."""
        if self.annual_amount < _ZERO_MONEY:
            msg = "StatePensionEntitlement.annual_amount must be non-negative"
            raise ValueError(msg)
        if self.cpi_uprated_annual_amount < _ZERO_MONEY:
            msg = (
                "StatePensionEntitlement.cpi_uprated_annual_amount must be non-negative"
            )
            raise ValueError(msg)
        if self.deferral_uplift < _ZERO:
            msg = "StatePensionEntitlement.deferral_uplift must be non-negative"
            raise ValueError(msg)


class StatePensionScheme(Protocol):
    """Region-supplied state pension rules (planning §4.2).

    Turns a record into an entitlement using the region's data: the
    state pension age timetable and deferral increments from the age
    rules. The amount itself is the record's stated DWP forecast — the
    region computes no rates of its own (planning §5.1).
    """

    def entitlement(
        self, record: StatePensionRecord, date_of_birth: date
    ) -> StatePensionEntitlement:
        """The record's entitlement in the rates the forecast states."""
        ...


class UpratingRule(Enum):
    """How the main state pension amount grows each year (planning §7)."""

    CPI = auto()
    """CPI-only uprating (the alternative scenario)."""
    TRIPLE_LOCK = auto()
    """Triple-lock proxy: ``max(CPI + earnings margin, floor)``."""


@dataclass(frozen=True, slots=True)
class StatePensionUprating:
    """The parsed ``policy.state_pension.uprating`` assumption value.

    The triple lock — ``max(earnings, CPI, floor)`` — is proxied as
    ``max(CPI + deterministic_cpi_margin, floor)``, the margin
    standing in for the long-run earnings premium (planning §7). The
    proxy applies in *every* run mode: CPI is deterministic across
    Monte Carlo paths by design and no earnings series is modelled,
    so each path uprates the state pension identically (issue #112).
    """

    rule: UpratingRule
    floor: Decimal | None = None
    cpi_margin: Decimal | None = None

    def __post_init__(self) -> None:
        """Require the triple-lock parameters exactly when they apply."""
        needs_parameters = self.rule is UpratingRule.TRIPLE_LOCK
        if needs_parameters:
            consistent = self.floor is not None and self.cpi_margin is not None
        else:
            consistent = self.floor is None and self.cpi_margin is None
        if not consistent:
            msg = (
                f"{_UPRATING_CONTEXT}: floor and deterministic_cpi_margin are"
                " required exactly for the triple_lock rule"
            )
            raise EngineError(msg)

    @classmethod
    def from_assumption_value(cls, value: object) -> StatePensionUprating:
        """Parse the assumption's value: a rule tag or a parameter table.

        Accepts the bare tag ``"cpi"`` (the alternative scenario of
        planning §7) or a table with a ``rule`` key plus the
        triple-lock parameters.

        Raises:
            EngineError: If the value has any other shape.
        """
        if isinstance(value, str):
            return cls(rule=_parse_rule(value))
        if not isinstance(value, Mapping):
            msg = (
                f"{_UPRATING_CONTEXT}: expected a rule tag or table,"
                f" got {type(value).__name__}"
            )
            raise EngineError(msg)
        entries = dict(value)
        rule = _parse_rule(_take_string(entries, "rule"))
        floor = _take_decimal(entries, "floor")
        margin = _take_decimal(entries, "deterministic_cpi_margin")
        if entries:
            unknown = ", ".join(sorted(entries))
            msg = f"{_UPRATING_CONTEXT}: unknown keys: {unknown}"
            raise EngineError(msg)
        return cls(rule=rule, floor=floor, cpi_margin=margin)

    def annual_rate(self, cpi: Decimal) -> Decimal:
        """The main amount's uprating rate in a year whose CPI is ``cpi``.

        Never negative: statutory uprating leaves rates unchanged when
        the relevant index falls (planning §5.1), so a deflationary CPI
        assumption freezes the pension rather than cutting it.
        """
        if (
            self.rule is UpratingRule.TRIPLE_LOCK
            and self.floor is not None
            and self.cpi_margin is not None
        ):
            return max(cpi + self.cpi_margin, self.floor, _ZERO)
        return max(cpi, _ZERO)


def _parse_rule(raw: str) -> UpratingRule:
    """Parse a rule tag (``"cpi"`` or ``"triple_lock"``)."""
    by_tag: Mapping[str, UpratingRule] = {
        "cpi": UpratingRule.CPI,
        "triple_lock": UpratingRule.TRIPLE_LOCK,
    }
    rule = by_tag.get(raw)
    if rule is None:
        known = ", ".join(sorted(by_tag))
        msg = f"{_UPRATING_CONTEXT}: unknown rule {raw!r} (one of: {known})"
        raise EngineError(msg)
    return rule


def _take_string(entries: dict[str, object], key: str) -> str:
    """Pop a required string entry from the assumption table."""
    raw = entries.pop(key, None)
    if not isinstance(raw, str):
        msg = f"{_UPRATING_CONTEXT}.{key}: expected a string tag"
        raise EngineError(msg)
    return raw


def _take_decimal(entries: dict[str, object], key: str) -> Decimal | None:
    """Pop an optional Decimal entry from the assumption table."""
    raw = entries.pop(key, None)
    if raw is None:
        return None
    if not isinstance(raw, Decimal):
        msg = f"{_UPRATING_CONTEXT}.{key}: expected a Decimal value"
        raise EngineError(msg)
    return raw
