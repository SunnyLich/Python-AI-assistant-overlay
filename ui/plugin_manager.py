"""Addon Manager dialog."""
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
        self.setWindowTitle("Addon Manager")
        self.setModal(False)
        enable_standard_window_controls(self)
        self._build_ui()
        fit_window_to_screen(self, preferred_width=480, preferred_height=400)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Addons")
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel(
            "Addons are Python packages in the <code>addons/</code> folder. "
            "Each addon runs in its own host process."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 9pt; opacity: 0.7;")
        root.addWidget(subtitle)

        # Scrollable addon list
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
            plugins = self._manager.summaries() if hasattr(self._manager, "summaries") else []
        except RuntimeError:
            self._manager = None
            plugins = []

        if not plugins:
            empty = QLabel("No addons loaded. Drop a folder with addon.toml into addons/.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.5; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for plugin in plugins:
                inner_layout.addWidget(self._mod_card(plugin))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # Footer buttons
        footer = QHBoxLayout()
        footer.setSpacing(8)

        open_btn = QPushButton("Open addons folder")
        open_btn.clicked.connect(self._open_plugins_folder)
        footer.addWidget(open_btn)
        footer.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def _mod_card(self, plugin: dict) -> QFrame:
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

        name_row = QHBoxLayout()
        name = str(plugin.get("name") or plugin.get("id") or "Addon")
        addon_id = str(plugin.get("id") or name)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        enable = QCheckBox("Enabled")
        enable.setChecked(bool(plugin.get("enabled", True)))
        name_row.addWidget(enable)
        layout.addLayout(name_row)

        description = str(plugin.get("description") or "")
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
            layout.addWidget(desc_lbl)

        path = str(plugin.get("path") or "")
        if path:
            path_lbl = QLabel(path)
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        hook_names = [str(h) for h in (plugin.get("hooks") or [])]
        if hook_names:
            hooks_lbl = QLabel("Hooks: " + ", ".join(hook_names))
            hooks_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(hooks_lbl)

        tools = [str(t) for t in (plugin.get("tools") or [])]
        if tools:
            tools_lbl = QLabel("Model tools: " + ", ".join(tools))
            tools_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(tools_lbl)

        permissions = plugin.get("permissions") or {}
        if permissions:
            perms_lbl = QLabel("Permissions: " + ", ".join(sorted(str(k) for k in permissions.keys())))
            perms_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(perms_lbl)

        settings_box = self._settings_box(addon_id, plugin.get("settings") or [])
        if settings_box is not None:
            settings_box.setEnabled(enable.isChecked())
            layout.addWidget(settings_box)

        error = str(plugin.get("error") or "")
        if error:
            err_lbl = QLabel(error.splitlines()[-1] if "\n" in error else error)
            err_lbl.setWordWrap(True)
            err_lbl.setStyleSheet("font-size: 8pt; color: #b00020;")
            layout.addWidget(err_lbl)

        def _on_toggle(checked: bool, _name=addon_id, _box=settings_box):
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
        from core.system.paths import ADDONS_DIR
        path = str(ADDONS_DIR)
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
