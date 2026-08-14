"""The shell's visual theme: palette, stylesheet, and branding (§4.7).

Purely presentational Qt mechanics — no copy, no policy. The Fusion
style gives an identical baseline on every platform; the palette and
stylesheet build a light look around the brand green — white cards on
a softly green-tinted window, the accent carrying buttons, focus,
selections, tab underlines, and headings; the window icon and the
About wordmark load from ``assets/`` (the icon at every
taskbar-to-tile size), so refreshed art is a file swap.

The chart series palette is a colour-vision-validated categorical
set (fixed slot order — the ordering is the CVD-safety mechanism);
the percentile band overlays take an ordered ink ramp so the bands
read apart from the series fills, and the Monte Carlo fan fills one
brand-green hue at stepped alphas so probability depth reads by
lightness alone (9.24).
"""

from importlib import resources
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

ACCENT = "#1e6d59"
"""The single accent colour — the brand tile's green: buttons, focus,
selections, the active tab underline."""
ACCENT_TEXT = "#14503f"
"""The accent stepped darker for text on light surfaces (card titles,
table headers, the selected tab) — the button green is too light to
read as small text."""
_ACCENT_HOVER = "#195b4a"
_ACCENT_PRESSED = "#144a3c"
_ACCENT_DISABLED = "#a3c4b9"
_WINDOW = "#e9eeeb"
_CARD = "#ffffff"
_BORDER = "#d3dbd5"
_TEXT = "#1f2523"
_MUTED = "#5c6663"
_DISABLED_TEXT = "#95a09b"
_DISABLED_FILL = "#f0f3f1"
_TINT_HOVER = "#e7f0ec"
_TINT_PRESSED = "#d7e7e0"
_ALT_ROW = "#f4f8f5"
_HEADER_BG = "#eef3f0"
_SCROLL_HANDLE = "#c2ccc6"
_SCROLL_HANDLE_HOVER = "#9db1a8"
_TOOLTIP_BG = "#253230"
ERROR_TEXT = "#b3261e"
"""Inline field-error text (roadmap 10.2) — a red that clears WCAG AA
contrast on the white card surface."""

CHART_SURFACE = "#ffffff"
"""The chart plot surface — white, matching the cards the charts sit
beside, and the surface the series palette is validated against."""
CHART_GRID = "#e8ece9"
"""Hairline horizontal gridlines — recessive, behind the marks."""
CHART_AXIS_LINE = "#c9d2cc"
"""The axis baseline pen — a step darker than the grid."""
CHART_LABEL_INK = _MUTED
"""Axis tick labels and titles — muted ink, never the series colour."""
CHART_TEXT_INK = _TEXT
"""Legend labels — primary ink; the marker beside them carries the
series identity."""

CHART_SERIES = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)
"""Categorical series colours, assigned to bar sets in this fixed
order, never cycled or re-ranked. The order is validated for adjacent
colour-vision-deficiency separation on the white chart surface; the
sub-3:1-contrast slots are relieved by the per-segment hover
tooltips, the white segment gaps, and the cash-flow CSV export."""

CHART_BAND_INKS = ("#0b0b0b", "#52514e", "#898781")
"""Percentile/backtest band overlays, assigned in band order — one
neutral ink hue stepped light-ward so the ordered bands read apart
from every series fill and from each other."""

CHART_FAN_FILL = ACCENT
"""The Monte Carlo fan's single fill hue — the brand green; depth
comes from the alpha ramp, so the fan stays colour-vision-safe
(lightness alone orders the fills)."""

CHART_FAN_ALPHAS = (45, 75, 110, 150)
"""Fill opacities in fan order, outermost first. The nested fills
overlap, so the stacked alphas deepen naturally toward the median —
central probability mass reads as colour depth."""

_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

STYLESHEET = f"""
QGroupBox {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 14px 12px 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: {ACCENT_TEXT};
    font-weight: 600;
}}
QLineEdit, QComboBox {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background: {_DISABLED_FILL};
    color: {_DISABLED_TEXT};
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox QAbstractItemView {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    padding: 4px;
    selection-background-color: {_TINT_HOVER};
    selection-color: {_TEXT};
}}
QPushButton {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 6px;
    padding: 6px 16px;
}}
QPushButton:hover {{
    background: {_TINT_HOVER};
    border-color: {ACCENT};
    color: {ACCENT_TEXT};
}}
QPushButton:pressed {{
    background: {_TINT_PRESSED};
}}
QPushButton:disabled {{
    background: {_DISABLED_FILL};
    border-color: {_BORDER};
    color: {_DISABLED_TEXT};
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
QPushButton#primaryButton:disabled {{
    background: {_ACCENT_DISABLED};
    border-color: {_ACCENT_DISABLED};
    color: white;
}}
QTabWidget::pane {{
    border: 1px solid {_BORDER};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    padding: 8px 18px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    color: {_MUTED};
    margin-right: 2px;
}}
QTabBar::tab:hover {{
    color: {_TEXT};
}}
QTabBar::tab:selected {{
    color: {ACCENT_TEXT};
    font-weight: bold;
    border-bottom: 2px solid {ACCENT};
}}
QTableWidget {{
    background: {_CARD};
    alternate-background-color: {_ALT_ROW};
    border: 1px solid {_BORDER};
    border-radius: 6px;
    gridline-color: {_ALT_ROW};
    selection-background-color: {_TINT_PRESSED};
    selection-color: {_TEXT};
}}
QHeaderView::section {{
    background: {_HEADER_BG};
    border: none;
    border-bottom: 1px solid {_BORDER};
    padding: 6px 10px;
    font-weight: 600;
    color: {ACCENT_TEXT};
}}
QListWidget {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 6px;
    padding: 3px;
}}
QListWidget::item {{
    padding: 5px 8px;
    border-radius: 4px;
}}
QListWidget::item:hover {{
    background: {_TINT_HOVER};
}}
QListWidget::item:selected {{
    background: {_TINT_PRESSED};
    color: {_TEXT};
}}
QScrollArea {{
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {_SCROLL_HANDLE};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {_SCROLL_HANDLE_HOVER};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {_SCROLL_HANDLE};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {_SCROLL_HANDLE_HOVER};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QMenuBar {{
    background: transparent;
    padding: 2px 6px;
}}
QMenuBar::item {{
    padding: 5px 10px;
    border-radius: 5px;
    color: {_TEXT};
}}
QMenuBar::item:selected {{
    background: {_TINT_HOVER};
    color: {ACCENT_TEXT};
}}
QMenu {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 22px 6px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {_TINT_HOVER};
    color: {ACCENT_TEXT};
}}
QMenu::separator {{
    height: 1px;
    background: {_BORDER};
    margin: 5px 8px;
}}
QStatusBar {{
    color: {_MUTED};
}}
QStatusBar::item {{
    border: none;
}}
QToolTip {{
    background-color: {_TOOLTIP_BG};
    color: {_WINDOW};
    border: 1px solid {_TOOLTIP_BG};
    padding: 5px 8px;
}}
QRadioButton {{
    spacing: 6px;
}}
QLabel#answerLabel {{
    font-size: 13pt;
    font-weight: 600;
    color: {ACCENT_TEXT};
}}
QLabel#fieldErrorLabel {{
    color: {ERROR_TEXT};
}}
QPushButton#advancedToggle {{
    background: transparent;
    border: none;
    color: {ACCENT_TEXT};
    padding: 2px 0;
    text-align: left;
}}
QPushButton#advancedToggle:hover {{
    color: {ACCENT};
    text-decoration: underline;
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
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(_ALT_ROW))
    palette.setColor(QPalette.ColorRole.Text, QColor(_TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(_TEXT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(_MUTED))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())


__all__ = [
    "ACCENT",
    "ACCENT_TEXT",
    "CHART_AXIS_LINE",
    "CHART_BAND_INKS",
    "CHART_FAN_ALPHAS",
    "CHART_FAN_FILL",
    "CHART_GRID",
    "CHART_LABEL_INK",
    "CHART_SERIES",
    "CHART_SURFACE",
    "CHART_TEXT_INK",
    "STYLESHEET",
    "app_icon",
    "apply_theme",
    "wordmark_pixmap",
]
