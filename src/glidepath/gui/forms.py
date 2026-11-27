"""Facts entry widgets: thin bindings over the form specs (§4.7, 8.2).

Every label, hint, and message comes from the app layer's
:class:`~glidepath.app.FactsFormViewModel`; these widgets only render
fields, collect raw text back, and forward it to the submit callback.
Presentation mechanics live here: required fields carry the app
layer's marker (roadmap 10.2), hints double as tooltips so guidance
survives typing (roadmap 10.4), advanced fields sit behind a
"More options" disclosure per section (roadmap 10.1), submission
errors render inline under their fields with the first scrolled into
view (roadmap 10.2), and the Simple/Detailed switch shows or hides
rows and sections by the app layer's levels (roadmap 11.1) — a field
holding a value, or an error, always shows.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCalendarWidget,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import (
    DATE_PICKER_TOOLTIP,
    ENTITY_ID_KEY,
    HIDE_ADVANCED_LABEL,
    HIDE_DETAILS_LABEL,
    INCOME_PREFERENCE_ANNUITY,
    INCOME_PREFERENCE_KEY,
    OWNER_KEY,
    REQUIRED_MARKER,
    SHOW_ADVANCED_LABEL,
    SHOW_DETAILS_LABEL,
    FactsFormData,
    FactsFormViewModel,
    FieldKind,
    FieldLevel,
    FormView,
    PersonFormData,
    PlanEntityIds,
    SectionSpec,
    money_display_text,
    simple_view_shows,
)
from glidepath.gui.style import chevron_icon

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from PySide6.QtGui import QResizeEvent, QWheelEvent

    from glidepath.app import FactsSubmissionOutcome, FieldSpec, FormError


class ScrollSafeComboBox(QComboBox):
    """A combo box that scroll gestures pass over instead of spinning.

    Inside a scrolling form, a stock ``QComboBox`` under the pointer
    swallows wheel events and changes its value — so scrolling the
    page silently edits the plan. This variant only reacts to the
    wheel once the user has clicked into it; otherwise the event
    propagates to the surrounding scroll area.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Take focus by click or tab only, never by wheel-over."""
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Spin the value only when focused; else let the page scroll."""
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


_WIDTH_CHARS: dict[FieldKind, int] = {
    FieldKind.MONEY: 13,
    FieldKind.NUMBER: 8,
    FieldKind.DATE: 13,
    FieldKind.TEXT: 40,
}
"""Entry widths per field kind, in digit widths (roadmap 11.3).

Measured in the widget's own font, so the widths follow the user's
font and scaling rather than fixed pixels; amounts and short numbers
are sized to what they hold instead of spanning the window. Choices
size to their longest option instead, so no option is ever clipped."""
_WIDTH_ALLOWANCE_CHARS = 5
"""Room for the padding, the border, and a prefix or picker icon."""
_SIDE_HINT_KINDS = frozenset({FieldKind.MONEY, FieldKind.NUMBER, FieldKind.DATE})
"""Short entries whose hint reads beside them, not as a placeholder."""


def _fit_width(editor: QLineEdit | QComboBox, kind: FieldKind) -> None:
    """Size ``editor`` to what its kind of value needs (constant docstring)."""
    if isinstance(editor, QComboBox):
        editor.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        editor.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return
    digits = _WIDTH_CHARS[kind] + _WIDTH_ALLOWANCE_CHARS
    editor.setMaximumWidth(editor.fontMetrics().horizontalAdvance("0" * digits))


class MoneyEntry(QLineEdit):
    """An amount field: a £ prefix, thousands separators when typed.

    The prefix is a label drawn inside the field's left text margin, so
    the text itself stays exactly what the app layer parses; leaving
    the field re-shows the amount with separators
    (:func:`~glidepath.app.money_display_text`).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the line edit with its prefix label."""
        super().__init__(parent)
        self.prefix = QLabel("£", self)
        self.prefix.setObjectName("moneyPrefix")
        self.prefix.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        margins = self.textMargins()
        self.setTextMargins(
            self.fontMetrics().horizontalAdvance("£ "),
            margins.top(),
            margins.right(),
            margins.bottom(),
        )
        self.editingFinished.connect(self._show_separators)

    def set_amount_text(self, text: str) -> None:
        """Set the text as the form shows amounts (with separators)."""
        self.setText(money_display_text(text))

    def _show_separators(self) -> None:
        """Re-show the typed amount with thousands separators."""
        self.set_amount_text(self.text())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep the prefix at the start of the text area."""
        super().resizeEvent(event)
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        area = self.style().subElementRect(
            QStyle.SubElement.SE_LineEditContents, option, self
        )
        self.prefix.setGeometry(
            area.left(), area.top(), self.textMargins().left(), area.height()
        )


class DateEntry(QLineEdit):
    """An ISO date field: typed text first, a calendar popup as assist.

    A plain line edit (so blank stays honestly blank — the app layer's
    blank-means-today/none semantics survive, `FieldKind.DATE`) with a
    trailing action that pops a calendar; picking a day writes it back
    as ``YYYY-MM-DD`` text, exactly as if typed.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the line edit with its calendar action and popup."""
        super().__init__(parent)
        self.calendar = QCalendarWidget(self)
        self.calendar.setWindowFlags(Qt.WindowType.Popup)
        self.calendar.clicked.connect(self._day_picked)
        # clicked covers the mouse; activated (Enter/Return) covers the
        # keyboard — without it the popup can be navigated but never
        # committed by keyboard users.
        self.calendar.activated.connect(self._day_picked)
        self.pick_action = self.addAction(
            chevron_icon(), QLineEdit.ActionPosition.TrailingPosition
        )
        self.pick_action.setToolTip(DATE_PICKER_TOOLTIP)
        self.pick_action.triggered.connect(self._open_calendar)

    def _open_calendar(self) -> None:
        """Pop the calendar under the field, seeded from its text."""
        current = QDate.fromString(self.text().strip(), Qt.DateFormat.ISODate)
        if current.isValid():
            self.calendar.setSelectedDate(current)
        self.calendar.move(self.mapToGlobal(self.rect().bottomLeft()))
        self.calendar.show()

    def _day_picked(self, day: QDate) -> None:
        """Write the picked day back as ISO text and close the popup."""
        self.setText(day.toString(Qt.DateFormat.ISODate))
        self.calendar.hide()


class SectionForm(QGroupBox):
    """One titled group of fields rendered from a :class:`SectionSpec`.

    Advanced fields (`FieldSpec.advanced`, roadmap 10.1) render into a
    sub-form hidden behind the "More options" toggle; loading a value
    into one, or landing an error on one, reveals them — hidden data
    or hidden errors would read as the app ignoring the user.

    In the Simple view (roadmap 11.1) each row shows only when the app
    layer's :func:`~glidepath.app.simple_view_shows` says so, or while
    it holds a value or an error; there is no disclosure toggle.
    Visibility is re-derived on view changes, loads, errors and the
    controlling choices of conditional fields — never mid-typing, so a
    field never vanishes under the cursor.
    """

    def __init__(self, spec: SectionSpec, parent: QWidget | None = None) -> None:
        """Render the section's fields as labelled editors."""
        super().__init__(spec.title, parent)
        self._spec = spec
        self._view = FormView.DETAILED
        self._advanced_disclosed = False
        self._editors: dict[str, QLineEdit | QComboBox] = {}
        self._rows: dict[str, QFormLayout] = {}
        self._row_fields: dict[str, QWidget] = {}
        self._error_labels: dict[str, QLabel] = {}
        self._errored: set[str] = set()
        self._advanced_keys = frozenset(
            field.key for field in spec.fields if field.advanced
        )
        layout = QVBoxLayout(self)
        self._add_header(layout, spec)
        self.section_error_label = QLabel("", self)
        self.section_error_label.setObjectName("fieldErrorLabel")
        self.section_error_label.setWordWrap(True)
        self.section_error_label.hide()
        layout.addWidget(self.section_error_label)
        basic = QFormLayout()
        layout.addLayout(basic)
        self._advanced_widget = QWidget(self)
        advanced = QFormLayout(self._advanced_widget)
        advanced.setContentsMargins(0, 0, 0, 0)
        for field in spec.fields:
            self._add_field(advanced if field.advanced else basic, field)
        layout.addWidget(self._advanced_widget)
        self.advanced_toggle = QPushButton(SHOW_ADVANCED_LABEL, self)
        self.advanced_toggle.setObjectName("advancedToggle")
        self.advanced_toggle.clicked.connect(self.toggle_advanced)
        layout.addWidget(self.advanced_toggle)
        self._advanced_widget.setVisible(False)
        if not self._advanced_keys:
            self.advanced_toggle.setVisible(False)
        for field in spec.fields:
            if field.essential_when is not None:
                controlling = self._editors.get(field.essential_when[0])
                if isinstance(controlling, QComboBox):
                    controlling.currentIndexChanged.connect(self.refresh_view)

    def _with_side_hint(
        self, editor: QLineEdit | QComboBox, field: FieldSpec
    ) -> QWidget:
        """The row's field: a short entry carries its hint beside it (11.3).

        Amounts, numbers and dates are sized to what they hold, so a
        long hint would be clipped as a placeholder; it reads in full
        as muted text in the space the short entry leaves, and stays
        the tooltip (roadmap 10.4).
        """
        if not field.hint or field.kind not in _SIDE_HINT_KINDS:
            return editor
        if isinstance(editor, QLineEdit):
            editor.setPlaceholderText("")
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(editor)
        hint = QLabel(field.hint, row)
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        row_layout.addWidget(hint, 1)
        return row

    def _add_header(self, layout: QVBoxLayout, spec: SectionSpec) -> None:
        """The one-line summary with the full description behind a toggle.

        A section without a summary shows its description as it is.
        """
        self.description_label = QLabel(spec.description, self)
        self.description_label.setWordWrap(True)
        self.details_toggle = QPushButton(SHOW_DETAILS_LABEL, self)
        self.details_toggle.setObjectName("detailsToggle")
        self.details_toggle.clicked.connect(self.toggle_details)
        if spec.summary:
            summary = QLabel(spec.summary, self)
            summary.setObjectName("sectionSummary")
            summary.setWordWrap(True)
            header = QHBoxLayout()
            header.addWidget(summary, 1)
            header.addWidget(self.details_toggle)
            layout.addLayout(header)
            self.description_label.setVisible(False)
        else:
            self.details_toggle.setVisible(False)
            self.description_label.setVisible(bool(spec.description))
        layout.addWidget(self.description_label)

    @property
    def spec(self) -> SectionSpec:
        """The section spec this form renders."""
        return self._spec

    def toggle_details(self) -> None:
        """Show or hide the section's full description (roadmap 11.3)."""
        showing = self.description_label.isHidden()
        self.description_label.setVisible(showing)
        self.details_toggle.setText(
            HIDE_DETAILS_LABEL if showing else SHOW_DETAILS_LABEL
        )

    def _add_field(self, layout: QFormLayout, field: FieldSpec) -> None:
        """Render one field row: marker, editor, tooltip, error label."""
        label = field.label
        if field.required:
            label = f"{label} {REQUIRED_MARKER}"
        editor: QLineEdit | QComboBox
        if field.kind is FieldKind.CHOICE:
            editor = ScrollSafeComboBox(self)
            for option in field.choices:
                editor.addItem(option.label, option.value)
        else:
            if field.kind is FieldKind.DATE:
                editor = DateEntry(self)
            elif field.kind is FieldKind.MONEY:
                editor = MoneyEntry(self)
            else:
                editor = QLineEdit(self)
            editor.setPlaceholderText(field.hint)
        _fit_width(editor, field.kind)
        if field.hint:
            editor.setToolTip(field.hint)
        self._editors[field.key] = editor
        self._rows[field.key] = layout
        row_field = self._with_side_hint(editor, field)
        self._row_fields[field.key] = row_field
        layout.addRow(label, row_field)
        error = QLabel("", self)
        error.setObjectName("fieldErrorLabel")
        error.setWordWrap(True)
        error.hide()
        layout.addRow(error)
        self._error_labels[field.key] = error

    @property
    def advanced_visible(self) -> bool:
        """Whether the advanced fields are currently disclosed."""
        return not self._advanced_widget.isHidden()

    def set_advanced_visible(self, *, visible: bool) -> None:
        """Disclose (or tuck away) the section's advanced fields."""
        self._advanced_disclosed = visible
        self.advanced_toggle.setText(
            HIDE_ADVANCED_LABEL if visible else SHOW_ADVANCED_LABEL
        )
        if self._view is FormView.DETAILED:
            self._advanced_widget.setVisible(visible)

    def set_view(self, view: FormView) -> None:
        """Show the section as the Simple or Detailed view (roadmap 11.1)."""
        self._view = view
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-derive which rows show in the current view (class docstring)."""
        detailed = self._view is FormView.DETAILED
        values = self.values()
        any_advanced = False
        for field in self._spec.fields:
            shown = (
                detailed
                or simple_view_shows(self._spec, field, values)
                or bool(values[field.key])
                or field.key in self._errored
            )
            self._rows[field.key].setRowVisible(self._row_fields[field.key], shown)
            any_advanced = any_advanced or (shown and field.advanced)
        if detailed:
            self.advanced_toggle.setVisible(bool(self._advanced_keys))
            self._advanced_widget.setVisible(self._advanced_disclosed)
        else:
            self.advanced_toggle.setVisible(False)
            self._advanced_widget.setVisible(any_advanced)

    def has_content(self) -> bool:
        """Whether any field holds a value or the section shows an error."""
        return bool(self._errored) or any(self.values().values())

    def toggle_advanced(self) -> None:
        """Flip the advanced-fields disclosure."""
        self.set_advanced_visible(visible=self._advanced_widget.isHidden())

    def reveal(self, key: str) -> None:
        """Disclose the advanced fields when ``key`` sits behind them."""
        if key in self._advanced_keys:
            self.set_advanced_visible(visible=True)

    def editor(self, key: str) -> QLineEdit | QComboBox:
        """The editor widget for ``key`` (tests and shells drive it)."""
        return self._editors[key]

    def editor_or_none(self, key: str) -> QLineEdit | QComboBox | None:
        """The editor widget for ``key``, or ``None`` for a section error."""
        return self._editors.get(key)

    def set_value(self, key: str, value: str) -> None:
        """Set one field's raw value, revealing a populated advanced field."""
        editor = self._editors[key]
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(editor.findData(value))
        elif isinstance(editor, MoneyEntry):
            editor.set_amount_text(value)
        else:
            editor.setText(value)
        if value:
            self.reveal(key)

    def values(self) -> dict[str, str]:
        """The section's raw text, keyed exactly like its spec."""
        collected: dict[str, str] = {}
        for key, editor in self._editors.items():
            if isinstance(editor, QComboBox):
                collected[key] = str(editor.currentData())
            else:
                collected[key] = editor.text()
        return collected

    def set_values(self, values: Mapping[str, str]) -> None:
        """Set the given fields; keys absent from ``values`` are untouched."""
        for key, value in values.items():
            self.set_value(key, value)
        self.refresh_view()

    def set_error(self, field_key: str, message: str) -> None:
        """Show one inline error (an empty key errors the section)."""
        label = self._error_labels.get(field_key, self.section_error_label)
        label.setText(message)
        label.show()
        self._errored.add(field_key)
        self.reveal(field_key)
        self.refresh_view()

    def clear_errors(self) -> None:
        """Hide every inline error the last submission showed."""
        self._errored.clear()
        self.section_error_label.hide()
        self.section_error_label.setText("")
        for label in self._error_labels.values():
            label.hide()
            label.setText("")

    def clear(self) -> None:
        """Blank every field, reset choices, and tuck the advanced away."""
        for editor in self._editors.values():
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
            else:
                editor.setText("")
        self.clear_errors()
        if self._advanced_keys:
            self.set_advanced_visible(visible=False)
        self.refresh_view()


class RepeatableSection(QWidget):
    """A list of :class:`SectionForm` instances with add/remove controls.

    Each instance carries its entity id opaquely (blank for a row the
    user added), so deleting or reordering rows keeps every surviving
    entity's identity — the id travels with its row, never with its
    position (§4.3).
    """

    def __init__(self, spec: SectionSpec, parent: QWidget | None = None) -> None:
        """Render the empty list with its add button."""
        super().__init__(parent)
        self._spec = spec
        self._view = FormView.DETAILED
        self._entries: list[QWidget] = []
        self._forms: list[SectionForm] = []
        self._entity_ids: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout = QVBoxLayout()
        layout.addLayout(self._list_layout)
        self.add_button = QPushButton(spec.add_label, self)
        self.add_button.clicked.connect(self.add_entry)
        layout.addLayout(_compact(self.add_button))

    def add_entry(self, *, entity_id: str = "") -> SectionForm:
        """Append one more section instance and return its form."""
        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        form = SectionForm(self._spec, container)
        form.set_view(self._view)
        remove = QPushButton(self._spec.remove_label, container)
        remove.clicked.connect(lambda: self._remove_entry(container))
        container_layout.addWidget(form)
        container_layout.addLayout(_compact(remove))
        self._list_layout.addWidget(container)
        self._entries.append(container)
        self._forms.append(form)
        self._entity_ids.append(entity_id)
        return form

    def _remove_entry(self, container: QWidget) -> None:
        """Drop one section instance — and its entity id — from the list."""
        index = self._entries.index(container)
        del self._entries[index]
        del self._forms[index]
        del self._entity_ids[index]
        self._list_layout.removeWidget(container)
        container.deleteLater()

    @property
    def forms(self) -> tuple[SectionForm, ...]:
        """The live section instances, in display order."""
        return tuple(self._forms)

    @property
    def spec(self) -> SectionSpec:
        """The section spec every row renders."""
        return self._spec

    def set_view(self, view: FormView) -> None:
        """Show the rows as the Simple or Detailed view (roadmap 11.1).

        Simple offers the add action only for an essential section
        (accounts); rows that exist always show, whatever the view.
        """
        self._view = view
        for form in self._forms:
            form.set_view(view)
        self.add_button.setVisible(
            view is FormView.DETAILED or self._spec.level is FieldLevel.ESSENTIAL
        )

    def values_list(self) -> tuple[dict[str, str], ...]:
        """Raw text for every instance, its entity id included, in order."""
        return tuple(
            {ENTITY_ID_KEY: entity_id, **form.values()}
            for entity_id, form in zip(self._entity_ids, self._forms, strict=True)
        )

    def set_values_list(self, values_list: Sequence[Mapping[str, str]]) -> None:
        """Replace every instance with one per entry of ``values_list``.

        The reserved entity-id and owner keys never render as fields —
        the id travels with the row and the owner is the pane's
        section placement (roadmap 9.31).
        """
        reserved = (ENTITY_ID_KEY, OWNER_KEY)
        self.clear()
        for values in values_list:
            form = self.add_entry(entity_id=values.get(ENTITY_ID_KEY, ""))
            form.set_values(
                {key: value for key, value in values.items() if key not in reserved}
            )

    def set_entity_ids(self, entity_ids: Sequence[str]) -> None:
        """Re-seed every row's entity id, e.g. after a successful save."""
        if len(entity_ids) != len(self._forms):
            msg = "one entity id per section instance required"
            raise ValueError(msg)
        self._entity_ids = list(entity_ids)

    def clear(self) -> None:
        """Drop every section instance."""
        for container in tuple(self._entries):
            self._remove_entry(container)


def _compact(button: QPushButton) -> QHBoxLayout:
    """A row holding ``button`` at its own size, not the window's width."""
    row = QHBoxLayout()
    row.addWidget(button)
    row.addStretch()
    return row


def _owned_rows(section: RepeatableSection, owner: int) -> tuple[dict[str, str], ...]:
    """The section's rows stamped with their owning person (§4.11)."""
    return tuple({**row, OWNER_KEY: str(owner)} for row in section.values_list())


def _partner_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[tuple[Mapping[str, str], ...], tuple[Mapping[str, str], ...]]:
    """One section's combined rows split (first person's, partner's)."""
    mine = tuple(row for row in rows if row.get(OWNER_KEY, "") != "1")
    theirs = tuple(row for row in rows if row.get(OWNER_KEY, "") == "1")
    return mine, theirs


class FactsEntryPane(QWidget):
    """The facts tab: sections, submit and clear buttons, a status line.

    The partner is strictly optional (roadmap 9.31): their sections sit
    hidden behind one explicit "Add a partner" action, and removing the
    partner confirms first — it deletes their person details and every
    row under them. With no partner the pane renders and submits
    exactly as the single-person form always has.

    Both annuity purchase sections — the first person's and the
    partner's — sit directly beneath the retirement-income preference
    that reveals them (roadmap 10.3): they render only while the
    preference is annuity-or-mix — or rows already exist, so data is
    never hidden — the partner's additionally only while a partner is
    on the form, and switching back to drawdown-only confirms before
    it deletes the purchase rows.

    The Simple/Detailed switch (roadmap 11.1) re-derives every row's
    and section's visibility from the app layer's levels: Simple shows
    the essential sections plus any section or row holding a value or
    an error, and a one-line note naming what Detailed adds.
    """

    def __init__(  # noqa: PLR0913 - the pane's whole binding surface
        self,
        view_model: FactsFormViewModel,
        on_submit: Callable[[FactsFormData], FactsSubmissionOutcome],
        on_clear: Callable[[], str | None],
        parent: QWidget | None = None,
        *,
        view: FormView = FormView.DETAILED,
        on_view_changed: Callable[[FormView], None] | None = None,
    ) -> None:
        """Render the form and wire the submit, clear and view callbacks."""
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_clear = on_clear
        self._view = view
        self._on_view_changed = on_view_changed
        self._remove_partner_title = view_model.remove_partner_label
        self._remove_partner_confirm = view_model.remove_partner_confirm
        self._drawdown_only_title = view_model.retirement_income.title
        self._drawdown_only_confirm = view_model.drawdown_only_confirm
        self._partner_active = False
        self._syncing_income = False

        self.person_form = SectionForm(view_model.person)
        self.spending_form = SectionForm(view_model.spending)
        self.retirement_income_form = SectionForm(view_model.retirement_income)
        self.state_pension_form = SectionForm(view_model.state_pension)
        self.wrappers = RepeatableSection(view_model.wrapper)
        self.db_pensions = RepeatableSection(view_model.db_pension)
        self.annuity_purchases = RepeatableSection(view_model.annuity_purchase)
        self._build_partner_sections(view_model)
        income_editor = self.retirement_income_form.editor(INCOME_PREFERENCE_KEY)
        if isinstance(income_editor, QComboBox):
            income_editor.currentIndexChanged.connect(self._income_preference_changed)
        self.submit_button = QPushButton(view_model.submit_label, self)
        self.submit_button.setObjectName("primaryButton")
        self.submit_button.clicked.connect(self.submit)
        self.clear_button = QPushButton(view_model.clear_label, self)
        self.clear_button.clicked.connect(self.clear)
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self._build_view_switch(view_model)
        self._assemble_layout(view_model)
        self._apply_view()

    def _build_view_switch(self, view_model: FactsFormViewModel) -> None:
        """Create the Simple/Detailed switch and the Simple note (11.1)."""
        self.view_buttons: dict[FormView, QPushButton] = {}
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        for view in FormView:
            button = QPushButton(view_model.view_labels.get(view, view.value), self)
            button.setObjectName("viewSwitchButton")
            button.setCheckable(True)
            button.setChecked(view is self._view)
            button.clicked.connect(
                lambda _checked=False, chosen=view: self._choose_view(chosen)
            )
            self._view_group.addButton(button)
            self.view_buttons[view] = button
        self.simple_note = QLabel(view_model.simple_view_note, self)
        self.simple_note.setWordWrap(True)
        self.simple_note.setObjectName("simpleViewNote")

    @property
    def view(self) -> FormView:
        """The view the form currently shows."""
        return self._view

    def set_view(self, view: FormView) -> None:
        """Show the form as ``view`` without reporting it as a user choice."""
        self._view = view
        self.view_buttons[view].setChecked(True)
        self._apply_view()

    def _choose_view(self, view: FormView) -> None:
        """Apply a view the user picked and report it for persistence."""
        changed = view is not self._view
        self.set_view(view)
        if changed and self._on_view_changed is not None:
            self._on_view_changed(view)

    def _apply_view(self) -> None:
        """Re-derive every section's and row's visibility (class docstring)."""
        simple = self._view is FormView.SIMPLE
        for form in (
            self.person_form,
            self.spending_form,
            self.retirement_income_form,
            self.state_pension_form,
            self.partner_form,
            self.partner_state_pension_form,
        ):
            form.set_view(self._view)
            form.setVisible(
                not simple
                or form.spec.level is FieldLevel.ESSENTIAL
                or form.has_content()
            )
        for repeatable in (
            self.wrappers,
            self.db_pensions,
            self.annuity_purchases,
            self.partner_wrappers,
            self.partner_db_pensions,
            self.partner_annuity_purchases,
        ):
            repeatable.set_view(self._view)
        for repeatable in (self.db_pensions, self.partner_db_pensions):
            repeatable.setVisible(not simple or bool(repeatable.forms))
        self.simple_note.setVisible(simple)
        self.intro_label.setVisible(not simple)
        self._sync_annuity_sections()

    def _assemble_layout(self, view_model: FactsFormViewModel) -> None:
        """Stack the sections into the scroll area and the button row."""
        self.intro_label = QLabel(view_model.intro, self)
        self.intro_label.setWordWrap(True)
        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        switch_row = QHBoxLayout()
        for button in self.view_buttons.values():
            switch_row.addWidget(button)
        switch_row.addStretch()
        content_layout.addLayout(switch_row)
        content_layout.addWidget(self.simple_note)
        content_layout.addWidget(self.intro_label)
        content_layout.addWidget(self.person_form)
        content_layout.addWidget(self.spending_form)
        content_layout.addWidget(self.retirement_income_form)
        # Both annuity sections sit directly under the preference that
        # reveals them (roadmap 10.3) — disclosure next to its control.
        # The partner's shows only while a partner is on the form.
        content_layout.addWidget(self.annuity_purchases)
        content_layout.addWidget(self.partner_annuity_purchases)
        content_layout.addWidget(self.state_pension_form)
        content_layout.addWidget(self.wrappers)
        content_layout.addWidget(self.db_pensions)
        content_layout.addLayout(_compact(self.add_partner_button))
        content_layout.addWidget(self._partner_group)
        content_layout.addStretch()

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(content)

        buttons = QWidget(self)
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.addWidget(self.clear_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.submit_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)

    def _build_partner_sections(self, view_model: FactsFormViewModel) -> None:
        """Create the add-partner action and the hidden partner group."""
        self.add_partner_button = QPushButton(view_model.add_partner_label, self)
        self.add_partner_button.clicked.connect(self.add_partner)
        self.partner_form = SectionForm(view_model.partner)
        self.partner_state_pension_form = SectionForm(view_model.partner_state_pension)
        self.partner_wrappers = RepeatableSection(view_model.partner_wrapper)
        self.partner_db_pensions = RepeatableSection(view_model.partner_db_pension)
        self.partner_annuity_purchases = RepeatableSection(
            view_model.partner_annuity_purchase
        )
        self.remove_partner_button = QPushButton(view_model.remove_partner_label)
        self.remove_partner_button.clicked.connect(self._confirm_remove_partner)
        self._partner_group = QWidget(self)
        partner_layout = QVBoxLayout(self._partner_group)
        partner_layout.setContentsMargins(0, 0, 0, 0)
        partner_layout.addWidget(self.partner_form)
        partner_layout.addWidget(self.partner_state_pension_form)
        partner_layout.addWidget(self.partner_wrappers)
        partner_layout.addWidget(self.partner_db_pensions)
        partner_layout.addLayout(_compact(self.remove_partner_button))
        self._partner_group.setVisible(False)

    @property
    def has_partner(self) -> bool:
        """Whether the partner sections are on the form."""
        return self._partner_active

    def add_partner(self) -> None:
        """Put the (blank) partner sections on the form (§4.11)."""
        self._set_partner_active(active=True)

    def remove_partner(self) -> None:
        """Take the partner off the form, deleting all their rows."""
        self.partner_form.clear()
        self.partner_state_pension_form.clear()
        self.partner_wrappers.clear()
        self.partner_db_pensions.clear()
        self.partner_annuity_purchases.clear()
        self._set_partner_active(active=False)

    def _confirm_remove_partner(self) -> None:
        """Confirm before the removal deletes the partner's rows (§4.11)."""
        answer = QMessageBox.question(
            self,
            self._remove_partner_title,
            self._remove_partner_confirm,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_partner()

    def _set_partner_active(self, *, active: bool) -> None:
        """Swap between the add-partner action and the partner sections.

        The partner's annuity section lives under the household
        preference, outside the partner group, so its visibility is
        re-derived here rather than riding the group's.
        """
        self._partner_active = active
        self._partner_group.setVisible(active)
        self.add_partner_button.setVisible(not active)
        self._apply_view()

    def _income_preference(self) -> str:
        """The income-preference token currently selected (10.3)."""
        return self.retirement_income_form.values().get(INCOME_PREFERENCE_KEY, "")

    def _annuity_rows_exist(self) -> bool:
        """Whether any annuity purchase rows sit on the form."""
        return bool(self.annuity_purchases.forms) or bool(
            self.partner_annuity_purchases.forms
        )

    def _sync_annuity_sections(self) -> None:
        """Show the purchase sections per the preference — rows always show.

        A row that exists is never hidden (hidden data would still
        submit); with no rows, the preference alone decides. The
        partner's section additionally needs a partner on the form.
        """
        offered = (
            self._view is FormView.DETAILED
            and self._income_preference() == INCOME_PREFERENCE_ANNUITY
        )
        visible = offered or self._annuity_rows_exist()
        self.annuity_purchases.setVisible(visible)
        self.partner_annuity_purchases.setVisible(visible and self._partner_active)

    def _set_income_preference(self, value: str) -> None:
        """Set the preference combo without re-entering the handler."""
        self._syncing_income = True
        try:
            self.retirement_income_form.set_value(INCOME_PREFERENCE_KEY, value)
        finally:
            self._syncing_income = False

    def _income_preference_changed(self) -> None:
        """Apply a preference switch, confirming a destructive one (10.3).

        Switching back to drawdown-only while purchase rows exist
        deletes them — the same confirm-first rule as removing the
        partner; declining reverts the combo instead.
        """
        if self._syncing_income:
            return
        if (
            self._income_preference() != INCOME_PREFERENCE_ANNUITY
            and self._annuity_rows_exist()
        ):
            answer = QMessageBox.question(
                self,
                self._drawdown_only_title,
                self._drawdown_only_confirm,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_income_preference(INCOME_PREFERENCE_ANNUITY)
                return
            self.annuity_purchases.clear()
            self.partner_annuity_purchases.clear()
        self._sync_annuity_sections()

    def form_data(self) -> FactsFormData:
        """The whole form's raw text as one submission.

        Rows carry their owning person (:data:`~glidepath.app.OWNER_KEY`)
        from the section they sit in, first person's rows first — the
        order :func:`~glidepath.app.plan_entity_ids` seeds back.
        """
        persons = [
            PersonFormData(
                person=self.person_form.values(),
                state_pension=self.state_pension_form.values(),
            )
        ]
        wrappers = _owned_rows(self.wrappers, 0)
        db_pensions = _owned_rows(self.db_pensions, 0)
        annuity_purchases = _owned_rows(self.annuity_purchases, 0)
        if self._partner_active:
            persons.append(
                PersonFormData(
                    person=self.partner_form.values(),
                    state_pension=self.partner_state_pension_form.values(),
                )
            )
            wrappers += _owned_rows(self.partner_wrappers, 1)
            db_pensions += _owned_rows(self.partner_db_pensions, 1)
            annuity_purchases += _owned_rows(self.partner_annuity_purchases, 1)
        return FactsFormData(
            persons=tuple(persons),
            spending=self.spending_form.values(),
            retirement_income=self.retirement_income_form.values(),
            wrappers=wrappers,
            db_pensions=db_pensions,
            annuity_purchases=annuity_purchases,
        )

    def set_form_data(self, data: FactsFormData) -> None:
        """Replace the whole form's raw text (e.g. the launch example)."""
        self._syncing_income = True
        try:
            first = data.persons[0] if data.persons else PersonFormData()
            self.person_form.clear()
            self.person_form.set_values(first.person)
            self.spending_form.clear()
            self.spending_form.set_values(data.spending)
            self.retirement_income_form.clear()
            self.retirement_income_form.set_values(data.retirement_income)
            self.state_pension_form.clear()
            self.state_pension_form.set_values(first.state_pension)
            self._set_partner_active(active=len(data.persons) > 1)
            self.partner_form.clear()
            self.partner_state_pension_form.clear()
            if self._partner_active:
                second = data.persons[1]
                self.partner_form.set_values(second.person)
                self.partner_state_pension_form.set_values(second.state_pension)
            wrappers, partner_wrappers = _partner_rows(data.wrappers)
            db_pensions, partner_db_pensions = _partner_rows(data.db_pensions)
            annuities, partner_annuities = _partner_rows(data.annuity_purchases)
            self.wrappers.set_values_list(wrappers)
            self.db_pensions.set_values_list(db_pensions)
            self.annuity_purchases.set_values_list(annuities)
            self.partner_wrappers.set_values_list(partner_wrappers)
            self.partner_db_pensions.set_values_list(partner_db_pensions)
            self.partner_annuity_purchases.set_values_list(partner_annuities)
        finally:
            self._syncing_income = False
        self._apply_view()

    def set_entity_ids(self, ids: PlanEntityIds) -> None:
        """Seed every repeatable row's entity id from the saved plan.

        Called after a successful save so rows the user typed fresh
        adopt their minted ids — the next resubmission then edits the
        same entities instead of minting again (§4.3). The ids arrive
        first person's rows first, the order :func:`form_data` submits,
        so they split between the sections by row count.
        """
        mine = len(self.wrappers.forms)
        self.wrappers.set_entity_ids(ids.wrappers[:mine])
        self.partner_wrappers.set_entity_ids(ids.wrappers[mine:])
        mine = len(self.db_pensions.forms)
        self.db_pensions.set_entity_ids(ids.db_pensions[:mine])
        self.partner_db_pensions.set_entity_ids(ids.db_pensions[mine:])
        mine = len(self.annuity_purchases.forms)
        self.annuity_purchases.set_entity_ids(ids.annuity_purchases[:mine])
        self.partner_annuity_purchases.set_entity_ids(ids.annuity_purchases[mine:])

    def _all_section_forms(self) -> tuple[SectionForm, ...]:
        """Every live section form, singular and repeatable rows alike."""
        forms = [
            self.person_form,
            self.spending_form,
            self.retirement_income_form,
            self.state_pension_form,
            self.partner_form,
            self.partner_state_pension_form,
        ]
        for repeatable in (
            self.wrappers,
            self.db_pensions,
            self.annuity_purchases,
            self.partner_wrappers,
            self.partner_db_pensions,
            self.partner_annuity_purchases,
        ):
            forms.extend(repeatable.forms)
        return tuple(forms)

    def _section_form(self, section: str, index: int | None) -> SectionForm | None:
        """The form widget an error addresses, or ``None`` off the pane."""
        singles: dict[str, SectionForm] = {
            "person": self.person_form,
            "spending": self.spending_form,
            "retirement_income": self.retirement_income_form,
            "state_pension": self.state_pension_form,
            "partner": self.partner_form,
            "partner_state_pension": self.partner_state_pension_form,
        }
        if section in singles:
            return singles[section]
        repeatables: dict[str, RepeatableSection] = {
            "wrapper": self.wrappers,
            "db_pension": self.db_pensions,
            "annuity_purchase": self.annuity_purchases,
            "partner_wrapper": self.partner_wrappers,
            "partner_db_pension": self.partner_db_pensions,
            "partner_annuity_purchase": self.partner_annuity_purchases,
        }
        repeatable = repeatables.get(section)
        if repeatable is None:
            return None
        forms = repeatable.forms
        position = 0 if index is None else index
        if 0 <= position < len(forms):
            return forms[position]
        return None

    def _clear_all_errors(self) -> None:
        """Hide every inline error before the next submission renders."""
        for form in self._all_section_forms():
            form.clear_errors()

    def _show_errors(self, errors: Sequence[FormError]) -> None:
        """Render each error inline and scroll the first into view (10.2)."""
        first_target: QWidget | None = None
        for error in errors:
            form = self._section_form(error.section, error.index)
            if form is None:
                continue
            form.set_error(error.field_key, error.message)
            if first_target is None:
                first_target = form.editor_or_none(error.field_key) or form
        self._apply_view()
        if first_target is not None:
            self._scroll.ensureWidgetVisible(first_target)
            first_target.setFocus()

    def submit(self) -> None:
        """Forward the submission; render its status and inline errors."""
        self._clear_all_errors()
        outcome = self._on_submit(self.form_data())
        self.status_label.setText(outcome.status)
        self._show_errors(outcome.errors)

    def clear(self) -> None:
        """Empty every section only when the callback allows the clear."""
        status = self._on_clear()
        if status is None:
            return
        self._clear_all_errors()
        self.set_form_data(FactsFormData())
        self.status_label.setText(status)


__all__ = [
    "DateEntry",
    "FactsEntryPane",
    "MoneyEntry",
    "RepeatableSection",
    "ScrollSafeComboBox",
    "SectionForm",
]
