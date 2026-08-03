"""The stated-vs-assumed widgets (§4.7, roadmap 8.3).

Four read-only tables — facts, assumptions, decisions, and the
entity-level plan structure (issue #70) — bound to the app layer's
:class:`~glidepath.app.InspectorViewModel`. Double-clicking an
assumption row opens the in-place override prompt (multi-line for
table-valued rows, issue #71); the new raw text goes straight back to
the app layer.
"""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from glidepath.app import AssumptionRow, InspectorViewModel


def _read_only_table(parent: QWidget) -> QTableWidget:
    """A table configured for display-only rows."""
    table = QTableWidget(parent)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    return table


def _fill_table(
    table: QTableWidget,
    columns: tuple[str, ...],
    rows: list[tuple[str, ...]],
    tooltips: list[str] | None = None,
) -> None:
    """Replace a table's contents with pre-formatted cells."""
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            item = QTableWidgetItem(text)
            if tooltips is not None:
                item.setToolTip(tooltips[row_index])
            table.setItem(row_index, column_index, item)
    table.resizeColumnsToContents()


class InspectorPane(QWidget):
    """The stated-vs-assumed tab (roadmap 8.3)."""

    def __init__(
        self,
        on_override: Callable[[str, str], str | None],
        parent: QWidget | None = None,
    ) -> None:
        """Build the three-column surface and wire the override callback."""
        super().__init__(parent)
        self._on_override = on_override
        self._view_model: InspectorViewModel | None = None

        self._facts_box = QGroupBox(self)
        self.facts_table = _read_only_table(self._facts_box)
        facts_layout = QVBoxLayout(self._facts_box)
        facts_layout.addWidget(self.facts_table)

        self._roll_forwards_box = QGroupBox(self)
        self.roll_forwards_table = _read_only_table(self._roll_forwards_box)
        roll_forwards_layout = QVBoxLayout(self._roll_forwards_box)
        roll_forwards_layout.addWidget(self.roll_forwards_table)

        self._assumptions_box = QGroupBox(self)
        self.assumptions_table = _read_only_table(self._assumptions_box)
        self.assumptions_table.cellDoubleClicked.connect(self._edit_assumption)
        assumptions_layout = QVBoxLayout(self._assumptions_box)
        assumptions_layout.addWidget(self.assumptions_table)

        self._decisions_box = QGroupBox(self)
        self.decisions_table = _read_only_table(self._decisions_box)
        decisions_layout = QVBoxLayout(self._decisions_box)
        decisions_layout.addWidget(self.decisions_table)

        self._structure_box = QGroupBox(self)
        self.structure_table = _read_only_table(self._structure_box)
        structure_layout = QVBoxLayout(self._structure_box)
        structure_layout.addWidget(self.structure_table)

        self.summary_label = QLabel("", self)
        self.summary_label.setWordWrap(True)
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)

        stated_column = QVBoxLayout()
        stated_column.addWidget(self._facts_box, 2)
        stated_column.addWidget(self._roll_forwards_box, 1)

        columns = QHBoxLayout()
        columns.addLayout(stated_column, 1)
        columns.addWidget(self._assumptions_box, 2)
        columns.addWidget(self._decisions_box, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(columns, 2)
        layout.addWidget(self._structure_box, 1)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.status_label)

    def refresh(self, view_model: InspectorViewModel) -> None:
        """Re-render every table from a fresh view model."""
        self._view_model = view_model
        self._facts_box.setTitle(view_model.facts_heading)
        self._assumptions_box.setTitle(view_model.assumptions_heading)
        self._decisions_box.setTitle(view_model.decisions_heading)
        _fill_table(
            self.facts_table,
            view_model.facts_columns,
            [
                (row.label, row.value, row.as_of, row.recorded)
                for row in view_model.facts
            ],
        )
        _fill_table(
            self.assumptions_table,
            view_model.assumptions_columns,
            [
                (
                    row.key,
                    row.value,
                    row.default_value,
                    row.status,
                    row.usage,
                    row.source,
                    row.recorded,
                )
                for row in view_model.assumptions
            ],
            tooltips=[row.description for row in view_model.assumptions],
        )
        _fill_table(
            self.decisions_table,
            view_model.decisions_columns,
            [(row.label, row.value, row.recorded) for row in view_model.decisions],
        )
        self._roll_forwards_box.setTitle(view_model.roll_forwards_heading)
        self._roll_forwards_box.setVisible(bool(view_model.roll_forwards))
        _fill_table(
            self.roll_forwards_table,
            view_model.roll_forwards_columns,
            [
                (row.label, row.stated, row.as_of, row.months, row.opening)
                for row in view_model.roll_forwards
            ],
        )
        self._structure_box.setTitle(view_model.structure_heading)
        _fill_table(
            self.structure_table,
            view_model.structure_columns,
            [(row.entity, row.setting, row.value) for row in view_model.structure],
        )
        self.summary_label.setText(view_model.summary)

    def assumption_row(self, row_index: int) -> AssumptionRow:
        """The assumption row behind a table row (refresh must have run)."""
        if self._view_model is None:
            msg = "refresh() must run before rows can be read"
            raise RuntimeError(msg)
        return self._view_model.assumptions[row_index]

    def _edit_assumption(self, row_index: int, _column: int) -> None:
        """Open the in-place override prompt for a double-clicked row."""
        if self._view_model is None:  # pragma: no cover - tables imply a model
            return
        row = self.assumption_row(row_index)
        # Joining the row's key to the prompt is layout, not copy — both
        # strings come from the app layer (§4.7).
        if row.structured:
            text, accepted = QInputDialog.getMultiLineText(
                self,
                self._view_model.override_title,
                f"{row.key} — {self._view_model.table_override_prompt}",
                row.edit_text,
            )
        else:
            text, accepted = QInputDialog.getText(
                self,
                self._view_model.override_title,
                f"{row.key} — {self._view_model.override_prompt}",
                text=row.edit_text,
            )
        if not accepted:
            return
        error = self._on_override(row.key, text)
        self.status_label.setText(error or "")


__all__ = [
    "InspectorPane",
]
