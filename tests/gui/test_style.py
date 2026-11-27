"""Theme smoke tests, offscreen: palette, stylesheet, brand assets."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QComboBox

from glidepath.gui.style import (
    ACCENT,
    STYLESHEET,
    app_icon,
    apply_theme,
    wordmark_pixmap,
)


class TestTheme:
    """The theme applies application-wide and stays value-checkable."""

    def test_apply_theme_sets_palette_stylesheet_and_icon(self) -> None:
        """One call restyles the app: accent palette, the QSS, the icon.

        The style itself becomes a stylesheet proxy once QSS is set,
        so the base style has no checkable name — the palette accent
        is the observable effect.
        """
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_theme(app)
        highlight = app.palette().color(QPalette.ColorRole.Highlight)
        assert highlight.name() == ACCENT
        assert app.styleSheet() == STYLESHEET
        assert not app.windowIcon().isNull()

    def test_app_icon_loads_every_size(self) -> None:
        """The packaged brand icon covers taskbar-to-tile sizes."""
        icon = app_icon()
        assert not icon.isNull()
        assert len(icon.availableSizes()) >= 7

    def test_wordmark_loads_at_display_resolution(self) -> None:
        """The About/README wordmark asset is present and wide."""
        pixmap = wordmark_pixmap()
        assert not pixmap.isNull()
        assert pixmap.width() >= 800

    def test_combo_boxes_draw_their_drop_down_arrow(self) -> None:
        """Choice fields show an arrow, so they never pass for text boxes.

        Regression for #236: styling ``QComboBox::drop-down`` without a
        ``::down-arrow`` made the themed Fusion style drop the arrow.
        An empty combo's drop-down area must hold pixels that differ
        from the field's own background.
        """
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_theme(app)
        combo = QComboBox()
        combo.addItem("")
        combo.setFixedSize(200, 32)
        combo.show()
        app.processEvents()
        image = combo.grab().toImage()
        background = image.pixelColor(combo.width() // 2, combo.height() // 2)
        inset = 4
        arrow_area = [
            image.pixelColor(x, y)
            for x in range(combo.width() - 30, combo.width() - inset)
            for y in range(inset, combo.height() - inset)
        ]
        combo.close()
        assert any(_differs(colour, background) for colour in arrow_area)


def _differs(colour: QColor, background: QColor) -> bool:
    """Whether ``colour`` is visibly different from ``background``."""
    channels = (
        (colour.red(), background.red()),
        (colour.green(), background.green()),
        (colour.blue(), background.blue()),
    )
    return sum(abs(a - b) for a, b in channels) > 60
