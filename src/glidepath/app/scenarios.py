"""Scenario manager and comparison view models (roadmap 8.5; planning §4.3).

A scenario stored as deltas *is* its own diff (§4.3), so the manager
renders each scenario as its override list — orphaned overrides
flagged in place, never silently dropped — and presents the comparison
report (per-period metric values and diffs across scenarios, roadmap
6.3) as a table and a grouped chart, in either money basis.

The scenario-editing state transitions live here too: shells forward
raw text and stable option keys, targets are offered from exactly the
§4.3 whitelist (decision variables and assumptions — facts are never
overridable), and every rejection comes back as a message on an
:class:`~glidepath.app.plan.OverrideOutcome`, never an exception at a
shell (§4.7).
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Final

from glidepath.app.charts import (
    BASIS_HEADING,
    DEFAULT_CHART_BASIS,
    ChartBasisOption,
    ChartSeries,
    ChartSpec,
    basis_key,
    basis_options,
    basis_suffix,
)
from glidepath.app.display import format_money, format_value
from glidepath.app.labels import capitalised, display_label, entity_names, pretty_path
from glidepath.app.plan import (
    OverrideOutcome,
    PlanState,
    _parsed_override_value,
    state_with_scenarios,
)
from glidepath.app.tables import table_edit_text
from glidepath.core import (
    BASE_RUN_NAME,
    AssumptionKey,
    AssumptionTarget,
    EntityId,
    Money,
    Override,
    Scenario,
    compare_scenario_results,
    decision_target_catalogue,
    resolve_scenario,
    scenario_orphans,
)

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import (
        AssumptionSet,
        DecisionTargetInfo,
        Household,
        OverrideTarget,
        ReportBasis,
        ScenarioPeriodEntry,
    )

TITLE: Final = "Scenarios"

SCENARIOS_HEADING: Final = "Your scenarios"

ADD_SCENARIO_LABEL: Final = "Add scenario…"

REMOVE_SCENARIO_LABEL: Final = "Remove scenario"

NAME_PROMPT_TITLE: Final = "Add scenario"

NAME_PROMPT: Final = "Scenario name:"

OVERRIDES_HEADING: Final = "Overrides — what this scenario changes"

OVERRIDES_COLUMNS: Final = ("Target", "Value", "Note", "Status")

ADD_OVERRIDE_LABEL: Final = "Add override…"

REMOVE_OVERRIDE_LABEL: Final = "Remove override"

TARGET_PROMPT_TITLE: Final = "Add override"

TARGET_PROMPT: Final = "What should this scenario change?"

VALUE_PROMPT_TITLE: Final = "Override value"

VALUE_PROMPT: Final = "New value (blank removes the override):"

TABLE_VALUE_PROMPT: Final = (
    "New value — one 'key = value' line per figure, dotted keys for"
    " nested tables (blank removes the override):"
)

COMPARISON_HEADING: Final = "Comparison vs base plan"

COMPARISON_TABLE_LABEL: Final = "Table"

COMPARISON_CHART_LABEL: Final = "Chart"

METRIC_HEADING: Final = "Metric"

PERIOD_COLUMN_LABEL: Final = "Period"

BASE_RUN_LABEL: Final = "Base plan"

DELTA_COLUMN_SUFFIX: Final = " (vs base)"

NO_DELTA_TEXT: Final = "—"

VALID_STATUS: Final = "Ready"

ORPHANED_OVERRIDE_STATUS: Final = "Orphaned — target no longer in the plan"

NO_PLAN_MESSAGE: Final = (
    "No plan yet — save facts on the Facts tab before building scenarios."
)

NO_SCENARIOS_MESSAGE: Final = (
    "No scenarios yet — add one to compare a what-if against your base plan."
)

NO_VALID_SCENARIOS_MESSAGE: Final = (
    "No valid scenarios to compare — remove or retarget the flagged overrides."
)

SCENARIO_RUN_FAILED_PREFIX: Final = "The scenario comparison failed: "

DEFAULT_COMPARISON_METRIC_KEY: Final = "closing_balance"

_METRIC_LABELS: Final[Mapping[str, str]] = {
    "closing_balance": "Closing balance",
    "income_total": "Income in payment",
    "lump_sums": "Lump sums",
    "tax_due": "Tax due",
    "contributions": "Contributions",
    "withdrawals_gross": "Withdrawals (gross)",
    "net_withdrawn": "Net withdrawn",
    "spending_need": "Spending need",
    "planned_outflows": "Planned outflows",
    "shortfall": "Shortfall",
}
"""Display labels per comparison metric — keyed by the
:class:`~glidepath.core.PeriodMetrics` field names (guard-tested)."""

_DECISION_KEY_SEPARATOR: Final = ":"
"""Joins entity id and field path into one stable decision-target key.
Assumption keys are dotted but never hold a colon, so the two key
grammars cannot collide."""

_UNKNOWN_METRIC_MESSAGE: Final = "unknown comparison metric key"
_NO_PLAN_EDIT_MESSAGE: Final = "save your facts before building scenarios"
_EMPTY_NAME_MESSAGE: Final = "enter a scenario name"
_RESERVED_NAME_MESSAGE: Final = f"{BASE_RUN_NAME!r} is reserved for the base plan"
_DUPLICATE_NAME_MESSAGE: Final = "a scenario with that name already exists"
_UNKNOWN_SCENARIO_MESSAGE: Final = "unknown scenario"
_UNKNOWN_TARGET_MESSAGE: Final = "unknown override target"
_ORPHANED_TARGET_MESSAGE: Final = (
    "that target is no longer in the plan — remove the override instead"
)
_INT_VALUE_MESSAGE: Final = "enter a whole number"
_DECIMAL_VALUE_MESSAGE: Final = "enter a plain number, e.g. 0.05"


@dataclass(frozen=True)
class TargetOption:
    """One override target a scenario may change (the §4.3 whitelist).

    ``key`` is the stable identity a shell hands back on selection;
    ``edit_text`` pre-fills the value prompt with the base value; a
    non-empty ``choices`` narrows entry to those options (enum-valued
    targets); ``structured`` marks a table-valued assumption, edited
    as multi-line ``key = value`` text (issue #71).
    """

    key: str
    label: str
    edit_text: str
    structured: bool
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverrideRow:
    """One scenario override rendered as its own diff line (§4.3)."""

    target_key: str
    target_label: str
    value: str
    note: str
    status: str
    orphaned: bool
    edit_text: str
    structured: bool
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioItem:
    """One scenario in the manager: name, validity, and its override list."""

    name: str
    note: str
    status: str
    valid: bool
    overrides: tuple[OverrideRow, ...]


@dataclass(frozen=True)
class ComparisonMetricOption:
    """One per-period metric the comparison report can present."""

    key: str
    label: str


@dataclass(frozen=True)
class ScenariosViewModel:
    """The whole scenario manager + comparison screen (roadmap 8.5).

    ``comparison_rows`` are pre-formatted cells matching
    ``comparison_columns``; ``chart`` holds one series per run —
    alternatives side by side, never a stack — or ``None`` when there
    is nothing to compare, in which case ``message`` says why.
    """

    title: str
    scenarios_heading: str
    add_scenario_label: str
    remove_scenario_label: str
    name_prompt_title: str
    name_prompt: str
    overrides_heading: str
    overrides_columns: tuple[str, ...]
    add_override_label: str
    remove_override_label: str
    target_prompt_title: str
    target_prompt: str
    value_prompt_title: str
    value_prompt: str
    table_value_prompt: str
    scenarios: tuple[ScenarioItem, ...]
    target_options: tuple[TargetOption, ...]
    comparison_heading: str
    comparison_table_label: str
    comparison_chart_label: str
    basis_heading: str
    basis_options: tuple[ChartBasisOption, ...]
    selected_basis_key: str
    metric_heading: str
    metric_options: tuple[ComparisonMetricOption, ...]
    selected_metric_key: str
    comparison_columns: tuple[str, ...]
    comparison_rows: tuple[tuple[str, ...], ...]
    chart_categories: tuple[str, ...]
    chart: ChartSpec | None
    message: str


def metric_from_key(key: str) -> str:
    """The comparison metric a shell-selected option key denotes.

    Raises:
        ValueError: If ``key`` is not a known metric key.
    """
    if key not in _METRIC_LABELS:
        raise ValueError(_UNKNOWN_METRIC_MESSAGE)
    return key


def _target_key(target: OverrideTarget) -> str:
    """A target's stable option key: the assumption key, or id + path."""
    if isinstance(target, AssumptionTarget):
        return str(target.key.value)
    return f"{target.entity_id}{_DECISION_KEY_SEPARATOR}{target.field_path}"


def _edit_text(value: object) -> str:
    """A value as prompt-prefill text that parses back to its own shape.

    An unset optional decision (a ``None`` death age, §4.11) prefills
    empty — the blank that also means "remove the override", never a
    literal ``"None"`` the parser would reject.
    """
    if value is None:
        return ""
    if isinstance(value, Money):
        return str(value.quantized().amount)
    if isinstance(value, Enum):
        return format_value(value)
    if isinstance(value, Mapping):
        return table_edit_text(value)
    return str(value)


def _choices(value: object) -> tuple[str, ...]:
    """The display choices an enum-valued target narrows entry to."""
    if isinstance(value, Enum):
        return tuple(format_value(member) for member in type(value))
    return ()


def _decision_infos(
    household: Household | None,
) -> dict[tuple[EntityId, str], DecisionTargetInfo]:
    """The addressable decision targets, keyed for override lookup."""
    if household is None:
        return {}
    return {
        (info.target.entity_id, info.target.field_path): info
        for info in decision_target_catalogue(household)
    }


def scenario_target_options(state: PlanState) -> tuple[TargetOption, ...]:
    """Every legal override target: the plan's decisions, then assumptions.

    Exactly the §4.3 whitelist — the option list has no way to name a
    fact, so nothing user-stated can be offered for replacement.
    Labels are guaranteed unique (duplicates are numbered), so a shell
    resolving the user's pick by label cannot hit the wrong target.
    """
    if state.household is None:
        return ()
    names = entity_names(state.household)
    options = [
        TargetOption(
            key=_target_key(info.target),
            label=display_label(info.label, names),
            edit_text=_edit_text(info.value),
            structured=False,
            choices=_choices(info.value),
        )
        for info in decision_target_catalogue(state.household)
    ]
    for key in sorted(state.assumptions.keys, key=str):
        value = state.assumptions.get(key).value
        options.append(
            TargetOption(
                key=str(key.value),
                label=str(key.value),
                edit_text=_edit_text(value),
                structured=isinstance(value, Mapping),
            )
        )
    return _uniquely_labelled(options)


def _uniquely_labelled(options: list[TargetOption]) -> tuple[TargetOption, ...]:
    """The options with duplicate labels numbered in order.

    Planned-outflow labels are free user text with no uniqueness rule,
    so two outflows named alike would otherwise be indistinguishable
    in a picker that returns display text.
    """
    counts = Counter(option.label for option in options)
    numbered: Counter[str] = Counter()
    unique = []
    for option in options:
        if counts[option.label] == 1:
            unique.append(option)
            continue
        numbered[option.label] += 1
        unique.append(
            TargetOption(
                key=option.key,
                label=f"{option.label} ({numbered[option.label]})",
                edit_text=option.edit_text,
                structured=option.structured,
                choices=option.choices,
            )
        )
    return tuple(unique)


def state_with_scenario_added(
    state: PlanState, name: str, *, today: date
) -> OverrideOutcome:
    """Add an empty scenario named ``name``, or say why not (§4.3).

    Names must be non-blank, unique, and not the reserved base run
    name; scenarios need a base plan to diff against, so a household
    must have been captured first.
    """
    if state.household is None:
        return OverrideOutcome(state=state, error=_NO_PLAN_EDIT_MESSAGE)
    text = name.strip()
    if not text:
        return OverrideOutcome(state=state, error=_EMPTY_NAME_MESSAGE)
    if text == BASE_RUN_NAME:
        return OverrideOutcome(state=state, error=_RESERVED_NAME_MESSAGE)
    if any(scenario.name == text for scenario in state.scenarios):
        return OverrideOutcome(state=state, error=_DUPLICATE_NAME_MESSAGE)
    scenarios = (*state.scenarios, Scenario(name=text))
    return OverrideOutcome(state=state_with_scenarios(state, scenarios, today=today))


def state_without_scenario(state: PlanState, name: str, *, today: date) -> PlanState:
    """The state with the named scenario removed (a no-op if unknown)."""
    scenarios = tuple(s for s in state.scenarios if s.name != name)
    if len(scenarios) == len(state.scenarios):
        return state
    return state_with_scenarios(state, scenarios, today=today)


def state_with_scenario_override(
    state: PlanState,
    scenario_name: str,
    target_key: str,
    raw_value: str,
    *,
    today: date,
) -> OverrideOutcome:
    """Set one override on a scenario and re-run the comparison.

    A blank ``raw_value`` removes the override (the §4.3 symmetric of
    "blank restores the default"). A value that does not parse — or
    that the target entity's own invariants reject at resolution —
    leaves the state untouched and reports why.
    """
    scenario = _scenario_named(state, scenario_name)
    if scenario is None or state.household is None:
        return OverrideOutcome(state=state, error=_UNKNOWN_SCENARIO_MESSAGE)
    text = raw_value.strip()
    if not text:
        removed = state_without_scenario_override(
            state, scenario_name, target_key, today=today
        )
        return OverrideOutcome(state=removed)
    try:
        override = _parsed_override(state.household, state, target_key, text)
    except ValueError as exc:
        return OverrideOutcome(state=state, error=str(exc))
    candidate = _scenario_with_override(scenario, override)
    checkable = _without_orphans(candidate, state.household, state.assumptions)
    try:
        resolve_scenario(state.household, state.assumptions, checkable)
    except ValueError as exc:
        return OverrideOutcome(state=state, error=str(exc))
    scenarios = tuple(
        candidate if s.name == scenario_name else s for s in state.scenarios
    )
    return OverrideOutcome(state=state_with_scenarios(state, scenarios, today=today))


def state_without_scenario_override(
    state: PlanState, scenario_name: str, target_key: str, *, today: date
) -> PlanState:
    """The state with one override removed (a no-op if not present)."""
    scenario = _scenario_named(state, scenario_name)
    if scenario is None:
        return state
    overrides = tuple(
        override
        for override in scenario.overrides
        if _target_key(override.target) != target_key
    )
    if len(overrides) == len(scenario.overrides):
        return state
    changed = Scenario(name=scenario.name, overrides=overrides, note=scenario.note)
    scenarios = tuple(
        changed if s.name == scenario_name else s for s in state.scenarios
    )
    return state_with_scenarios(state, scenarios, today=today)


def _scenario_named(state: PlanState, name: str) -> Scenario | None:
    """The state's scenario named ``name``, if any."""
    for scenario in state.scenarios:
        if scenario.name == name:
            return scenario
    return None


def _without_orphans(
    scenario: Scenario, household: Household, assumptions: AssumptionSet
) -> Scenario:
    """The scenario with its orphaned overrides stripped, for validation.

    An orphaned override cannot resolve, but its presence must not
    exempt the *addressable* overrides from the entity invariants a
    resolution enforces — otherwise a scenario with one deleted target
    would silently accept invalid values everywhere else.
    """
    orphans = scenario_orphans(scenario, household, assumptions)
    if not orphans:
        return scenario
    overrides = tuple(
        override for override in scenario.overrides if override not in orphans
    )
    return Scenario(name=scenario.name, overrides=overrides, note=scenario.note)


def _scenario_with_override(scenario: Scenario, override: Override) -> Scenario:
    """The scenario with ``override`` replacing its target's entry, in place.

    An override on a new target appends; one on an existing target
    keeps its position — and its note, when the replacement carries
    none — so the diff view stays stable under value edits.
    """
    existing = next(
        (entry for entry in scenario.overrides if entry.target == override.target),
        None,
    )
    if existing is None:
        overrides = (*scenario.overrides, override)
    else:
        merged = Override(
            target=override.target,
            value=override.value,
            note=override.note if override.note is not None else existing.note,
        )
        overrides = tuple(
            merged if entry.target == override.target else entry
            for entry in scenario.overrides
        )
    return Scenario(name=scenario.name, overrides=overrides, note=scenario.note)


def _parsed_override(
    household: Household, state: PlanState, target_key: str, text: str
) -> Override:
    """The typed override ``target_key`` and ``text`` denote.

    Raises:
        ValueError: If the key names no addressable target or the text
            does not parse as the base value's shape.
    """
    if _DECISION_KEY_SEPARATOR in target_key:
        entity_text, _, field_path = target_key.partition(_DECISION_KEY_SEPARATOR)
        info = _decision_infos(household).get((EntityId(entity_text), field_path))
        if info is None:
            raise ValueError(_ORPHANED_TARGET_MESSAGE)
        return Override(
            target=info.target, value=_parsed_decision_value(info.value, text)
        )
    try:
        key = AssumptionKey(target_key)
    except ValueError:
        raise ValueError(_UNKNOWN_TARGET_MESSAGE) from None
    base = state.assumptions.get(key)
    return Override(
        target=AssumptionTarget(key=key), value=_parsed_override_value(base, text)
    )


def _parsed_decision_value(base_value: object, raw: str) -> object:
    """The typed value ``raw`` denotes for a decision target's shape.

    The §4.3 whitelist holds ``Money``, ``int``, ``Decimal``, and enum
    decisions only, so those are the shapes parsed here. A ``None``
    base is the whitelist's one optional-base decision — an unset
    ``death_age`` (§4.11), whose "what if I die at 75" must work over
    an alive-to-horizon base — and parses as the same whole-number
    template core resolution checks against.

    Raises:
        ValueError: If ``raw`` does not parse as the base value's shape.
    """
    if base_value is None:
        try:
            return int(raw, 10)
        except ValueError:
            raise ValueError(_INT_VALUE_MESSAGE) from None
    if isinstance(base_value, Money):
        return Money(_parsed_decimal(raw))
    if isinstance(base_value, Enum):
        return _parsed_choice(base_value, raw)
    if isinstance(base_value, int):
        try:
            return int(raw, 10)
        except ValueError:
            raise ValueError(_INT_VALUE_MESSAGE) from None
    if isinstance(base_value, Decimal):
        return _parsed_decimal(raw)
    msg = f"cannot override a {type(base_value).__name__} value"
    raise ValueError(msg)


def _parsed_decimal(raw: str) -> Decimal:
    """A finite decimal from user text.

    Raises:
        ValueError: If ``raw`` is not a plain finite number.
    """
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(_DECIMAL_VALUE_MESSAGE) from None
    if not value.is_finite():
        raise ValueError(_DECIMAL_VALUE_MESSAGE)
    return value


def _parsed_choice(base_value: Enum, raw: str) -> Enum:
    """The enum member ``raw`` names, by display text or member name.

    Raises:
        ValueError: If ``raw`` names no member, listing the choices.
    """
    text = raw.strip().lower()
    for member in type(base_value):
        if text in {format_value(member).lower(), member.name.lower()}:
            return member
    choices = ", ".join(format_value(member) for member in type(base_value))
    msg = f"choose one of: {choices}"
    raise ValueError(msg)


def build_scenarios_view_model(
    state: PlanState,
    basis: ReportBasis = DEFAULT_CHART_BASIS,
    metric_key: str = DEFAULT_COMPARISON_METRIC_KEY,
) -> ScenariosViewModel:
    """The scenario manager + comparison screen for ``state``.

    Scenarios render as their override lists (§4.3: the deltas *are*
    the diff); the comparison presents the selected metric per period
    across the base and every valid scenario. Without anything to
    compare, ``message`` carries the reason.

    Raises:
        ValueError: If ``metric_key`` is not a known metric key.
    """
    metric = metric_from_key(metric_key)
    names = entity_names(state.household)
    infos = _decision_infos(state.household)
    items = tuple(
        _scenario_item(scenario, state, names, infos) for scenario in state.scenarios
    )
    columns, rows, categories, chart = _comparison(state, basis, metric)
    return ScenariosViewModel(
        title=TITLE,
        scenarios_heading=SCENARIOS_HEADING,
        add_scenario_label=ADD_SCENARIO_LABEL,
        remove_scenario_label=REMOVE_SCENARIO_LABEL,
        name_prompt_title=NAME_PROMPT_TITLE,
        name_prompt=NAME_PROMPT,
        overrides_heading=OVERRIDES_HEADING,
        overrides_columns=OVERRIDES_COLUMNS,
        add_override_label=ADD_OVERRIDE_LABEL,
        remove_override_label=REMOVE_OVERRIDE_LABEL,
        target_prompt_title=TARGET_PROMPT_TITLE,
        target_prompt=TARGET_PROMPT,
        value_prompt_title=VALUE_PROMPT_TITLE,
        value_prompt=VALUE_PROMPT,
        table_value_prompt=TABLE_VALUE_PROMPT,
        scenarios=items,
        target_options=scenario_target_options(state),
        comparison_heading=COMPARISON_HEADING,
        comparison_table_label=COMPARISON_TABLE_LABEL,
        comparison_chart_label=COMPARISON_CHART_LABEL,
        basis_heading=BASIS_HEADING,
        basis_options=basis_options(),
        selected_basis_key=basis_key(basis),
        metric_heading=METRIC_HEADING,
        metric_options=tuple(
            ComparisonMetricOption(key=key, label=label)
            for key, label in _METRIC_LABELS.items()
        ),
        selected_metric_key=metric,
        comparison_columns=columns,
        comparison_rows=rows,
        chart_categories=categories,
        chart=chart,
        message=_message(state),
    )


def _message(state: PlanState) -> str:
    """The empty-state copy — why there is nothing to compare, if so."""
    if state.household is None:
        return NO_PLAN_MESSAGE
    if not state.scenarios:
        return NO_SCENARIOS_MESSAGE
    if state.scenario_run_error is not None:
        return SCENARIO_RUN_FAILED_PREFIX + state.scenario_run_error
    if state.scenario_runs is None:
        return NO_VALID_SCENARIOS_MESSAGE
    return ""


def _scenario_item(
    scenario: Scenario,
    state: PlanState,
    names: Mapping[str, str],
    infos: Mapping[tuple[EntityId, str], DecisionTargetInfo],
) -> ScenarioItem:
    """One scenario as its manager entry: status plus override rows."""
    orphans: tuple[Override, ...] = ()
    if state.household is not None:
        orphans = scenario_orphans(scenario, state.household, state.assumptions)
    rows = tuple(
        _override_row(override, names, infos, orphaned=override in orphans)
        for override in scenario.overrides
    )
    return ScenarioItem(
        name=scenario.name,
        note=scenario.note or "",
        status=VALID_STATUS if not orphans else _invalid_status(len(orphans)),
        valid=not orphans,
        overrides=rows,
    )


def _invalid_status(count: int) -> str:
    """The status line of a scenario with ``count`` orphaned overrides."""
    noun = "override" if count == 1 else "overrides"
    return f"Invalid — {count} orphaned {noun}; excluded from the comparison"


def _override_row(
    override: Override,
    names: Mapping[str, str],
    infos: Mapping[tuple[EntityId, str], DecisionTargetInfo],
    *,
    orphaned: bool,
) -> OverrideRow:
    """One override as its diff line, orphan status included."""
    target = override.target
    if isinstance(target, AssumptionTarget):
        label = str(target.key.value)
    else:
        info = infos.get((target.entity_id, target.field_path))
        label = (
            display_label(info.label, names)
            if info is not None
            else capitalised(pretty_path(target.field_path))
        )
    return OverrideRow(
        target_key=_target_key(target),
        target_label=label,
        value=format_value(override.value),
        note=override.note or "",
        status=ORPHANED_OVERRIDE_STATUS if orphaned else "",
        orphaned=orphaned,
        edit_text=_edit_text(override.value),
        structured=isinstance(override.value, Mapping),
        choices=_choices(override.value),
    )


def _comparison(
    state: PlanState, basis: ReportBasis, metric_key: str
) -> tuple[
    tuple[str, ...],
    tuple[tuple[str, ...], ...],
    tuple[str, ...],
    ChartSpec | None,
]:
    """The comparison table and chart for the selected metric and basis.

    The table covers the union of the runs' periods (blank cells where
    a run never modelled one); the chart clamps to periods every run
    modelled — a run with a shorter horizon has *no data* there, and a
    zero-height bar would falsely read as the metric falling to zero.
    """
    if state.scenario_runs is None:
        return (), (), (), None
    comparison = compare_scenario_results(state.scenario_runs, basis)
    base_name = comparison.run_names[0]
    columns = [PERIOD_COLUMN_LABEL, BASE_RUN_LABEL]
    for name in comparison.run_names[1:]:
        columns.extend((name, f"{name}{DELTA_COLUMN_SUFFIX}"))
    rows: list[tuple[str, ...]] = []
    categories: list[str] = []
    values: dict[str, list[Decimal]] = {name: [] for name in comparison.run_names}
    for row in comparison.rows:
        period_label = str(row.period.start.year)
        entries = {entry.run_name: entry for entry in row.entries}
        cells = [period_label, _metric_text(entries.get(base_name), metric_key)]
        for name in comparison.run_names[1:]:
            entry = entries.get(name)
            cells.append(_metric_text(entry, metric_key))
            cells.append(_delta_text(entry, metric_key))
        rows.append(tuple(cells))
        if len(entries) == len(comparison.run_names):
            categories.append(period_label)
            for name in comparison.run_names:
                values[name].append(getattr(entries[name].metrics, metric_key).amount)
    metric_label = _METRIC_LABELS[metric_key]
    series = tuple(
        ChartSeries(
            label=BASE_RUN_LABEL if name == base_name else name,
            values=tuple(values[name]),
        )
        for name in comparison.run_names
    )
    flat = [value for entry in values.values() for value in entry]
    chart = ChartSpec(
        title=metric_label,
        y_axis_label=f"{metric_label}, £ ({basis_suffix(basis)})",
        y_axis_max=max([*flat, Decimal(1)]),
        series=series,
    )
    return tuple(columns), tuple(rows), tuple(categories), chart


def _metric_text(entry: ScenarioPeriodEntry | None, metric_key: str) -> str:
    """One run's metric amount in one period, or blank if not modelled."""
    if entry is None:
        return ""
    money: Money = getattr(entry.metrics, metric_key)
    return format_money(money)


def _delta_text(entry: ScenarioPeriodEntry | None, metric_key: str) -> str:
    """A run's delta vs base, signed; a dash where no diff is meaningful."""
    if entry is None:
        return ""
    if entry.delta_vs_base is None:
        return NO_DELTA_TEXT
    delta: Money = getattr(entry.delta_vs_base, metric_key)
    text = format_money(delta)
    return f"+{text}" if delta.amount > 0 else text


__all__ = [
    "ADD_OVERRIDE_LABEL",
    "ADD_SCENARIO_LABEL",
    "DEFAULT_COMPARISON_METRIC_KEY",
    "NO_PLAN_MESSAGE",
    "NO_SCENARIOS_MESSAGE",
    "NO_VALID_SCENARIOS_MESSAGE",
    "ORPHANED_OVERRIDE_STATUS",
    "SCENARIO_RUN_FAILED_PREFIX",
    "VALID_STATUS",
    "ComparisonMetricOption",
    "OverrideRow",
    "ScenarioItem",
    "ScenariosViewModel",
    "TargetOption",
    "build_scenarios_view_model",
    "metric_from_key",
    "scenario_target_options",
    "state_with_scenario_added",
    "state_with_scenario_override",
    "state_without_scenario",
    "state_without_scenario_override",
]
