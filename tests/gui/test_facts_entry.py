"""Facts entry widget smoke tests, offscreen (issue 8.2, §4.7).

The widgets are thin by policy, so these tests only check bindings:
spec fields become editors, raw text collects back keyed like the
specs, and repeatable sections add and remove instances.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from glidepath.app import (
    ENTITY_ID_KEY,
    OWNER_KEY,
    FactsFormData,
    FieldKind,
    PersonFormData,
    PlanEntityIds,
    build_facts_form_view_model,
)
from glidepath.gui import forms as forms_module
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

    def test_rows_carry_their_entity_ids_opaquely(self) -> None:
        """Seeded ids collect back per row without becoming editors."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        section.set_values_list(
            (
                {ENTITY_ID_KEY: "id-a", "balance": "1111"},
                {ENTITY_ID_KEY: "id-b", "balance": "2222"},
            )
        )
        values = section.values_list()
        assert [entry[ENTITY_ID_KEY] for entry in values] == ["id-a", "id-b"]
        [first, _second] = section.forms
        assert first.values().get(ENTITY_ID_KEY) is None
        with pytest.raises(KeyError):
            first.editor(ENTITY_ID_KEY)

    def test_owner_keys_are_ignored_rather_than_rendered(self) -> None:
        """Seeded owner stamps never become editors or collected values.

        The owner is the pane's section placement (roadmap 9.31), so a
        section fed combined rows must swallow the reserved key.
        """
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        section.set_values_list(({OWNER_KEY: "1", "balance": "1111"},))
        [values] = section.values_list()
        assert values.get(OWNER_KEY) is None
        assert values["balance"] == "1111"
        [form] = section.forms
        with pytest.raises(KeyError):
            form.editor(OWNER_KEY)

    def test_removing_a_row_removes_its_id(self) -> None:
        """The id travels with its row, never with its position (§4.3)."""
        spec = build_facts_form_view_model().wrapper
        section = RepeatableSection(spec)
        section.set_values_list(
            (
                {ENTITY_ID_KEY: "id-a", "balance": "1111"},
                {ENTITY_ID_KEY: "id-b", "balance": "2222"},
            )
        )
        remove_buttons = [
            button
            for button in section.findChildren(QPushButton)
            if button.text() == spec.remove_label
        ]
        remove_buttons[0].click()
        [remaining] = section.values_list()
        assert remaining[ENTITY_ID_KEY] == "id-b"
        assert remaining["balance"] == "2222"

    def test_added_rows_start_blank_until_reseeded(self) -> None:
        """A user-added row is a new entity until a save mints its id."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        section.add_button.click()
        [values] = section.values_list()
        assert values[ENTITY_ID_KEY] == ""
        section.set_entity_ids(("id-minted",))
        [reseeded] = section.values_list()
        assert reseeded[ENTITY_ID_KEY] == "id-minted"

    def test_set_entity_ids_requires_one_id_per_row(self) -> None:
        """A count mismatch is a programming error, not a silent skew."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        section.add_entry()
        entity_ids = ("id-a", "id-b")
        with pytest.raises(ValueError, match="one entity id per section instance"):
            section.set_entity_ids(entity_ids)


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
        [person] = data.persons
        assert person.person["date_of_birth"] == "1984-05-20"
        assert data.wrappers[0]["balance"] == "25000"
        assert data.wrappers[0][OWNER_KEY] == "0"
        assert data.db_pensions == ()
        assert data.annuity_purchases[0]["at_age"] == "68"
        assert data.annuity_purchases[0][OWNER_KEY] == "0"
        assert pane.status_label.text() == "status text"

    def test_set_form_data_populates_every_section(self) -> None:
        """Programmatic form data lands field-for-field, lists included."""
        pane = _pane()
        pane.set_form_data(
            FactsFormData(
                persons=(PersonFormData(person={"date_of_birth": "1991-06-15"}),),
                spending={"annual_spending_real": "28000"},
                wrappers=({"balance": "48000"}, {"balance": "16500"}),
                annuity_purchases=({"at_age": "68", "fraction_of_pot": "0.5"},),
            )
        )
        data = pane.form_data()
        assert data.persons[0].person["date_of_birth"] == "1991-06-15"
        assert data.spending["annual_spending_real"] == "28000"
        assert [values["balance"] for values in data.wrappers] == ["48000", "16500"]
        assert data.annuity_purchases[0]["fraction_of_pot"] == "0.5"

    def test_set_form_data_replaces_previous_contents(self) -> None:
        """Setting new data never merges with what was on screen."""
        pane = _pane()
        pane.person_form.set_value("employment_income", "52000")
        pane.wrappers.add_entry().set_value("balance", "111")
        pane.set_form_data(
            FactsFormData(
                persons=(PersonFormData(person={"date_of_birth": "1991-06-15"}),)
            )
        )
        data = pane.form_data()
        assert data.persons[0].person["employment_income"] == ""
        assert data.wrappers == ()

    def test_set_entity_ids_seeds_every_repeatable_section(self) -> None:
        """The saved plan's minted ids land on each repeatable section."""
        pane = _pane()
        pane.wrappers.add_entry()
        pane.annuity_purchases.add_entry()
        pane.set_entity_ids(
            PlanEntityIds(wrappers=("id-w",), annuity_purchases=("id-a",))
        )
        data = pane.form_data()
        assert data.wrappers[0][ENTITY_ID_KEY] == "id-w"
        assert data.annuity_purchases[0][ENTITY_ID_KEY] == "id-a"
        assert data.db_pensions == ()

    def test_clear_button_empties_the_form_and_shows_the_status(self) -> None:
        """The clear callback's status appears once the form is blank."""
        pane = _pane(on_clear=lambda: "cleared status")
        pane.person_form.set_value("date_of_birth", "1984-05-20")
        pane.wrappers.add_entry().set_value("balance", "25000")
        pane.clear_button.click()
        assert pane.form_data().persons[0].person["date_of_birth"] == ""
        assert pane.form_data().wrappers == ()
        assert pane.status_label.text() == "cleared status"


def _message_box_answering(choice: QMessageBox.StandardButton) -> SimpleNamespace:
    """A QMessageBox stand-in whose question always answers ``choice``."""
    return SimpleNamespace(
        StandardButton=QMessageBox.StandardButton,
        question=lambda *_args: choice,
    )


def _pane_with_partner_entries() -> FactsEntryPane:
    """A pane with values and one row typed under each person."""
    pane = _pane()
    pane.person_form.set_value("date_of_birth", "1984-05-20")
    pane.wrappers.add_entry().set_value("balance", "1111")
    pane.add_partner()
    pane.partner_form.set_value("date_of_birth", "1986-03-02")
    pane.partner_state_pension_form.set_value("forecast_weekly_amount", "230.25")
    pane.partner_wrappers.add_entry().set_value("balance", "2222")
    return pane


class TestFactsEntryPanePartner:
    """The optional partner sections and their owner routing (9.31)."""

    def test_the_partner_starts_off_the_form(self) -> None:
        """No partner until asked: sections hidden, add action shown."""
        pane = _pane()
        assert not pane.has_partner
        assert pane.add_partner_button.isVisibleTo(pane)
        assert not pane.partner_form.isVisibleTo(pane)
        assert not pane.partner_wrappers.isVisibleTo(pane)
        data = pane.form_data()
        assert len(data.persons) == 1

    def test_add_partner_shows_the_sections(self) -> None:
        """The one explicit action swaps the button for the sections."""
        pane = _pane()
        pane.add_partner()
        assert pane.has_partner
        assert not pane.add_partner_button.isVisibleTo(pane)
        assert pane.partner_form.isVisibleTo(pane)
        assert pane.partner_state_pension_form.isVisibleTo(pane)
        assert pane.partner_wrappers.isVisibleTo(pane)
        assert pane.remove_partner_button.isVisibleTo(pane)

    def test_partner_entries_land_in_form_data(self) -> None:
        """Two persons submit, partner rows owner-stamped after mine."""
        pane = _pane_with_partner_entries()
        data = pane.form_data()
        assert len(data.persons) == 2
        assert data.persons[1].person["date_of_birth"] == "1986-03-02"
        assert data.persons[1].state_pension["forecast_weekly_amount"] == "230.25"
        owners = [(row[OWNER_KEY], row["balance"]) for row in data.wrappers]
        assert owners == [("0", "1111"), ("1", "2222")]

    def test_set_form_data_routes_rows_by_owner(self) -> None:
        """A two-person payload activates the partner and splits rows."""
        pane = _pane()
        pane.set_form_data(
            FactsFormData(
                persons=(
                    PersonFormData(person={"date_of_birth": "1984-05-20"}),
                    PersonFormData(
                        person={"date_of_birth": "1986-03-02"},
                        state_pension={"forecast_weekly_amount": "230.25"},
                    ),
                ),
                wrappers=(
                    {"balance": "1111"},
                    {OWNER_KEY: "1", "balance": "2222"},
                ),
            )
        )
        assert pane.has_partner
        assert pane.partner_form.values()["date_of_birth"] == "1986-03-02"
        partner_pension = pane.partner_state_pension_form.values()
        assert partner_pension["forecast_weekly_amount"] == "230.25"
        [mine] = pane.wrappers.values_list()
        assert mine["balance"] == "1111"
        [theirs] = pane.partner_wrappers.values_list()
        assert theirs["balance"] == "2222"

    def test_set_form_data_with_one_person_deactivates_the_partner(self) -> None:
        """A single-person payload takes the partner off the form."""
        pane = _pane_with_partner_entries()
        payload = FactsFormData(
            persons=(PersonFormData(person={"date_of_birth": "1984-05-20"}),)
        )
        pane.set_form_data(payload)
        assert not pane.has_partner
        assert pane.partner_form.values()["date_of_birth"] == ""
        assert pane.partner_wrappers.values_list() == ()

    def test_clear_empties_the_partner_sections(self) -> None:
        """The clear button leaves no partner residue behind."""
        pane = _pane_with_partner_entries()
        pane.clear_button.click()
        assert not pane.has_partner
        assert pane.partner_form.values()["date_of_birth"] == ""
        assert pane.partner_state_pension_form.values()["forecast_weekly_amount"] == ""
        assert pane.partner_wrappers.values_list() == ()
        assert len(pane.form_data().persons) == 1

    def test_remove_partner_deletes_their_values_and_rows(self) -> None:
        """Removal empties the partner sections and hides them again."""
        pane = _pane_with_partner_entries()
        pane.remove_partner()
        assert not pane.has_partner
        assert pane.add_partner_button.isVisibleTo(pane)
        assert pane.partner_form.values()["date_of_birth"] == ""
        assert pane.partner_wrappers.values_list() == ()
        data = pane.form_data()
        assert len(data.persons) == 1
        assert [row["balance"] for row in data.wrappers] == ["1111"]

    def test_remove_button_confirms_before_deleting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering Yes to the prompt removes the partner."""
        pane = _pane_with_partner_entries()
        monkeypatch.setattr(
            forms_module,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Yes),
        )
        pane.remove_partner_button.click()
        assert not pane.has_partner
        assert pane.partner_wrappers.values_list() == ()

    def test_declining_the_confirm_keeps_the_partner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering No leaves the partner and their rows in place."""
        pane = _pane_with_partner_entries()
        monkeypatch.setattr(
            forms_module,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.No),
        )
        pane.remove_partner_button.click()
        assert pane.has_partner
        assert pane.partner_form.values()["date_of_birth"] == "1986-03-02"
        [row] = pane.partner_wrappers.values_list()
        assert row["balance"] == "2222"

    def test_set_entity_ids_splits_the_combined_ids_by_row_count(self) -> None:
        """Ids arrive mine-first and land on the owning sections."""
        pane = _pane()
        pane.wrappers.add_entry()
        pane.wrappers.add_entry()
        pane.add_partner()
        pane.partner_wrappers.add_entry()
        pane.partner_annuity_purchases.add_entry()
        pane.set_entity_ids(
            PlanEntityIds(
                wrappers=("id-w0", "id-w1", "id-pw0"),
                annuity_purchases=("id-pa0",),
            )
        )
        data = pane.form_data()
        assert [row[ENTITY_ID_KEY] for row in data.wrappers] == [
            "id-w0",
            "id-w1",
            "id-pw0",
        ]
        assert [row[ENTITY_ID_KEY] for row in data.annuity_purchases] == ["id-pa0"]
