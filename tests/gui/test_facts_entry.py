"""Facts entry widget smoke tests, offscreen (issue 8.2, §4.7).

The widgets are thin by policy, so these tests only check bindings:
spec fields become editors, raw text collects back keyed like the
specs, and repeatable sections add and remove instances.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton

from glidepath.app import FactsFormData, FieldKind, build_facts_form_view_model
from glidepath.gui.forms import (
    DateEntry,
    FactsEntryPane,
    RepeatableSection,
    ScrollSafeComboBox,
    SectionForm,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _wheel_tick(combo: QComboBox) -> QWheelEvent:
    """One downward wheel notch aimed at the middle of ``combo``."""
    centre = QPointF(combo.rect().center())
    inverted = False
    return QWheelEvent(
        centre,
        QPointF(combo.mapToGlobal(combo.rect().center())),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        inverted,
    )


class TestSectionForm:
    """One section renders one editor per field spec."""

    def test_every_field_gets_the_right_editor(self) -> None:
        """Choices become combos, dates date entries, the rest line edits."""
        spec = build_facts_form_view_model().person
        form = SectionForm(spec)
        for field in spec.fields:
            editor = form.editor(field.key)
            if field.kind is FieldKind.CHOICE:
                assert isinstance(editor, QComboBox)
            else:
                assert isinstance(editor, QLineEdit)
                assert isinstance(editor, DateEntry) == (field.kind is FieldKind.DATE)
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


class TestScrollSafeComboBox:
    """Scrolling the page over a combo must not change its value."""

    def test_choice_editors_are_scroll_safe(self) -> None:
        """Every choice field renders as the wheel-guarded combo."""
        spec = build_facts_form_view_model().person
        form = SectionForm(spec)
        for field in spec.fields:
            if field.kind is FieldKind.CHOICE:
                assert isinstance(form.editor(field.key), ScrollSafeComboBox)

    def test_unfocused_combo_passes_the_wheel_to_the_page(self) -> None:
        """Without focus the value holds and the event propagates on."""
        form = SectionForm(build_facts_form_view_model().person)
        combo = form.editor("sex_for_longevity")
        assert isinstance(combo, QComboBox)
        before = combo.currentIndex()
        event = _wheel_tick(combo)
        QApplication.sendEvent(combo, event)
        assert combo.currentIndex() == before
        assert not event.isAccepted()

    def test_clicked_combo_still_spins_with_the_wheel(self) -> None:
        """Once focused by a click, the wheel changes the selection."""
        form = SectionForm(build_facts_form_view_model().person)
        form.show()
        combo = form.editor("sex_for_longevity")
        assert isinstance(combo, QComboBox)
        combo.setFocus()
        QApplication.processEvents()
        assert combo.hasFocus()
        assert combo.count() > 1
        combo.setCurrentIndex(0)
        QApplication.sendEvent(combo, _wheel_tick(combo))
        assert combo.currentIndex() == 1


class TestDateEntry:
    """Date fields type like line edits, with a calendar assist."""

    @staticmethod
    def date_of_birth_entry() -> tuple[SectionForm, DateEntry]:
        """The person form's date-of-birth editor, with its form kept alive."""
        form = SectionForm(build_facts_form_view_model().person)
        editor = form.editor("date_of_birth")
        assert isinstance(editor, DateEntry)
        return form, editor

    def test_blank_and_typed_text_round_trip(self) -> None:
        """The entry is a plain line edit for text: blank stays blank."""
        _form, entry = self.date_of_birth_entry()
        assert entry.text() == ""
        entry.setText("1984-05-20")
        assert entry.text() == "1984-05-20"

    def test_pick_action_opens_the_calendar_on_the_typed_date(self) -> None:
        """The calendar pops seeded from parseable text."""
        _form, entry = self.date_of_birth_entry()
        entry.setText("1984-05-20")
        assert not entry.calendar.isVisible()
        entry.pick_action.trigger()
        assert entry.calendar.isVisible()
        assert entry.calendar.selectedDate() == QDate(1984, 5, 20)

    def test_unparseable_text_still_opens_the_calendar(self) -> None:
        """Garbage text cannot block the assist; the popup still opens."""
        _form, entry = self.date_of_birth_entry()
        entry.setText("20/05/1984")
        entry.pick_action.trigger()
        assert entry.calendar.isVisible()

    def test_picking_a_day_writes_iso_text_and_closes(self) -> None:
        """A calendar click lands as ISO text, exactly as if typed."""
        _form, entry = self.date_of_birth_entry()
        entry.pick_action.trigger()
        entry.calendar.clicked.emit(QDate(1991, 6, 15))
        assert entry.text() == "1991-06-15"
        assert not entry.calendar.isVisible()

    def test_keyboard_activation_also_commits(self) -> None:
        """Enter/Return in the calendar commits like a mouse click."""
        _form, entry = self.date_of_birth_entry()
        entry.pick_action.trigger()
        entry.calendar.activated.emit(QDate(1993, 2, 3))
        assert entry.text() == "1993-02-03"
        assert not entry.calendar.isVisible()


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


def _pane(
    on_submit: Callable[[FactsFormData], str] = lambda _data: "",
    on_clear: Callable[[], str] = lambda: "",
) -> FactsEntryPane:
    """A pane with no-op callbacks unless a test supplies its own."""
    return FactsEntryPane(build_facts_form_view_model(), on_submit, on_clear)


class TestFactsEntryPane:
    """The pane assembles raw text and forwards it on submit."""

    def test_submit_forwards_raw_values_and_shows_status(self) -> None:
        """Submission carries every section's raw text to the callback."""
        received: list[FactsFormData] = []

        def on_submit(data: FactsFormData) -> str:
            received.append(data)
            return "status text"

        pane = _pane(on_submit=on_submit)
        pane.person_form.set_value("date_of_birth", "1984-05-20")
        wrapper_form = pane.wrappers.add_entry()
        wrapper_form.set_value("balance", "25000")
        annuity_form = pane.annuity_purchases.add_entry()
        annuity_form.set_value("at_age", "68")
        pane.submit_button.click()
        [data] = received
        assert data.person["date_of_birth"] == "1984-05-20"
        assert data.wrappers[0]["balance"] == "25000"
        assert data.db_pensions == ()
        assert data.annuity_purchases[0]["at_age"] == "68"
        assert pane.status_label.text() == "status text"

    def test_set_form_data_populates_every_section(self) -> None:
        """Programmatic form data lands field-for-field, lists included."""
        pane = _pane()
        pane.set_form_data(
            FactsFormData(
                person={"date_of_birth": "1991-06-15"},
                spending={"annual_spending_real": "28000"},
                wrappers=({"balance": "48000"}, {"balance": "16500"}),
                annuity_purchases=({"at_age": "68", "fraction_of_pot": "0.5"},),
            )
        )
        data = pane.form_data()
        assert data.person["date_of_birth"] == "1991-06-15"
        assert data.spending["annual_spending_real"] == "28000"
        assert [values["balance"] for values in data.wrappers] == ["48000", "16500"]
        assert data.annuity_purchases[0]["fraction_of_pot"] == "0.5"

    def test_set_form_data_replaces_previous_contents(self) -> None:
        """Setting new data never merges with what was on screen."""
        pane = _pane()
        pane.person_form.set_value("employment_income", "52000")
        pane.wrappers.add_entry().set_value("balance", "111")
        pane.set_form_data(FactsFormData(person={"date_of_birth": "1991-06-15"}))
        data = pane.form_data()
        assert data.person["employment_income"] == ""
        assert data.wrappers == ()

    def test_clear_button_empties_the_form_and_shows_the_status(self) -> None:
        """The clear callback's status appears once the form is blank."""
        pane = _pane(on_clear=lambda: "cleared status")
        pane.person_form.set_value("date_of_birth", "1984-05-20")
        pane.wrappers.add_entry().set_value("balance", "25000")
        pane.clear_button.click()
        assert pane.form_data().person["date_of_birth"] == ""
        assert pane.form_data().wrappers == ()
        assert pane.status_label.text() == "cleared status"
