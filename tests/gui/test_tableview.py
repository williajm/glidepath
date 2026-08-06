"""Shared table widget tests, offscreen (§4.7).

``fill_table`` is the one mechanical binding every result pane uses:
columns size to their content up to a cap, cells past the tooltip
threshold hover to their own full text, and per-row tooltips bind to
the label column only.
"""

from PySide6.QtWidgets import QAbstractItemView, QWidget

from glidepath.gui.tableview import (
    _MAX_COLUMN_WIDTH,
    _TOOLTIP_LENGTH,
    fill_table,
    read_only_table,
)


class TestReadOnlyTable:
    """The shared table is configured for display-only rows."""

    def test_configured_for_display_only_rows(self) -> None:
        """No edit triggers, row selection, stripes, no row header."""
        parent = QWidget()
        table = read_only_table(parent)
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert (
            table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        )
        assert table.alternatingRowColors()
        assert not table.verticalHeader().isVisible()


class TestFillTable:
    """Rows bind mechanically: headers, cells, tooltips, capped widths."""

    def test_replaces_headers_and_cells(self) -> None:
        """Column headers and cell text come verbatim from the rows."""
        parent = QWidget()
        table = read_only_table(parent)
        fill_table(table, ("Metric", "Value"), [("Success rate", "97.0%")])
        assert table.columnCount() == 2
        assert table.rowCount() == 1
        header = table.horizontalHeaderItem(1)
        assert header is not None
        assert header.text() == "Value"
        item = table.item(0, 1)
        assert item is not None
        assert item.text() == "97.0%"

    def test_cells_past_the_tooltip_threshold_hover_to_their_text(self) -> None:
        """One character over the threshold turns the tooltip on.

        Anything a capped column might elide must be hoverable in
        full, while short cells stay tooltip-free.
        """
        parent = QWidget()
        table = read_only_table(parent)
        at_threshold = "x" * _TOOLTIP_LENGTH
        past_threshold = "y" * (_TOOLTIP_LENGTH + 1)
        fill_table(
            table,
            ("Label", "Value"),
            [("short", at_threshold), ("long", past_threshold)],
        )
        threshold_item = table.item(0, 1)
        assert threshold_item is not None
        assert threshold_item.toolTip() == ""
        elided_item = table.item(1, 1)
        assert elided_item is not None
        assert elided_item.toolTip() == past_threshold

    def test_row_tooltips_bind_to_the_label_column_only(self) -> None:
        """The label column carries the row description.

        Other cells hover to their own text even when a row
        description is supplied, so an elided value never hides.
        """
        parent = QWidget()
        table = read_only_table(parent)
        long_label = "L" * (_TOOLTIP_LENGTH + 1)
        long_value = "V" * (_TOOLTIP_LENGTH + 1)
        fill_table(
            table,
            ("Label", "Value"),
            [(long_label, long_value)],
            tooltips=["the row description"],
        )
        label_item = table.item(0, 0)
        assert label_item is not None
        assert label_item.toolTip() == "the row description"
        value_item = table.item(0, 1)
        assert value_item is not None
        assert value_item.toolTip() == long_value

    def test_wide_columns_cap_while_narrow_columns_size_to_content(self) -> None:
        """A column past the cap clamps to it; the rest stay uncapped.

        Narrow columns keep their content-sized width, so the cap
        never inflates a short column.
        """
        parent = QWidget()
        table = read_only_table(parent)
        fill_table(table, ("Narrow", "Wide"), [("ab", "w" * 400)])
        assert table.columnWidth(1) == _MAX_COLUMN_WIDTH
        narrow_width = table.columnWidth(0)
        assert 0 < narrow_width < _MAX_COLUMN_WIDTH
