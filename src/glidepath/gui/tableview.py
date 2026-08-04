"""Shared read-only table widgets for view-model rows (§4.7).

Every result pane renders pre-formatted app-layer rows the same way;
these helpers keep that mechanical binding in one place.
"""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)


def read_only_table(parent: QWidget) -> QTableWidget:
    """A table configured for display-only rows."""
    table = QTableWidget(parent)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    return table


def fill_table(
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


__all__ = [
    "fill_table",
    "read_only_table",
]
