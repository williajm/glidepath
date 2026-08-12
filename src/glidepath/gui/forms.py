"""Facts entry widgets: thin bindings over the form specs (§4.7, 8.2).

Every label, hint, and message comes from the app layer's
:class:`~glidepath.app.FactsFormViewModel`; these widgets only render
fields, collect raw text back, and forward it to the submit callback.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
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
    QStyle,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import (
    DATE_PICKER_TOOLTIP,
    ENTITY_ID_KEY,
    OWNER_KEY,
    FactsFormData,
    FactsFormViewModel,
    FieldKind,
    PersonFormData,
    PlanEntityIds,
    SectionSpec,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from PySide6.QtGui import QWheelEvent


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
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown),
            QLineEdit.ActionPosition.TrailingPosition,
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
    """One titled group of fields rendered from a :class:`SectionSpec`."""

    def __init__(self, spec: SectionSpec, parent: QWidget | None = None) -> None:
        """Render the section's fields as labelled editors."""
        super().__init__(spec.title, parent)
        self._editors: dict[str, QLineEdit | QComboBox] = {}
        layout = QFormLayout(self)
        if spec.description:
            description = QLabel(spec.description, self)
            description.setWordWrap(True)
            layout.addRow(description)
        for field in spec.fields:
            if field.kind is FieldKind.CHOICE:
                combo = ScrollSafeComboBox(self)
                for option in field.choices:
                    combo.addItem(option.label, option.value)
                self._editors[field.key] = combo
                layout.addRow(field.label, combo)
            else:
                edit = (
                    DateEntry(self) if field.kind is FieldKind.DATE else QLineEdit(self)
                )
                edit.setPlaceholderText(field.hint)
                self._editors[field.key] = edit
                layout.addRow(field.label, edit)

    def editor(self, key: str) -> QLineEdit | QComboBox:
        """The editor widget for ``key`` (tests and shells drive it)."""
        return self._editors[key]

    def set_value(self, key: str, value: str) -> None:
        """Set one field's raw value programmatically."""
        editor = self._editors[key]
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(editor.findData(value))
        else:
            editor.setText(value)

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

    def clear(self) -> None:
        """Blank every text field and reset every choice to its first option."""
        for editor in self._editors.values():
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
            else:
                editor.setText("")


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
        self._entries: list[QWidget] = []
        self._forms: list[SectionForm] = []
        self._entity_ids: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout = QVBoxLayout()
        layout.addLayout(self._list_layout)
        self.add_button = QPushButton(spec.add_label, self)
        self.add_button.clicked.connect(self.add_entry)
        layout.addWidget(self.add_button)

    def add_entry(self, *, entity_id: str = "") -> SectionForm:
        """Append one more section instance and return its form."""
        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        form = SectionForm(self._spec, container)
        remove = QPushButton(self._spec.remove_label, container)
        remove.clicked.connect(lambda: self._remove_entry(container))
        container_layout.addWidget(form)
        container_layout.addWidget(remove)
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
    """

    def __init__(
        self,
        view_model: FactsFormViewModel,
        on_submit: Callable[[FactsFormData], str],
        on_clear: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        """Render the form and wire the submit and clear callbacks."""
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_clear = on_clear
        self._remove_partner_title = view_model.remove_partner_label
        self._remove_partner_confirm = view_model.remove_partner_confirm
        self._partner_active = False

        intro = QLabel(view_model.intro, self)
        intro.setWordWrap(True)
        self.person_form = SectionForm(view_model.person)
        self.spending_form = SectionForm(view_model.spending)
        self.state_pension_form = SectionForm(view_model.state_pension)
        self.wrappers = RepeatableSection(view_model.wrapper)
        self.db_pensions = RepeatableSection(view_model.db_pension)
        self.annuity_purchases = RepeatableSection(view_model.annuity_purchase)
        self._build_partner_sections(view_model)
        self.submit_button = QPushButton(view_model.submit_label, self)
        self.submit_button.setObjectName("primaryButton")
        self.submit_button.clicked.connect(self.submit)
        self.clear_button = QPushButton(view_model.clear_label, self)
        self.clear_button.clicked.connect(self.clear)
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(self.person_form)
        content_layout.addWidget(self.spending_form)
        content_layout.addWidget(self.state_pension_form)
        content_layout.addWidget(self.wrappers)
        content_layout.addWidget(self.db_pensions)
        content_layout.addWidget(self.annuity_purchases)
        content_layout.addWidget(self.add_partner_button)
        content_layout.addWidget(self._partner_group)
        content_layout.addStretch()

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        buttons = QWidget(self)
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.addWidget(self.clear_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.submit_button)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
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
        partner_layout.addWidget(self.partner_annuity_purchases)
        partner_layout.addWidget(self.remove_partner_button)
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
        """Swap between the add-partner action and the partner sections."""
        self._partner_active = active
        self._partner_group.setVisible(active)
        self.add_partner_button.setVisible(not active)

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
            wrappers=wrappers,
            db_pensions=db_pensions,
            annuity_purchases=annuity_purchases,
        )

    def set_form_data(self, data: FactsFormData) -> None:
        """Replace the whole form's raw text (e.g. the launch example)."""
        first = data.persons[0] if data.persons else PersonFormData()
        self.person_form.clear()
        self.person_form.set_values(first.person)
        self.spending_form.clear()
        self.spending_form.set_values(data.spending)
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

    def submit(self) -> None:
        """Forward the submission and show the returned status text."""
        self.status_label.setText(self._on_submit(self.form_data()))

    def clear(self) -> None:
        """Empty every section, then show the clear callback's status."""
        self.set_form_data(FactsFormData())
        self.status_label.setText(self._on_clear())


__all__ = [
    "DateEntry",
    "FactsEntryPane",
    "RepeatableSection",
    "ScrollSafeComboBox",
    "SectionForm",
]
