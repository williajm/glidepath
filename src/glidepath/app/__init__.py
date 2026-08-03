"""UI-agnostic application layer (roadmap 8.1 to 8.3; planning §4.7).

View models, user-facing copy (including the §1 disclaimer), display
formatting, form parsing, session state, and first-run state for any
UI shell. Everything here is plain typed Python over the scenario
layer and engine — no Qt imports (guard test), no assumption of a
desktop — so a future web shell can reuse it unchanged.
"""

from glidepath.app.copy import (
    ABOUT_TITLE,
    APP_NAME,
    DISCLAIMER_ACCEPT_LABEL,
    DISCLAIMER_BODY,
    DISCLAIMER_DECLINE_LABEL,
    DISCLAIMER_TITLE,
    HELP_MENU_LABEL,
)
from glidepath.app.display import (
    format_date,
    format_money,
    format_recorded,
    format_value,
)
from glidepath.app.firstrun import (
    FirstRunState,
    default_state_path,
    load_state,
    record_disclaimer_acknowledged,
)
from glidepath.app.forms import (
    ChoiceOption,
    FactsFormData,
    FactsFormResult,
    FactsFormViewModel,
    FieldKind,
    FieldSpec,
    FormError,
    SectionSpec,
    build_facts_form_view_model,
    format_form_errors,
    parse_facts_form,
)
from glidepath.app.inspector import (
    AssumptionRow,
    DecisionRow,
    FactRow,
    InspectorViewModel,
    RollForwardRow,
    StructureRow,
    build_inspector_view_model,
)
from glidepath.app.plan import (
    OVERRIDE_SOURCE,
    OverrideOutcome,
    PlanState,
    facts_saved_message,
    initial_plan_state,
    state_with_household,
    state_with_override,
)
from glidepath.app.shell import (
    AboutViewModel,
    DisclaimerViewModel,
    ShellViewModel,
    build_shell_view_model,
    should_show_disclaimer,
)

__all__ = [
    "ABOUT_TITLE",
    "APP_NAME",
    "DISCLAIMER_ACCEPT_LABEL",
    "DISCLAIMER_BODY",
    "DISCLAIMER_DECLINE_LABEL",
    "DISCLAIMER_TITLE",
    "HELP_MENU_LABEL",
    "OVERRIDE_SOURCE",
    "AboutViewModel",
    "AssumptionRow",
    "ChoiceOption",
    "DecisionRow",
    "DisclaimerViewModel",
    "FactRow",
    "FactsFormData",
    "FactsFormResult",
    "FactsFormViewModel",
    "FieldKind",
    "FieldSpec",
    "FirstRunState",
    "FormError",
    "InspectorViewModel",
    "OverrideOutcome",
    "PlanState",
    "RollForwardRow",
    "SectionSpec",
    "ShellViewModel",
    "StructureRow",
    "build_facts_form_view_model",
    "build_inspector_view_model",
    "build_shell_view_model",
    "default_state_path",
    "facts_saved_message",
    "format_date",
    "format_form_errors",
    "format_money",
    "format_recorded",
    "format_value",
    "initial_plan_state",
    "load_state",
    "parse_facts_form",
    "record_disclaimer_acknowledged",
    "should_show_disclaimer",
    "state_with_household",
    "state_with_override",
]
