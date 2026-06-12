"""
ui/plugin_manager.py -- Plugin Manager dialog.

Shows all loaded mods, their status, and any tray actions they expose.
Opened from the tray menu via "Plugin Manager...".
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame, QCheckBox, QLineEdit, QComboBox, QFormLayout,
)
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen


_TRUE = {"1", "true", "yes", "on"}


class PluginManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugin Manager")
        self.setModal(False)
        enable_standard_window_controls(self)
        self._build_ui()
        fit_window_to_screen(self, preferred_width=480, preferred_height=400)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        # Header
        title = QLabel("Mods")
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel(
            "Mods are Python packages in the <code>plugins/</code> folder. "
            "They extend the app with lifecycle hooks, tray actions, and model tools."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 9pt; opacity: 0.7;")
        root.addWidget(subtitle)

        # Scrollable mod list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        try:
            from core.plugin_manager import get_manager
            self._manager = get_manager()
            mods = self._manager._mods  # access internal list for display
        except RuntimeError:
            self._manager = None
            mods = []

        if not mods:
            empty = QLabel("No mods loaded. Drop a folder with __init__.py into plugins/.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.5; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for mod in mods:
                inner_layout.addWidget(self._mod_card(mod))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # Footer buttons
        footer = QHBoxLayout()
        footer.setSpacing(8)

        open_btn = QPushButton("Open plugins folder")
        open_btn.clicked.connect(self._open_plugins_folder)
        footer.addWidget(open_btn)
        footer.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def _mod_card(self, mod) -> QFrame:
        card = QFrame()
        card.setObjectName("modCard")
        card.setStyleSheet("""
            QFrame#modCard {
                border: 1px solid rgba(128,128,128,0.25);
                border-radius: 8px;
                padding: 2px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Mod name row, with an enable toggle on the right
        name_row = QHBoxLayout()
        name_lbl = QLabel(mod.name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        enable = QCheckBox("Enabled")
        enable.setChecked(bool(getattr(mod, "enabled", True)))
        name_row.addWidget(enable)
        layout.addLayout(name_row)

        # Module file path as subtitle
        mod_file = getattr(mod.module, "__file__", None)
        if mod_file:
            path_lbl = QLabel(str(Path(mod_file).parent))
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        # Hooks summary
        hook_names = [
            h for h in ("on_startup", "on_shutdown", "before_query",
                         "after_response", "get_tools", "get_tray_actions",
                         "get_settings", "get_system_prompt_section")
            if hasattr(mod.module, h)
        ]
        if hook_names:
            hooks_lbl = QLabel("Hooks: " + ", ".join(hook_names))
            hooks_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(hooks_lbl)

        # Model tools contributed
        try:
            fn = getattr(mod.module, "get_tools", None)
            tools = fn() if fn else []
        except Exception:
            tools = []
        if tools:
            tool_names = [t.get("name", "?") for t in tools if isinstance(t, dict)]
            tools_lbl = QLabel("Model tools: " + ", ".join(tool_names))
            tools_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(tools_lbl)

        # Per-mod settings (replaces the old tray-action buttons)
        settings = []
        if self._manager is not None:
            settings = self._manager.get_settings(mod.name)
        settings_box = self._settings_box(mod.name, settings)
        if settings_box is not None:
            settings_box.setEnabled(enable.isChecked())
            layout.addWidget(settings_box)

        # Toggle enable; grey out settings when disabled
        def _on_toggle(checked: bool, _name=mod.name, _box=settings_box):
            if self._manager is not None:
                self._manager.set_enabled(_name, checked)
            if _box is not None:
                _box.setEnabled(checked)
        enable.toggled.connect(_on_toggle)

        return card

    def _settings_box(self, mod_name: str, settings: list) -> QWidget | None:
        if not settings:
            return None
        box = QFrame()
        form = QFormLayout(box)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)
        for s in settings:
            key = str(s.get("key", "")).strip()
            if not key:
                continue
            label = str(s.get("label") or key)
            stype = str(s.get("type") or "text").lower()
            value = s.get("value")
            widget = self._setting_widget(mod_name, key, stype, value, s.get("options") or [])
            if widget is None:
                continue
            help_text = str(s.get("help") or "")
            if help_text:
                widget.setToolTip(help_text)
            form.addRow(label, widget)
        return box

    def _setting_widget(self, mod_name, key, stype, value, options):
        def _save(v):
            if self._manager is not None:
                self._manager.set_setting(mod_name, key, v)

        if stype == "bool":
            cb = QCheckBox()
            cb.setChecked(str(value).strip().lower() in _TRUE)
            cb.toggled.connect(lambda checked: _save("true" if checked else "false"))
            return cb
        if stype == "choice" and options:
            combo = QComboBox()
            opts = [str(o) for o in options]
            combo.addItems(opts)
            if str(value) in opts:
                combo.setCurrentText(str(value))
            combo.currentTextChanged.connect(_save)
            return combo
        # text / number → line edit, persisted on edit-finished
        edit = QLineEdit("" if value is None else str(value))
        if stype == "number":
            edit.setPlaceholderText("number")
        edit.editingFinished.connect(lambda e=edit: _save(e.text()))
        return edit

    @staticmethod
    def _open_plugins_folder():
        from core.system.paths import PLUGINS_DIR
        path = str(PLUGINS_DIR)
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])


_dialog_instance: PluginManagerDialog | None = None


def open_plugin_manager(parent=None):
    global _dialog_instance
    # Don't parent to the floating icon overlay (a Qt.Tool / NSPanel window):
    # attaching a normal child window to it crashes Cocoa on show(). Match the
    # settings dialog — only Linux keeps the parent. See ui/settings_panel/dialog.py.
    dialog_parent = parent if sys.platform.startswith("linux") else None
    if _dialog_instance is None or not _dialog_instance.isVisible():
        _dialog_instance = PluginManagerDialog(dialog_parent)
    _dialog_instance.show()
    _dialog_instance.raise_()
    _dialog_instance.activateWindow()
