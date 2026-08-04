"""The shell's visual theme: palette, stylesheet, and branding (§4.7).

Purely presentational Qt mechanics — no copy, no policy. The Fusion
style gives an identical baseline on every platform; the palette and
stylesheet build a light, neutral look around the brand green; the
window icon and the About wordmark load from ``assets/`` (the icon at
every taskbar-to-tile size), so refreshed art is a file swap.
"""

from importlib import resources
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

ACCENT = "#1e6d59"
"""The single accent colour — the brand tile's green: buttons, focus,
selections, the active tab underline."""
_ACCENT_HOVER = "#195b4a"
_ACCENT_PRESSED = "#144a3c"
_WINDOW = "#f4f6f4"
_CARD = "#ffffff"
_BORDER = "#d7dcd6"
_TEXT = "#22292b"
_MUTED = "#5c6663"

_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

STYLESHEET = f"""
QGroupBox {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 12px 12px 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {_TEXT};
    font-weight: bold;
}}
QLineEdit, QComboBox {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 5px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton#primaryButton {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
    font-weight: bold;
}}
QPushButton#primaryButton:hover {{
    background: {_ACCENT_HOVER};
    color: white;
}}
QPushButton#primaryButton:pressed {{
    background: {_ACCENT_PRESSED};
}}
QTabWidget::pane {{
    border: 1px solid {_BORDER};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    padding: 7px 18px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    color: {_MUTED};
}}
QTabBar::tab:selected {{
    color: {_TEXT};
    font-weight: bold;
    border-bottom: 2px solid {ACCENT};
}}
QTableWidget {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 6px;
    gridline-color: {_BORDER};
}}
QHeaderView::section {{
    background: {_WINDOW};
    border: none;
    border-bottom: 1px solid {_BORDER};
    padding: 5px 8px;
    font-weight: bold;
}}
QScrollArea {{
    border: none;
}}
"""


def _asset_bytes(name: str) -> bytes:
    """One packaged asset's raw bytes."""
    return (resources.files("glidepath.gui") / "assets" / name).read_bytes()


def app_icon() -> QIcon:
    """The brand icon — the glide curve on the green tile — every size."""
    icon = QIcon()
    for size in _ICON_SIZES:
        pixmap = QPixmap()
        pixmap.loadFromData(_asset_bytes(f"icon_{size}.png"))
        icon.addPixmap(pixmap)
    return icon


def wordmark_pixmap() -> QPixmap:
    """The horizontal wordmark, for the About screen."""
    pixmap = QPixmap()
    pixmap.loadFromData(_asset_bytes("wordmark.png"))
    return pixmap


def apply_theme(app: QApplication) -> None:
    """Apply the theme to the whole application (module docstring)."""
    app.setStyle("Fusion")
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(_WINDOW))
    palette.setColor(QPalette.ColorRole.Base, QColor(_CARD))
    palette.setColor(QPalette.ColorRole.Text, QColor(_TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(_TEXT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(_MUTED))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())


__all__ = ["ACCENT", "STYLESHEET", "app_icon", "apply_theme", "wordmark_pixmap"]
