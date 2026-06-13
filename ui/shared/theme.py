"""Shared Qt application theme helpers.

A theme is just a *template* of four base colours — background, surface, text,
accent. There are two templates (light and dark); switching the app between
light and dark simply swaps which template is active. Cards, borders, buttons
and hover states are derived from those four so the user only picks four
swatches per mode in Settings → App.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

import config


# Per-mode template defaults. Editable via THEME_<MODE>_<ROLE> config keys.
_TEMPLATE_DEFAULTS = {
    "dark": {
        "bg": "#1c1e26",
        "surface": "#17181d",
        "text": "#e8e8f0",
        "accent": "#8b87ff",
    },
    "light": {
        "bg": "#f2f2f7",
        "surface": "#ffffff",
        "text": "#1c1c1e",
        "accent": "#5856d6",
    },
}


def is_dark_mode() -> bool:
    """Return True if dark mode should be active right now."""
    mode = getattr(config, "THEME_MODE", "system")
    if mode == "dark":
        return True
    if mode == "light":
        return False
    # "system" — ask Qt for the OS colour scheme
    app = QApplication.instance()
    if app is None:
        return False
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def _hex(c: QColor) -> str:
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def _color(value: str, fallback: str) -> QColor:
    """Parse a user colour string, tolerating both #RRGGBB and #RRGGBBAA."""
    s = (value or "").strip()
    if s.startswith("#") and len(s) == 9:  # #RRGGBBAA — drop alpha for the palette
        s = s[:7]
    c = QColor(s)
    return c if c.isValid() else QColor(fallback)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Blend two colours: t=0 → a, t=1 → b."""
    return QColor(
        round(a.red() * (1 - t) + b.red() * t),
        round(a.green() * (1 - t) + b.green() * t),
        round(a.blue() * (1 - t) + b.blue() * t),
    )


def template_base(dark: bool) -> dict[str, str]:
    """The four user-chosen base colours for one mode, as hex strings."""
    mode = "dark" if dark else "light"
    d = _TEMPLATE_DEFAULTS[mode]
    out: dict[str, str] = {}
    for role, default in d.items():
        key = f"THEME_{mode.upper()}_{role.upper()}"
        out[role] = _hex(_color(getattr(config, key, default), default))
    return out


def theme_colors(dark: bool | None = None) -> dict[str, str]:
    """Derive the full palette for the active (or requested) mode.

    Cards, borders, buttons and hover states are computed from the four base
    colours so the user only chooses background, surface, text and accent.
    """
    if dark is None:
        dark = is_dark_mode()
    base = template_base(dark)
    bg = QColor(base["bg"])
    surface = QColor(base["surface"])
    text = QColor(base["text"])
    accent = QColor(base["accent"])
    ar, ag, ab = accent.red(), accent.green(), accent.blue()

    if dark:
        card = bg.lighter(118)
        border = bg.lighter(165)
        button = bg.lighter(140)
        button_hover = bg.lighter(160)
        button_pressed = bg.darker(112)
        tab = bg.lighter(118)
        tab_selected = bg.lighter(150)
        tooltip_bg = bg.lighter(140)
        tooltip_border = bg.lighter(175)
        scroll_handle = bg.lighter(175)
        text_dim = text.darker(165)
        accent_hover = accent.lighter(120)
    else:
        card = surface
        border = bg.darker(112)
        button = bg.lighter(102)
        button_hover = _mix(surface, accent, 0.10)
        button_pressed = _mix(surface, accent, 0.18)
        tab = bg.lighter(101)
        tab_selected = surface
        tooltip_bg = surface
        tooltip_border = bg.darker(110)
        scroll_handle = bg.darker(118)
        text_dim = _mix(text, bg, 0.55)
        accent_hover = accent.darker(112)

    return {
        "bg": base["bg"],
        "surface": base["surface"],
        "text": base["text"],
        "accent": base["accent"],
        "on_accent": "#ffffff",
        "card": _hex(card),
        "border": _hex(border),
        "button": _hex(button),
        "button_hover": _hex(button_hover),
        "button_pressed": _hex(button_pressed),
        "tab": _hex(tab),
        "tab_selected": _hex(tab_selected),
        "tooltip_bg": _hex(tooltip_bg),
        "tooltip_border": _hex(tooltip_border),
        "text_dim": _hex(text_dim),
        "accent_hover": _hex(accent_hover),
        "scroll_handle": _hex(scroll_handle),
        # Translucent accent washes for hover/pressed fills.
        "accent_hint": f"rgba({ar},{ag},{ab},0.08)",
        "accent_soft": f"rgba({ar},{ag},{ab},0.12)",
        "accent_strong": f"rgba({ar},{ag},{ab},0.22)",
    }


def _apply_color_scheme_hint(app: QApplication) -> None:
    """Tell Qt our preferred colour scheme so native window chrome matches.

    On macOS this drives the NSWindow appearance (title bar), which otherwise
    stays light while the styled content is dark — the mismatch that makes the
    overridden dark theme look broken. "system" sets Unknown so the OS decides
    (and so is_dark_mode()'s system path keeps reading the real OS scheme).
    """
    hints = app.styleHints()
    if not hasattr(hints, "setColorScheme"):
        return
    mode = getattr(config, "THEME_MODE", "system")
    scheme = {
        "dark": Qt.ColorScheme.Dark,
        "light": Qt.ColorScheme.Light,
    }.get(mode, Qt.ColorScheme.Unknown)
    try:
        hints.setColorScheme(scheme)
    except (AttributeError, TypeError):
        pass


def _app_stylesheet(c: dict[str, str]) -> str:
    return f"""
        QWidget {{
            background-color: {c["bg"]};
            color: {c["text"]};
        }}
        QToolTip {{
            color: {c["text"]};
            background-color: {c["tooltip_bg"]};
            border: 1px solid {c["tooltip_border"]};
            padding: 4px;
        }}
        QTabWidget::pane {{
            border: 1px solid {c["border"]};
        }}
        QTabBar::tab {{
            background: {c["tab"]};
            color: {c["text_dim"]};
            padding: 6px 12px;
            border: 1px solid {c["border"]};
            border-bottom: none;
        }}
        QTabBar::tab:selected {{
            background: {c["tab_selected"]};
            color: {c["text"]};
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
            background: {c["surface"]};
            color: {c["text"]};
            border: 1px solid {c["border"]};
            border-radius: 4px;
            padding: 4px;
            selection-background-color: {c["accent"]};
            selection-color: {c["on_accent"]};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
            border-color: {c["accent"]};
        }}
        QPushButton {{
            background: {c["button"]};
            color: {c["text"]};
            border: 1px solid {c["border"]};
            border-radius: 4px;
            padding: 5px 12px;
        }}
        QPushButton:hover {{
            background: {c["button_hover"]};
        }}
        QPushButton:pressed {{
            background: {c["button_pressed"]};
        }}
        QCheckBox, QLabel, QGroupBox {{
            color: {c["text"]};
        }}
        QGroupBox {{
            border: 1px solid {c["border"]};
            border-radius: 4px;
            margin-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            background-color: {c["bg"]};
        }}
        QScrollArea, QFrame {{
            background: transparent;
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {c["bg"]};
            border: none;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {c["scroll_handle"]};
            border-radius: 4px;
            min-height: 24px;
            min-width: 24px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0px;
            height: 0px;
        }}
        """


def apply_app_theme(app: QApplication | None = None) -> None:
    """Apply the active mode's template to the whole Qt application."""
    app = app or QApplication.instance()
    if app is None:
        return

    _apply_color_scheme_hint(app)

    c = theme_colors()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(c["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["card"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["tooltip_bg"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(c["button"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(c["accent_hover"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["on_accent"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["text_dim"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor(c["border"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["text_dim"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["text_dim"]))

    app.setPalette(palette)
    app.setStyleSheet(_app_stylesheet(c))
