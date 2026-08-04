"""Theme smoke tests, offscreen: palette, stylesheet, brand assets."""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

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
