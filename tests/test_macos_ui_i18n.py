import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_macos_agent_dialogs_use_qt_catalog_chrome():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import config
    from macos_py.workers.ui_host import MacAgentHistoryDialog, MacAgentRunDialog
    from ui import i18n

    class FakeHost:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event, data=None, req_id=None):
            self.events.append((event, data, req_id))

        def _agent_notify_approval(self, *args, **kwargs):
            self.events.append(("approval", args, kwargs))

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    host = FakeHost()
    history = MacAgentHistoryDialog(host)
    run = MacAgentRunDialog(host, {"title": "Demo Task"})

    try:
        history_buttons = {button.text() for button in history.dialog.findChildren(QPushButton)}
        run_buttons = {button.text() for button in run.dialog.findChildren(QPushButton)}
        run_tabs = {run.tabs.tabText(i) for i in range(run.tabs.count())}

        assert history.dialog.windowTitle() == i18n.t("Agent Task History")
        assert "Agent Task History" not in history.dialog.windowTitle()
        assert i18n.t("Refresh") in history_buttons
        assert "Refresh" not in history_buttons
        assert i18n.t("Open Run Folder") in history_buttons
        assert "Open Run Folder" not in history_buttons

        assert run.dialog.windowTitle().startswith(i18n.t("Agent Task"))
        assert i18n.t("Approve") in run_buttons
        assert "Approve" not in run_buttons
        assert i18n.t("Meeting Room") in run_tabs
        assert "Meeting Room" not in run_tabs
    finally:
        history.dialog.deleteLater()
        run.dialog.deleteLater()
        app.processEvents()
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
