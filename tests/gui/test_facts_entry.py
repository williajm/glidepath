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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QWidget,
)

from glidepath.app import (
    ENTITY_ID_KEY,
    HIDE_ADVANCED_LABEL,
    HIDE_DETAILS_LABEL,
    INCOME_PREFERENCE_ANNUITY,
    INCOME_PREFERENCE_KEY,
    OWNER_KEY,
    REQUIRED_MARKER,
    SHOW_ADVANCED_LABEL,
    SHOW_DETAILS_LABEL,
    STEP_ERROR_SUFFIX,
    FactsFormData,
    FactsSubmissionOutcome,
    FieldKind,
    FormError,
    FormView,
    PersonFormData,
    PlanEntityIds,
    build_facts_form_view_model,
)
from glidepath.gui import forms as forms_module
from glidepath.gui.forms import (
    DateEntry,
    FactsEntryPane,
    MoneyEntry,
    RepeatableSection,
    ScrollSafeComboBox,
    SectionForm,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _shown(pane: FactsEntryPane, widget: QWidget) -> bool:
    """Whether ``widget`` shows on its own step's page (roadmap 11.2).

    Each step is a page of its own, so a section on another step is
    out of sight without being hidden; this asks the question the
    visibility tests mean: would the user see it on its step?
    """
    for page in pane.step_pages.findChildren(QScrollArea):
        if page.isAncestorOf(widget):
            return widget.isVisibleTo(page)
    return widget.isVisibleTo(pane)


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
        """Choices become combos, dates date entries, amounts money entries."""
        spec = build_facts_form_view_model().person
        form = SectionForm(spec)
        for field in spec.fields:
            editor = form.editor(field.key)
            if field.kind is FieldKind.CHOICE:
                assert isinstance(editor, QComboBox)
            else:
                assert isinstance(editor, QLineEdit)
                assert isinstance(editor, DateEntry) == (field.kind is FieldKind.DATE)
                assert isinstance(editor, MoneyEntry) == (field.kind is FieldKind.MONEY)

    def test_short_entries_show_their_hint_beside_them(self) -> None:
        """Amounts, numbers and dates carry the hint as text, not placeholder.

        A short entry would clip a long placeholder (roadmap 11.3); the
        free-text entries keep theirs.
        """
        spec = build_facts_form_view_model().wrapper
        form = SectionForm(spec)
        hints = {
            label.text()
            for label in form.findChildren(QLabel)
            if label.objectName() == "fieldHint"
        }
        for field in spec.fields:
            editor = form.editor(field.key)
            if not isinstance(editor, QLineEdit) or not field.hint:
                continue
            if field.kind is FieldKind.TEXT:
                assert editor.placeholderText() == field.hint
            else:
                assert editor.placeholderText() == ""
                assert field.hint in hints
            assert editor.toolTip() == field.hint

    def test_short_entries_are_narrower_than_free_text(self) -> None:
        """Amounts are sized to what they hold, not the window."""
        form = SectionForm(build_facts_form_view_model().wrapper)
        assert form.editor("balance").maximumWidth() < (
            form.editor("label").maximumWidth()
        )

    def test_money_entries_show_separators_and_parse_back(self) -> None:
        """An amount shows as 48,000 with a £ prefix; leaving re-formats."""
        form = SectionForm(build_facts_form_view_model().wrapper)
        balance = form.editor("balance")
        assert isinstance(balance, MoneyEntry)
        assert balance.prefix.text() == "£"
        form.set_value("balance", "48000")
        assert form.values()["balance"] == "48,000"
        balance.setText("1234.5")
        balance.editingFinished.emit()
        assert balance.text() == "1,234.5"
        balance.setText("not money")
        balance.editingFinished.emit()
        assert balance.text() == "not money"

    def test_the_summary_hides_the_full_description(self) -> None:
        """One line shows; the toggle reveals the rest (roadmap 11.3)."""
        spec = build_facts_form_view_model().wrapper
        form = SectionForm(spec)
        assert spec.summary
        assert not form.description_label.isVisibleTo(form)
        form.toggle_details()
        assert form.description_label.isVisibleTo(form)
        assert form.details_toggle.text() == HIDE_DETAILS_LABEL
        form.toggle_details()
        assert not form.description_label.isVisibleTo(form)
        assert form.details_toggle.text() == SHOW_DETAILS_LABEL

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
        assert remaining["balance"] == "2,222"

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
        assert values["balance"] == "1,111"
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
        assert remaining["balance"] == "2,222"

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
    on_submit: Callable[[FactsFormData], FactsSubmissionOutcome] = (
        lambda _data: FactsSubmissionOutcome(status="")
    ),
    on_clear: Callable[[], str] = lambda: "",
) -> FactsEntryPane:
    """A pane with no-op callbacks unless a test supplies its own."""
    return FactsEntryPane(build_facts_form_view_model(), on_submit, on_clear)


class TestFactsEntryPane:
    """The pane assembles raw text and forwards it on submit."""

    def test_submit_forwards_raw_values_and_shows_status(self) -> None:
        """Submission carries every section's raw text to the callback."""
        received: list[FactsFormData] = []

        def on_submit(data: FactsFormData) -> FactsSubmissionOutcome:
            received.append(data)
            return FactsSubmissionOutcome(status="status text")

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
        assert data.wrappers[0]["balance"] == "25,000"
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
                annuity_purchases=({"at_age": "68", "percent_of_pot": "50"},),
            )
        )
        data = pane.form_data()
        assert data.persons[0].person["date_of_birth"] == "1991-06-15"
        assert data.spending["annual_spending_real"] == "28,000"
        assert [values["balance"] for values in data.wrappers] == ["48,000", "16,500"]
        assert data.annuity_purchases[0]["percent_of_pot"] == "50"

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
        assert _shown(pane, pane.add_partner_button)
        assert not _shown(pane, pane.partner_form)
        assert not _shown(pane, pane.partner_wrappers)
        data = pane.form_data()
        assert len(data.persons) == 1

    def test_add_partner_shows_the_sections(self) -> None:
        """The one explicit action swaps the button for the sections."""
        pane = _pane()
        pane.add_partner()
        assert pane.has_partner
        assert not _shown(pane, pane.add_partner_button)
        assert _shown(pane, pane.partner_form)
        assert _shown(pane, pane.partner_state_pension_form)
        assert _shown(pane, pane.partner_wrappers)
        assert _shown(pane, pane.remove_partner_button)

    def test_partner_entries_land_in_form_data(self) -> None:
        """Two persons submit, partner rows owner-stamped after mine."""
        pane = _pane_with_partner_entries()
        data = pane.form_data()
        assert len(data.persons) == 2
        assert data.persons[1].person["date_of_birth"] == "1986-03-02"
        assert data.persons[1].state_pension["forecast_weekly_amount"] == "230.25"
        owners = [(row[OWNER_KEY], row["balance"]) for row in data.wrappers]
        assert owners == [("0", "1,111"), ("1", "2,222")]

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
        assert mine["balance"] == "1,111"
        [theirs] = pane.partner_wrappers.values_list()
        assert theirs["balance"] == "2,222"

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
        assert _shown(pane, pane.add_partner_button)
        assert pane.partner_form.values()["date_of_birth"] == ""
        assert pane.partner_wrappers.values_list() == ()
        data = pane.form_data()
        assert len(data.persons) == 1
        assert [row["balance"] for row in data.wrappers] == ["1,111"]

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
        assert row["balance"] == "2,222"

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


class TestAdvancedDisclosure:
    """Rarely needed fields sit behind "More options" (roadmap 10.1)."""

    @staticmethod
    def person_form() -> SectionForm:
        """The person section — it carries advanced fields."""
        return SectionForm(build_facts_form_view_model().person)

    def test_advanced_fields_start_hidden_behind_the_toggle(self) -> None:
        """The advanced sub-form is tucked away and the toggle offers it."""
        form = self.person_form()
        assert not form.advanced_visible
        assert form.advanced_toggle.text() == SHOW_ADVANCED_LABEL
        assert form.advanced_toggle.isVisibleTo(form)

    def test_the_toggle_discloses_and_tucks_away(self) -> None:
        """Clicking flips the sub-form and the toggle's own label."""
        form = self.person_form()
        form.advanced_toggle.click()
        disclosed = form.advanced_visible
        assert disclosed
        assert form.advanced_toggle.text() == HIDE_ADVANCED_LABEL
        form.advanced_toggle.click()
        tucked_away = not form.advanced_visible
        assert tucked_away
        assert form.advanced_toggle.text() == SHOW_ADVANCED_LABEL

    def test_a_section_without_advanced_fields_hides_the_toggle(self) -> None:
        """No disclosure is offered where there is nothing to disclose."""
        form = SectionForm(build_facts_form_view_model().retirement_income)
        assert not form.advanced_toggle.isVisibleTo(form)

    def test_loading_a_value_into_an_advanced_field_reveals_it(self) -> None:
        """A populated advanced field must never stay hidden."""
        form = self.person_form()
        form.set_value("death_age", "82")
        assert form.advanced_visible

    def test_clear_tucks_the_advanced_fields_away_again(self) -> None:
        """A cleared section returns to its compact rendering."""
        form = self.person_form()
        form.set_value("death_age", "82")
        form.clear()
        assert not form.advanced_visible

    def test_blank_values_do_not_reveal(self) -> None:
        """Loading blanks (the common case) keeps the compact view."""
        form = self.person_form()
        form.set_values({"death_age": "", "lsa_used": ""})
        assert not form.advanced_visible


class TestRequiredMarkersAndTooltips:
    """Required labels are flagged; hints survive typing (10.2, 10.4)."""

    def test_required_fields_carry_the_marker_on_their_labels(self) -> None:
        """The date of birth is required; its rendered label says so."""
        spec = build_facts_form_view_model().person
        form = SectionForm(spec)
        labels = {label.text() for label in form.findChildren(QLabel)}
        dob = next(field for field in spec.fields if field.key == "date_of_birth")
        income = next(
            field for field in spec.fields if field.key == "employment_income"
        )
        assert f"{dob.label} {REQUIRED_MARKER}" in labels
        assert income.label in labels

    def test_every_hinted_editor_carries_its_hint_as_a_tooltip(self) -> None:
        """Placeholder guidance stays reachable after the user types."""
        spec = build_facts_form_view_model().person
        form = SectionForm(spec)
        for field in spec.fields:
            if field.hint:
                assert form.editor(field.key).toolTip() == field.hint


def _error_labels_showing(form: SectionForm) -> list[str]:
    """The texts of the form's visible inline error labels."""
    return [
        label.text()
        for label in form.findChildren(QLabel)
        if label.objectName() == "fieldErrorLabel" and label.isVisibleTo(form)
    ]


class TestInlineErrors:
    """Submission errors land under their fields (roadmap 10.2)."""

    @staticmethod
    def rejecting_pane(errors: tuple[FormError, ...]) -> FactsEntryPane:
        """A pane whose submit callback always reports ``errors``."""
        return _pane(
            on_submit=lambda _data: FactsSubmissionOutcome(
                status="fix the errors", errors=errors
            )
        )

    def test_an_error_renders_under_its_field(self) -> None:
        """The message shows inline on the addressed section."""
        error = FormError("person", None, "date_of_birth", "this field is required")
        pane = self.rejecting_pane((error,))
        pane.submit_button.click()
        assert _error_labels_showing(pane.person_form) == ["this field is required"]
        assert pane.status_label.text() == "fix the errors"

    def test_an_error_on_a_repeatable_row_lands_on_that_row(self) -> None:
        """The row index routes the message to the right instance."""
        pane = self.rejecting_pane((FormError("wrapper", 1, "balance", "bad amount"),))
        pane.wrappers.add_entry()
        pane.wrappers.add_entry()
        pane.submit_button.click()
        first, second = pane.wrappers.forms
        assert _error_labels_showing(first) == []
        assert _error_labels_showing(second) == ["bad amount"]

    def test_an_error_on_an_advanced_field_reveals_it(self) -> None:
        """A hidden error would read as the app ignoring the save."""
        error = FormError("person", None, "death_age", "enter a whole number")
        pane = self.rejecting_pane((error,))
        pane.submit_button.click()
        assert pane.person_form.advanced_visible

    def test_a_sectionwide_error_uses_the_section_label(self) -> None:
        """An empty field key errors the section as a whole."""
        pane = self.rejecting_pane((FormError("person", None, "", "whole-form no"),))
        pane.submit_button.click()
        label = pane.person_form.section_error_label
        assert label.isVisibleTo(pane.person_form)
        assert label.text() == "whole-form no"

    def test_the_next_submission_clears_previous_errors(self) -> None:
        """A clean resubmission leaves no stale inline messages."""
        outcomes = [
            FactsSubmissionOutcome(
                status="fix",
                errors=(FormError("person", None, "date_of_birth", "required"),),
            ),
            FactsSubmissionOutcome(status="saved"),
        ]
        pane = _pane(on_submit=lambda _data: outcomes.pop(0))
        pane.submit_button.click()
        assert _error_labels_showing(pane.person_form) == ["required"]
        pane.submit_button.click()
        assert _error_labels_showing(pane.person_form) == []
        assert pane.status_label.text() == "saved"

    def test_an_error_off_the_pane_is_status_only(self) -> None:
        """An unaddressable error never crashes the rendering."""
        pane = self.rejecting_pane((FormError("wrapper", 3, "balance", "no row"),))
        pane.submit_button.click()
        assert pane.status_label.text() == "fix the errors"


class TestIncomePreference:
    """The annuity sections follow the preference dropdown (10.3)."""

    def test_annuity_sections_start_hidden(self) -> None:
        """Drawdown-only is the default; no purchase sections show."""
        pane = _pane()
        assert pane.annuity_purchases.isHidden()
        assert pane.partner_annuity_purchases.isHidden()

    def test_choosing_annuity_reveals_the_sections(self) -> None:
        """The preference dropdown is the disclosure control."""
        pane = _pane()
        pane.retirement_income_form.set_value(
            INCOME_PREFERENCE_KEY, INCOME_PREFERENCE_ANNUITY
        )
        assert _shown(pane, pane.annuity_purchases)

    def test_the_partner_section_also_needs_a_partner(self) -> None:
        """The partner's purchases sit under the preference, couples only.

        Both purchase sections live directly beneath the disclosure
        control (roadmap 10.3); the partner's shows only while a
        partner is on the form.
        """
        pane = _pane()
        pane.retirement_income_form.set_value(
            INCOME_PREFERENCE_KEY, INCOME_PREFERENCE_ANNUITY
        )
        assert not _shown(pane, pane.partner_annuity_purchases)
        pane.add_partner()
        assert _shown(pane, pane.partner_annuity_purchases)
        pane.remove_partner()
        assert not _shown(pane, pane.partner_annuity_purchases)

    def test_switching_back_confirms_then_deletes_the_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering Yes clears every purchase row and hides the sections."""
        pane = _pane()
        pane.retirement_income_form.set_value(
            INCOME_PREFERENCE_KEY, INCOME_PREFERENCE_ANNUITY
        )
        pane.annuity_purchases.add_entry().set_value("at_age", "68")
        monkeypatch.setattr(
            forms_module,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Yes),
        )
        pane.retirement_income_form.set_value(INCOME_PREFERENCE_KEY, "")
        assert pane.form_data().annuity_purchases == ()
        assert not _shown(pane, pane.annuity_purchases)

    def test_declining_the_confirm_keeps_the_rows_and_reverts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering No keeps the purchases and the annuity preference."""
        pane = _pane()
        pane.retirement_income_form.set_value(
            INCOME_PREFERENCE_KEY, INCOME_PREFERENCE_ANNUITY
        )
        pane.annuity_purchases.add_entry().set_value("at_age", "68")
        monkeypatch.setattr(
            forms_module,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.No),
        )
        pane.retirement_income_form.set_value(INCOME_PREFERENCE_KEY, "")
        [row] = pane.form_data().annuity_purchases
        assert row["at_age"] == "68"
        values = pane.retirement_income_form.values()
        assert values[INCOME_PREFERENCE_KEY] == INCOME_PREFERENCE_ANNUITY
        assert _shown(pane, pane.annuity_purchases)

    def test_loaded_rows_show_even_with_a_blank_preference(self) -> None:
        """Rows on the form are never hidden by the preference."""
        pane = _pane()
        pane.set_form_data(FactsFormData(annuity_purchases=({"at_age": "68"},)))
        assert _shown(pane, pane.annuity_purchases)

    def test_loading_a_drawdown_plan_hides_the_sections_again(self) -> None:
        """A plan without purchases returns to the compact form."""
        pane = _pane()
        pane.retirement_income_form.set_value(
            INCOME_PREFERENCE_KEY, INCOME_PREFERENCE_ANNUITY
        )
        pane.set_form_data(FactsFormData())
        assert not _shown(pane, pane.annuity_purchases)


def _simple_pane(
    on_view_changed: Callable[[FormView], None] | None = None,
) -> FactsEntryPane:
    """A pane opened in the Simple view (roadmap 11.1)."""
    return FactsEntryPane(
        build_facts_form_view_model(),
        lambda _data: FactsSubmissionOutcome(status=""),
        lambda: "",
        view=FormView.SIMPLE,
        on_view_changed=on_view_changed,
    )


class TestSimpleView:
    """Simple shows who you are and what you have (planning §4.13)."""

    def test_blank_simple_form_shows_only_the_essentials(self) -> None:
        """Standard sections stay out of the way until they hold anything."""
        pane = _simple_pane()
        assert _shown(pane, pane.person_form)
        assert _shown(pane, pane.wrappers)
        assert _shown(pane, pane.wrappers.add_button)
        assert not _shown(pane, pane.spending_form)
        assert not _shown(pane, pane.retirement_income_form)
        assert not _shown(pane, pane.state_pension_form)
        assert not _shown(pane, pane.db_pensions)
        assert not _shown(pane, pane.annuity_purchases)
        assert _shown(pane, pane.simple_note)
        assert not _shown(pane, pane.intro_label)

    def test_simple_person_rows_hide_the_standard_fields(self) -> None:
        """Sex and the advanced access facts are Detailed only."""
        pane = _simple_pane()
        form = pane.person_form
        assert form.editor("date_of_birth").isVisibleTo(form)
        assert form.editor("employment_income").isVisibleTo(form)
        assert not form.editor("sex_for_longevity").isVisibleTo(form)
        assert not form.editor("death_age").isVisibleTo(form)
        assert not form.advanced_toggle.isVisibleTo(form)

    def test_a_standard_section_holding_a_value_shows(self) -> None:
        """Data is never hidden: a spending need shows in Simple."""
        pane = _simple_pane()
        pane.set_form_data(FactsFormData(spending={"annual_spending_real": "24000"}))
        assert _shown(pane, pane.spending_form)
        form = pane.spending_form
        assert form.editor("annual_spending_real").isVisibleTo(form)
        assert not form.editor("go_go_multiplier").isVisibleTo(form)

    def test_an_advanced_field_holding_a_value_shows(self) -> None:
        """A filled advanced field shows, without the disclosure toggle."""
        pane = _simple_pane()
        pane.set_form_data(
            FactsFormData(persons=(PersonFormData(person={"death_age": "85"}),))
        )
        form = pane.person_form
        assert form.editor("death_age").isVisibleTo(form)
        assert not form.editor("lsa_used").isVisibleTo(form)
        assert not form.advanced_toggle.isVisibleTo(form)

    def test_the_relief_choice_follows_the_account_kind(self) -> None:
        """Only a workplace pension asks how relief is given."""
        pane = _simple_pane()
        form = pane.wrappers.add_entry()
        relief = form.editor("relief_mechanic")
        assert not relief.isVisibleTo(form)
        form.set_value("kind", "uk.workplace_dc")
        assert relief.isVisibleTo(form)
        form.set_value("kind", "uk.sipp")
        assert not relief.isVisibleTo(form)

    def test_an_error_reveals_its_hidden_section(self) -> None:
        """An error on a field Simple hides must still be seen."""
        pane = _simple_pane()
        pane._show_errors(  # noqa: SLF001 - the pane's own error renderer
            (FormError("spending", None, "annual_spending_real", "bad"),)
        )
        assert _shown(pane, pane.spending_form)
        form = pane.spending_form
        assert form.editor("annual_spending_real").isVisibleTo(form)

    def test_choosing_detailed_shows_everything_and_reports_once(self) -> None:
        """The switch shows every section and reports the user's choice."""
        chosen: list[FormView] = []
        pane = _simple_pane(chosen.append)
        pane.view_buttons[FormView.DETAILED].click()
        assert pane.view is FormView.DETAILED
        assert chosen == [FormView.DETAILED]
        assert _shown(pane, pane.spending_form)
        assert _shown(pane, pane.state_pension_form)
        assert _shown(pane, pane.db_pensions)
        assert pane.person_form.editor("sex_for_longevity").isVisibleTo(
            pane.person_form
        )
        assert not _shown(pane, pane.simple_note)
        pane.view_buttons[FormView.DETAILED].click()
        assert chosen == [FormView.DETAILED]

    def test_switching_views_never_changes_what_submits(self) -> None:
        """Both views collect the same raw text (one form, two views)."""
        pane = _simple_pane()
        pane.set_form_data(
            FactsFormData(
                persons=(PersonFormData(person={"date_of_birth": "1980-01-01"}),),
                spending={"annual_spending_real": "24000"},
            )
        )
        simple = pane.form_data()
        pane.set_view(FormView.DETAILED)
        assert pane.form_data() == simple

    def test_a_half_filled_row_keeps_its_required_fields_in_simple(self) -> None:
        """A row holding anything shows every field it still needs.

        Regression (Codex review): a DB pension started in Detailed hid
        its blank statement date and pension age in Simple.
        """
        pane = _pane()
        form = pane.db_pensions.add_entry()
        form.set_value("accrued_annual_pension", "8500")
        pane.set_view(FormView.SIMPLE)
        assert form.editor("statement_date").isVisibleTo(form)
        assert form.editor("normal_pension_age").isVisibleTo(form)
        assert not form.editor("commutation_factor").isVisibleTo(form)

    def test_simple_hides_the_db_add_action(self) -> None:
        """DB pensions are added in Detailed; existing rows still show."""
        pane = _simple_pane()
        assert not pane.db_pensions.add_button.isVisibleTo(pane.db_pensions)
        pane.set_view(FormView.DETAILED)
        pane.db_pensions.add_entry()
        pane.set_view(FormView.SIMPLE)
        assert _shown(pane, pane.db_pensions)
        assert not pane.db_pensions.add_button.isVisibleTo(pane.db_pensions)


class TestRowCards:
    """Repeatable rows collapse to one-line cards (roadmap 11.4)."""

    def test_a_row_the_user_adds_opens_ready_to_type(self) -> None:
        """A new row shows its fields and a Done action."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        form = section.add_entry()
        assert section.is_expanded(form)

    def test_loaded_rows_start_as_their_summaries(self) -> None:
        """Saved rows show one line each; the fields stay closed."""
        pane = _pane()
        pane.set_form_data(
            FactsFormData(
                wrappers=(
                    {
                        "kind": "uk.isa",
                        "balance": "16500",
                        "employee_contribution": "4800",
                    },
                ),
            )
        )
        [form] = pane.wrappers.forms
        assert not pane.wrappers.is_expanded(form)
        summaries = [
            label.text()
            for label in pane.wrappers.findChildren(QLabel)
            if label.objectName() == "rowSummary"
        ]
        assert summaries == ["Stocks & shares ISA · £16,500 · £4,800 a year paid in"]

    def test_done_closes_the_row_and_refreshes_its_summary(self) -> None:
        """Typed values show on the card once the row is closed."""
        section = RepeatableSection(build_facts_form_view_model().wrapper)
        form = section.add_entry()
        form.set_value("kind", "uk.sipp")
        form.set_value("balance", "1000")
        section.set_expanded(form, expanded=False)
        assert not section.is_expanded(form)
        summary = next(
            label
            for label in section.findChildren(QLabel)
            if label.objectName() == "rowSummary"
        )
        assert summary.text() == "SIPP · £1,000"
        section.set_expanded(form, expanded=True)
        assert section.is_expanded(form)

    def test_an_error_opens_its_row(self) -> None:
        """A closed row carrying an error is opened so it is seen."""
        pane = _pane()
        pane.set_form_data(FactsFormData(wrappers=({"kind": "uk.isa"},)))
        [form] = pane.wrappers.forms
        assert not pane.wrappers.is_expanded(form)
        pane._show_errors(  # noqa: SLF001 - the pane's own error renderer
            (FormError("wrapper", 0, "balance", "this field is required"),)
        )
        assert pane.wrappers.is_expanded(form)

    def test_closed_rows_still_submit_and_keep_their_ids(self) -> None:
        """Closing a card changes what shows, never what submits."""
        pane = _pane()
        pane.set_form_data(
            FactsFormData(
                wrappers=({ENTITY_ID_KEY: "w-1", "kind": "uk.isa", "balance": "500"},)
            )
        )
        [row] = pane.form_data().wrappers
        assert row[ENTITY_ID_KEY] == "w-1"
        assert row["balance"] == "500"


def _listed(pane: FactsEntryPane) -> list[str]:
    """The keys of the steps the side list offers, in order."""
    steps = build_facts_form_view_model().steps
    return [step.key for step in steps if pane.step_listed(step.key)]


class TestSteps:
    """Sections are steps in a side list (roadmap 11.2)."""

    def test_detailed_lists_every_step(self) -> None:
        """All seven steps, starting on You."""
        pane = _pane()
        assert _listed(pane) == [
            "you",
            "spending",
            "retirement_income",
            "state_pension",
            "accounts",
            "db_pensions",
            "partner",
        ]
        assert pane.current_step == "you"

    def test_simple_lists_only_steps_with_something_to_show(self) -> None:
        """A blank Simple form: you, your accounts, and a partner."""
        pane = _simple_pane()
        assert _listed(pane) == ["you", "accounts", "partner"]
        pane.set_form_data(FactsFormData(spending={"annual_spending_real": "24000"}))
        assert _listed(pane) == ["you", "spending", "accounts", "partner"]

    def test_any_step_can_be_visited(self) -> None:
        """Steps are pages, visited in any order."""
        pane = _pane()
        pane.show_step("db_pensions")
        assert pane.current_step == "db_pensions"
        pane.show_step("you")
        assert pane.current_step == "you"

    def test_a_step_dropping_out_hands_over_to_the_first(self) -> None:
        """Switching to Simple on a Detailed-only step returns to You."""
        pane = _pane()
        pane.show_step("db_pensions")
        pane.set_view(FormView.SIMPLE)
        assert pane.current_step == "you"

    def test_errors_flag_their_steps_and_open_the_first(self) -> None:
        """Each errored step is marked; the first error's page shows."""
        pane = _pane()
        pane._show_errors(  # noqa: SLF001 - the pane's own error renderer
            (
                FormError("spending", None, "annual_spending_real", "bad"),
                FormError("person", None, "date_of_birth", "required"),
            )
        )
        assert pane.current_step == "spending"
        texts = [
            pane.step_list.item(row).text() for row in range(pane.step_list.count())
        ]
        assert texts[0] == "You" + STEP_ERROR_SUFFIX
        assert texts[1] == "Spending" + STEP_ERROR_SUFFIX
        assert texts[2] == "Retirement income"

    def test_a_new_submission_clears_the_flags(self) -> None:
        """Flags last only until the next submission renders."""
        pane = _pane()
        pane._show_errors(  # noqa: SLF001 - the pane's own error renderer
            (FormError("spending", None, "annual_spending_real", "bad"),)
        )
        pane.submit()
        assert pane.step_list.item(1).text() == "Spending"

    def test_replacing_the_form_clears_the_flags(self) -> None:
        """Opening another plan never keeps the last plan's warnings."""
        pane = _pane()
        pane._show_errors(  # noqa: SLF001 - the pane's own error renderer
            (FormError("spending", None, "annual_spending_real", "bad"),)
        )
        pane.set_form_data(FactsFormData())
        assert pane.step_list.item(1).text() == "Spending"

    def test_one_save_submits_every_step(self) -> None:
        """What submits never depends on which page is showing."""
        pane = _pane()
        pane.set_form_data(
            FactsFormData(
                persons=(PersonFormData(person={"date_of_birth": "1980-01-01"}),),
                spending={"annual_spending_real": "24000"},
            )
        )
        on_you = pane.form_data()
        pane.show_step("accounts")
        assert pane.form_data() == on_you
