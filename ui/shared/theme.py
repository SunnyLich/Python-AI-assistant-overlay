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


def apply_app_theme(app: QApplication | None = None) -> None:
    """Apply the configured app-wide palette to top-level Qt widgets."""
    app = app or QApplication.instance()
    if app is None:
        return

    if is_dark_mode():
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#202127"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#f0f0f2"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#17181d"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#24262d"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2e3038"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f2"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#2b2d35"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f0f0f2"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#8fb4ff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#4b67b0"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8d929d"))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#777b86"))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#777b86"))

        app.setPalette(palette)
        app.setStyleSheet(
            """
        QWidget {
            background-color: #202127;
            color: #f0f0f2;
        }
        QToolTip {
            color: #f6f6f7;
            background-color: #2e3038;
            border: 1px solid #50535f;
            padding: 4px;
        }
        QTabWidget::pane {
            border: 1px solid #3a3d48;
        }
        QTabBar::tab {
            background: #262832;
            color: #d8d9de;
            padding: 6px 12px;
            border: 1px solid #3a3d48;
            border-bottom: none;
        }
        QTabBar::tab:selected {
            background: #323541;
            color: #ffffff;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
            background: #17181d;
            color: #f0f0f2;
            border: 1px solid #454854;
            border-radius: 4px;
            padding: 4px;
            selection-background-color: #4b67b0;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
            border-color: #6f87d8;
        }
        QPushButton {
            background: #30333d;
            color: #f0f0f2;
            border: 1px solid #4b4f5d;
            border-radius: 4px;
            padding: 5px 12px;
        }
        QPushButton:hover {
            background: #393d49;
        }
        QPushButton:pressed {
            background: #252832;
        }
        QCheckBox {
            color: #f0f0f2;
        }
        QGroupBox {
            border: 1px solid #3a3d48;
            border-radius: 4px;
            margin-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }
        QScrollArea, QFrame {
            background: transparent;
        }
        QScrollBar:vertical, QScrollBar:horizontal {
            background: #202127;
            border: none;
        }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #555a66;
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
