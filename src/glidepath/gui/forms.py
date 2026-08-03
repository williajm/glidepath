"""Facts entry widgets: thin bindings over the form specs (§4.7, 8.2).

Every label, hint, and message comes from the app layer's
:class:`~glidepath.app.FactsFormViewModel`; these widgets only render
fields, collect raw text back, and forward it to the submit callback.
"""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import FactsFormData, FactsFormViewModel, FieldKind, SectionSpec

if TYPE_CHECKING:
    from collections.abc import Callable


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
                combo = QComboBox(self)
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


class FactsEntryPane(QWidget):
    """The facts tab: all sections, a submit button, and a status line."""

    def __init__(
        self,
        view_model: FactsFormViewModel,
        on_submit: Callable[[FactsFormData], str],
        parent: QWidget | None = None,
    ) -> None:
        """Render the form and wire the submit callback."""
        super().__init__(parent)
        self._on_submit = on_submit

        intro = QLabel(view_model.intro, self)
        intro.setWordWrap(True)
        self.person_form = SectionForm(view_model.person)
        self.spending_form = SectionForm(view_model.spending)
        self.state_pension_form = SectionForm(view_model.state_pension)
        self.wrappers = RepeatableSection(view_model.wrapper)
        self.db_pensions = RepeatableSection(view_model.db_pension)
        self.submit_button = QPushButton(view_model.submit_label, self)
        self.submit_button.clicked.connect(self.submit)
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

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(self.status_label)
        layout.addWidget(self.submit_button)

    def form_data(self) -> FactsFormData:
        """The whole form's raw text as one submission."""
        return FactsFormData(
            person=self.person_form.values(),
            spending=self.spending_form.values(),
            state_pension=self.state_pension_form.values(),
            wrappers=self.wrappers.values_list(),
            db_pensions=self.db_pensions.values_list(),
        )

    def submit(self) -> None:
        """Forward the submission and show the returned status text."""
        self.status_label.setText(self._on_submit(self.form_data()))


__all__ = [
    "FactsEntryPane",
    "RepeatableSection",
    "SectionForm",
]
