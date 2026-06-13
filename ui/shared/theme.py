"""Shared Qt application theme helpers."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

import config


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


def dark_theme_colors() -> dict[str, str]:
    """Derive the full dark palette from the four user-configurable base colours.

    Cards, borders, buttons and hover states are computed by lighten/darken so
    the user only chooses background, surface, text and accent in Settings.
    """
    bg = _color(getattr(config, "THEME_DARK_BG", "#1c1e26"), "#1c1e26")
    surface = _color(getattr(config, "THEME_DARK_SURFACE", "#17181d"), "#17181d")
    text = _color(getattr(config, "THEME_DARK_TEXT", "#e8e8f0"), "#e8e8f0")
    accent = _color(getattr(config, "THEME_DARK_ACCENT", "#8b87ff"), "#8b87ff")
    ar, ag, ab = accent.red(), accent.green(), accent.blue()
    return {
        "bg": _hex(bg),
        "surface": _hex(surface),
        "text": _hex(text),
        "accent": _hex(accent),
        "card": _hex(bg.lighter(118)),
        "border": _hex(bg.lighter(165)),
        "button": _hex(bg.lighter(140)),
        "button_hover": _hex(bg.lighter(160)),
        "button_pressed": _hex(bg.darker(112)),
        "tab": _hex(bg.lighter(118)),
        "tab_selected": _hex(bg.lighter(150)),
        "tooltip_bg": _hex(bg.lighter(140)),
        "tooltip_border": _hex(bg.lighter(175)),
        "text_dim": _hex(text.darker(165)),
        "accent_hover": _hex(accent.lighter(120)),
        "scroll_handle": _hex(bg.lighter(175)),
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


def apply_app_theme(app: QApplication | None = None) -> None:
    """Apply the configured app-wide palette to top-level Qt widgets."""
    app = app or QApplication.instance()
    if app is None:
        return

    _apply_color_scheme_hint(app)

    if is_dark_mode():
        c = dark_theme_colors()
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
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["text_dim"]))
        palette.setColor(QPalette.ColorRole.Mid, QColor(c["border"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["text_dim"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["text_dim"]))

        app.setPalette(palette)
        app.setStyleSheet(
            f"""
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
        QCheckBox {{
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
        )
        return

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2430"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f6f8fb"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1f2430"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1f2430"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1f2430"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#2457c5"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6feb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#667085"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#cfd6e2"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8a93a3"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#8a93a3"))

    app.setPalette(palette)
    app.setStyleSheet(
        """
        QWidget {
            background-color: #ffffff;
            color: #1f2430;
        }
        QToolTip {
            color: #1f2430;
            background-color: #ffffff;
            border: 1px solid #d7dce5;
            padding: 4px;
        }
        QTabWidget::pane {
            border: 1px solid #d7dce5;
        }
        QTabBar::tab {
            background: #f8fafc;
            color: #344054;
            padding: 6px 12px;
            border: 1px solid #d7dce5;
            border-bottom: none;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #101828;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
            background: #ffffff;
            color: #1f2430;
            border: 1px solid #cfd6e2;
            border-radius: 4px;
            padding: 4px;
            selection-background-color: #2f6feb;
            selection-color: #ffffff;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
            border-color: #2f6feb;
        }
        QPushButton {
            background: #f8fafc;
            color: #1f2430;
            border: 1px solid #cfd6e2;
            border-radius: 4px;
            padding: 5px 12px;
        }
        QPushButton:hover {
            background: #eef4ff;
            border-color: #a9bde8;
        }
        QPushButton:pressed {
            background: #e1ebff;
        }
        QCheckBox, QLabel, QGroupBox {
            color: #1f2430;
        }
        QGroupBox {
            border: 1px solid #d7dce5;
            border-radius: 4px;
            margin-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        QScrollArea, QFrame {
            background: transparent;
        }
        QScrollBar:vertical, QScrollBar:horizontal {
            background: #f6f8fb;
            border: none;
        }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #c7cfdd;
            border-radius: 4px;
            min-height: 24px;
            min-width: 24px;
        }
        QScrollBar::add-line, QScrollBar::sub-line {
            width: 0px;
            height: 0px;
        }
        """
    )
