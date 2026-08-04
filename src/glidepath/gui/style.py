"""The shell's visual theme: one palette, one stylesheet, one icon (§4.7).

Purely presentational Qt mechanics — no copy, no policy. The Fusion
style gives an identical baseline on every platform; the palette and
stylesheet build a light, neutral look with one accent colour; the
window icon is a painted placeholder that branding art can replace
without touching anything else.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

ACCENT = "#23735b"
"""The single accent colour: buttons, focus, selections, tab underline."""
_ACCENT_HOVER = "#1c5f4b"
_ACCENT_PRESSED = "#164e3d"
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


def placeholder_icon() -> QIcon:
    """A painted stand-in app icon: a glide curve on the accent tile.

    Deliberately replaceable: branding art dropped in here (an SVG or
    ``.ico``) changes the window and taskbar identity without touching
    the shell.
    """
    icon = QIcon()
    for size in _ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        radius = size / 5
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        pen = QPen(QColor("white"))
        pen.setWidthF(max(size / 10, 1.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # The namesake glide path: a de-risking curve from top-left
        # flattening out towards the bottom-right.
        points = [
            QPointF(size * 0.22, size * 0.25),
            QPointF(size * 0.30, size * 0.52),
            QPointF(size * 0.46, size * 0.68),
            QPointF(size * 0.62, size * 0.74),
            QPointF(size * 0.80, size * 0.76),
        ]
        painter.drawPolyline(points)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


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
    app.setWindowIcon(placeholder_icon())


__all__ = ["ACCENT", "STYLESHEET", "apply_theme", "placeholder_icon"]
