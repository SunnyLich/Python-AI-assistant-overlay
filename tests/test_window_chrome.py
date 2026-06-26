"""Window chrome coverage for top-level app surfaces."""

from __future__ import annotations

import os
import sys
import gc

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget


def test_text_popup_uses_standard_window_controls():
    """The full-reply popup should have the same titlebar buttons as app dialogs."""
    from ui.popup import TextPopup

    app = QApplication.instance() or QApplication(sys.argv)
    popup = TextPopup("hello")
    try:
        popup.show()
        app.processEvents()

        titlebar = popup.findChild(QWidget, "wispTitleBar")
        buttons = popup.findChildren(QPushButton)

        assert titlebar is not None
        assert {button.property("winbtn") for button in buttons} >= {"min", "max", "close"}
    finally:
        popup.close()
        popup.deleteLater()
        app.processEvents()


def test_window_chrome_survives_change_events_after_gc():
    """Window chrome event filter should keep its Python state alive."""
    from ui.shared.framed import install_window_chrome

    app = QApplication.instance() or QApplication(sys.argv)
    window = QWidget()
    try:
        install_window_chrome(window)
        assert getattr(window, "_wisp_window_chrome", None) is not None

        gc.collect()
        window.show()
        app.processEvents()
        for event_type in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.Resize,
        ):
            app.sendEvent(window, QEvent(event_type))
        app.processEvents()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
