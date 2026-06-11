import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_combo_ignores_wheel_when_popup_closed():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _NoScrollCombo

    class FakeWheelEvent:
        def __init__(self) -> None:
            self.ignored = False

        def ignore(self) -> None:
            self.ignored = True

    app = QApplication.instance() or QApplication(sys.argv)
    combo = _NoScrollCombo()
    event = FakeWheelEvent()
    try:
        combo.addItems(["one", "two"])
        combo.setFocus()

        combo.wheelEvent(event)

        assert event.ignored is True
    finally:
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_panel_loads_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    import ui.settings_panel.dialog as dialog_module
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)
    started: list[dict] = []

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(dialog_module.threading, "Thread", FakeThread)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._memory_browser_cv = layout
    dialog._memory_loading = True

    try:
        SettingsDialog._load_memory_panel(dialog)

        assert started
        assert started[0]["name"] == "wisp-memory-settings-load"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_panel_timeout_replaces_loading_message():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._memory_browser_cv = layout
    dialog._memory_loading = True
    dialog._memory_panel = None
    dialog._memory_load_token = 7
    layout.addWidget(QLabel("Loading stored facts..."))

    try:
        SettingsDialog._on_memory_panel_load_timeout(dialog, 7)

        assert dialog._memory_loading is False
        assert layout.count() == 1
        widget = layout.itemAt(0).widget()
        assert isinstance(widget, QLabel)
        assert "still starting up" in widget.text()
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_panel_ignores_stale_load_result():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)
    loading_label = QLabel("Loading stored facts...")
    layout.addWidget(loading_label)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._memory_browser_cv = layout
    dialog._memory_loading = True
    dialog._memory_load_token = 2

    try:
        SettingsDialog._on_memory_panel_loaded(dialog, 1, object(), [], "")

        assert dialog._memory_loading is True
        assert layout.itemAt(0).widget() is loading_label
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_panel_cancel_prevents_worker_start(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    import ui.settings_panel.dialog as dialog_module
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)
    started: list[bool] = []

    class FakeThread:
        def __init__(self, **_kwargs) -> None:
            started.append(False)

        def start(self) -> None:
            started[-1] = True

    monkeypatch.setattr(dialog_module.threading, "Thread", FakeThread)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._memory_browser_cv = layout
    dialog._memory_loading = False
    dialog._memory_load_token = 1

    try:
        SettingsDialog._load_memory_panel(dialog, 0)

        assert started == []
        assert dialog._memory_loading is False
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_refresh_runs_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        def get_all_facts(self):
            raise AssertionError("refresh should not run on the UI thread")

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel.refresh_facts()

        assert started
        assert started[0]["name"] == "wisp-memory-refresh"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_read_only_hides_mutation_controls():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)

    class FakeManager:
        def get_all_facts(self):
            return []

    panel = MemoryPanel(
        FakeManager(),
        initial_facts=[
            {"id": "fact-1", "text": "I prefer stable settings", "category": "general"}
        ],
        read_only=True,
    )

    try:
        assert not hasattr(panel, "_add_text")
        button_texts = {button.text() for button in panel.findChildren(QPushButton)}
        assert "Add" not in button_texts
        assert "X" not in button_texts
        assert "Refresh" in button_texts
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_add_runs_on_background_thread(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        def add_fact_manual(self, _text, _category):
            raise AssertionError("add should not run on the UI thread")

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel._add_text.setText("I prefer fast settings")
        panel._on_add_fact()

        assert panel._add_text.text() == ""
        assert started
        assert started[0]["name"] == "wisp-memory-add"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()
