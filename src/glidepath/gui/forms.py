"""Facts entry widgets: thin bindings over the form specs (§4.7, 8.2).

Every label, hint, and message comes from the app layer's
:class:`~glidepath.app.FactsFormViewModel`; these widgets only render
fields, collect raw text back, and forward it to the submit callback.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import FactsFormData, FactsFormViewModel, FieldKind, SectionSpec

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
                edit = QLineEdit(self)
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
    """A list of :class:`SectionForm` instances with add/remove controls."""

    def __init__(self, spec: SectionSpec, parent: QWidget | None = None) -> None:
        """Render the empty list with its add button."""
        super().__init__(parent)
        self._spec = spec
        self._entries: list[QWidget] = []
        self._forms: list[SectionForm] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout = QVBoxLayout()
        layout.addLayout(self._list_layout)
        self.add_button = QPushButton(spec.add_label, self)
        self.add_button.clicked.connect(self.add_entry)
        layout.addWidget(self.add_button)

    def add_entry(self) -> SectionForm:
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
        return form

    def _remove_entry(self, container: QWidget) -> None:
        """Drop one section instance from the list."""
        index = self._entries.index(container)
        del self._entries[index]
        del self._forms[index]
        self._list_layout.removeWidget(container)
        container.deleteLater()

    @property
    def forms(self) -> tuple[SectionForm, ...]:
        """The live section instances, in display order."""
        return tuple(self._forms)

    def values_list(self) -> tuple[dict[str, str], ...]:
        """Raw text for every instance, in display order."""
        return tuple(form.values() for form in self._forms)

    def set_values_list(self, values_list: Sequence[Mapping[str, str]]) -> None:
        """Replace every instance with one per entry of ``values_list``."""
        self.clear()
        for values in values_list:
            self.add_entry().set_values(values)

    def clear(self) -> None:
        """Drop every section instance."""
        for container in tuple(self._entries):
            self._remove_entry(container)


class FactsEntryPane(QWidget):
    """The facts tab: sections, submit and clear buttons, a status line."""

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

        intro = QLabel(view_model.intro, self)
        intro.setWordWrap(True)
        self.person_form = SectionForm(view_model.person)
        self.spending_form = SectionForm(view_model.spending)
        self.state_pension_form = SectionForm(view_model.state_pension)
        self.wrappers = RepeatableSection(view_model.wrapper)
        self.db_pensions = RepeatableSection(view_model.db_pension)
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

    def form_data(self) -> FactsFormData:
        """The whole form's raw text as one submission."""
        return FactsFormData(
            person=self.person_form.values(),
            spending=self.spending_form.values(),
            state_pension=self.state_pension_form.values(),
            wrappers=self.wrappers.values_list(),
            db_pensions=self.db_pensions.values_list(),
        )

    def set_form_data(self, data: FactsFormData) -> None:
        """Replace the whole form's raw text (e.g. the launch example)."""
        self.person_form.clear()
        self.person_form.set_values(data.person)
        self.spending_form.clear()
        self.spending_form.set_values(data.spending)
        self.state_pension_form.clear()
        self.state_pension_form.set_values(data.state_pension)
        self.wrappers.set_values_list(data.wrappers)
        self.db_pensions.set_values_list(data.db_pensions)

    def submit(self) -> None:
        """Forward the submission and show the returned status text."""
        self.status_label.setText(self._on_submit(self.form_data()))

    def clear(self) -> None:
        """Empty every section, then show the clear callback's status."""
        self.set_form_data(FactsFormData())
        self.status_label.setText(self._on_clear())


__all__ = [
    "FactsEntryPane",
    "RepeatableSection",
    "ScrollSafeComboBox",
    "SectionForm",
]
