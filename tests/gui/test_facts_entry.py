"""Facts entry widget smoke tests, offscreen (issue 8.2, §4.7).

The widgets are thin by policy, so these tests only check bindings:
spec fields become editors, raw text collects back keyed like the
specs, and repeatable sections add and remove instances.
"""

from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

from glidepath.app import FactsFormData, FieldKind, build_facts_form_view_model
from glidepath.gui.forms import FactsEntryPane, RepeatableSection, SectionForm


class TestSectionForm:
    """One section renders one editor per field spec."""

    def test_every_field_gets_the_right_editor(self) -> None:
        """Choices become combos; everything else is a line edit."""
        spec = build_facts_form_view_model().person
        form = SectionForm(spec)
        for field in spec.fields:
            editor = form.editor(field.key)
            if field.kind is FieldKind.CHOICE:
                assert isinstance(editor, QComboBox)
            else:
                assert isinstance(editor, QLineEdit)
                assert editor.placeholderText() == field.hint

    def test_values_default_to_blank_text_and_first_choice(self) -> None:
        """An untouched form returns blank text and each first option."""
        spec = build_facts_form_view_model().person
        values = SectionForm(spec).values()
        assert values["date_of_birth"] == ""
        first_residency = (
            next(field for field in spec.fields if field.key == "tax_residency")
            .choices[0]
            .value
        )
        assert values["tax_residency"] == first_residency

    def test_set_value_round_trips(self) -> None:
        """Programmatic values collect back for text and choice fields."""
        form = SectionForm(build_facts_form_view_model().person)
        form.set_value("date_of_birth", "1984-05-20")
        form.set_value("sex_for_longevity", "female")
        values = form.values()
        assert values["date_of_birth"] == "1984-05-20"
        assert values["sex_for_longevity"] == "female"


class TestRepeatableSection:
    """Wrappers and DB pensions are lists of section instances."""

    def test_starts_empty(self) -> None:
        """No instances until the user adds one."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        assert section.values_list() == ()

    def test_add_button_appends_an_instance(self) -> None:
        """The add button creates one more titled sub-form."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        section.add_button.click()
        assert len(section.forms) == 1
        assert len(section.values_list()) == 1

    def test_remove_button_drops_its_instance(self) -> None:
        """Each instance's remove button removes exactly that instance."""
        spec = build_facts_form_view_model().wrapper
        section = RepeatableSection(spec)
        first = section.add_entry()
        second = section.add_entry()
        first.set_value("balance", "1111")
        second.set_value("balance", "2222")
        remove_buttons = [
            button
            for button in section.findChildren(QPushButton)
            if button.text() == spec.remove_label
        ]
        assert len(remove_buttons) == 2
        remove_buttons[0].click()
        [(remaining)] = section.values_list()
        assert remaining["balance"] == "2222"


class TestFactsEntryPane:
    """The pane assembles raw text and forwards it on submit."""

    def test_submit_forwards_raw_values_and_shows_status(self) -> None:
        """Submission carries every section's raw text to the callback."""
        received: list[FactsFormData] = []

        def on_submit(data: FactsFormData) -> str:
            received.append(data)
            return "status text"

        pane = FactsEntryPane(build_facts_form_view_model(), on_submit)
        pane.person_form.set_value("date_of_birth", "1984-05-20")
        wrapper_form = pane.wrappers.add_entry()
        wrapper_form.set_value("balance", "25000")
        pane.submit_button.click()
        [data] = received
        assert data.person["date_of_birth"] == "1984-05-20"
        assert data.wrappers[0]["balance"] == "25000"
        assert data.db_pensions == ()
        assert pane.status_label.text() == "status text"
