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

_MAX_COLUMN_WIDTH = 300
"""Widths beyond this elide (the view's default ElideRight) instead of
forcing the whole table into a horizontal scrollbar; the full text
stays reachable via the cell tooltip or the override dialog."""

_TOOLTIP_LENGTH = 45
"""Cells longer than this get their own text as tooltip — roughly what
fits in a capped column, so anything elided is hoverable in full."""


def read_only_table(parent: QWidget) -> QTableWidget:
    """A table configured for display-only rows."""
    table = QTableWidget(parent)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    return table


def fill_table(
    table: QTableWidget,
    columns: tuple[str, ...],
    rows: list[tuple[str, ...]],
    tooltips: list[str] | None = None,
) -> None:
    """Replace a table's contents with pre-formatted cells.

    Columns size to their content up to a cap; longer cells elide.
    ``tooltips`` are per-row descriptions shown on the first (label)
    column only, so an elided cell elsewhere always hovers to its own
    full text, never to the row description.
    """
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            item = QTableWidgetItem(text)
            if tooltips is not None and column_index == 0:
                item.setToolTip(tooltips[row_index])
            elif len(text) > _TOOLTIP_LENGTH:
                item.setToolTip(text)
            table.setItem(row_index, column_index, item)
    table.resizeColumnsToContents()
    for index in range(table.columnCount()):
        if table.columnWidth(index) > _MAX_COLUMN_WIDTH:
            table.setColumnWidth(index, _MAX_COLUMN_WIDTH)


__all__ = [
    "fill_table",
    "read_only_table",
]
