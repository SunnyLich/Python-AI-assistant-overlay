"""Addon Manager dialog."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame, QCheckBox, QLineEdit, QComboBox, QFormLayout,
    QTextEdit, QMessageBox, QFileDialog,
)
from ui.i18n import t
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen


_TRUE = {"1", "true", "yes", "on"}


def _runtime_action_label(runtime: dict) -> str:
    """Return the dependency action label for an addon."""
    if runtime.get("needs_approval"):
        return t("Approve env")
    return t("Repair env") if runtime.get("ready") else t("Install env")


def _runtime_summary(runtime: dict) -> str:
    """Return a short dependency status summary for an addon."""
    if runtime.get("needs_approval"):
        return t("Dependency env: needs approval")
    return t("Dependency env: ready") if runtime.get("ready") else t("Dependency env: needs install")


def _expanding_form_layout(parent: QWidget | None = None) -> QFormLayout:
    """Create a form layout that lets field widgets grow."""
    form = QFormLayout(parent)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


class AddonManagerDialog(QDialog):
    """Qt dialog for managing installed addons."""
    def __init__(self, parent=None):
        """Initialize the addon manager dialog instance."""
        super().__init__(parent)
        self.setWindowTitle(t("Addon Manager"))
        self.setModal(False)
        self._settings_dialogs: dict[str, AddonSettingsDialog] = {}
        self._log_dialogs: dict[str, AddonLogDialog] = {}
        enable_standard_window_controls(self)
        self._build_ui()
        fit_window_to_screen(self, preferred_width=620, preferred_height=500)

    def _build_ui(self):
        """Build ui."""
        root = self.layout()
        if root is None:
            root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(t("Addons"))
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel(
            t("Addons are Python packages in the <code>addons/</code> folder. "
              "Each addon runs in its own host process.")
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
            from core.addon_manager import get_manager
            self._manager = get_manager()
            addons = self._manager.summaries() if hasattr(self._manager, "summaries") else []
        except RuntimeError:
            self._manager = None
            addons = []

        if not addons:
            empty = QLabel(t("No addons loaded. Drop a folder with addon.toml into addons/."))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.5; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for addon in addons:
                inner_layout.addWidget(self._addon_card(addon))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # Footer buttons
        footer = QHBoxLayout()
        footer.setSpacing(8)

        open_btn = QPushButton(t("Open addons folder"))
        open_btn.clicked.connect(self._open_addons_folder)
        footer.addWidget(open_btn)
        install_btn = QPushButton(t("Install archive"))
        install_btn.clicked.connect(self._install_archive)
        footer.addWidget(install_btn)
        install_folder_btn = QPushButton(t("Install folder"))
        install_folder_btn.clicked.connect(self._install_folder)
        footer.addWidget(install_folder_btn)
        footer.addStretch()

        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def _addon_card(self, addon: dict) -> QFrame:
        """Build a card for one addon."""
        card = QFrame()
        card.setObjectName("addonCard")
        card.setStyleSheet("""
            QFrame#addonCard {
                border: 1px solid #55555f;
                border-radius: 8px;
                padding: 2px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        name_row = QHBoxLayout()
        name = str(addon.get("name") or addon.get("id") or t("Addon"))
        addon_id = str(addon.get("id") or name)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        enable = QCheckBox(t("Enabled"))
        enable.setChecked(bool(addon.get("enabled", True)))
        settings = addon.get("settings") or []
        logs = str(addon.get("logs") or "")
        runtime = addon.get("runtime") if isinstance(addon.get("runtime"), dict) else {}
        packages = [str(p) for p in (runtime.get("packages") or [])]
        has_dependencies = str(runtime.get("tier") or "1") == "2"

        settings_btn = QPushButton(t("Settings"))
        settings_btn.setToolTip(t("Open this addon's settings"))

        def _open_addon_settings(_checked=False, _id=addon_id, _name=name, _settings=settings):
            """Open addon settings."""
            self._open_settings_window(_id, _name, _settings)

        settings_btn.clicked.connect(_open_addon_settings)
        name_row.addWidget(settings_btn)

        logs_btn = QPushButton(t("Logs"))
        logs_btn.setToolTip(t("Open this addon's diagnostic log"))

        def _open_addon_logs(_checked=False, _id=addon_id, _name=name, _logs=logs):
            """Open addon logs."""
            self._open_log_window(_id, _name, _logs)

        logs_btn.clicked.connect(_open_addon_logs)
        name_row.addWidget(logs_btn)

        if has_dependencies:
            repair_btn = QPushButton(_runtime_action_label(runtime))
            repair_btn.setToolTip(t("Install or rebuild this addon's dependency environment"))

            def _repair_addon_env(_checked=False, _id=addon_id, _name=name, _runtime=runtime):
                """Install or rebuild this addon's dependency environment."""
                self._repair_environment(_id, _name, _runtime)

            repair_btn.clicked.connect(_repair_addon_env)
            name_row.addWidget(repair_btn)
        name_row.addWidget(enable)
        layout.addLayout(name_row)

        description = str(addon.get("description") or "")
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
            layout.addWidget(desc_lbl)

        path = str(addon.get("path") or "")
        if path:
            path_lbl = QLabel(path)
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        hook_names = [str(h) for h in (addon.get("hooks") or [])]
        if hook_names:
            hooks_lbl = QLabel(t("Hooks: ") + ", ".join(hook_names))
            hooks_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(hooks_lbl)

        tools = [str(t) for t in (addon.get("tools") or [])]
        if tools:
            tools_lbl = QLabel(t("Model tools: ") + ", ".join(tools))
            tools_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(tools_lbl)

        permissions = addon.get("permissions") or {}
        if permissions:
            perms_lbl = QLabel(t("Permissions: ") + ", ".join(sorted(str(k) for k in permissions.keys())))
            perms_lbl.setStyleSheet("font-size: 8pt; opacity: 0.55;")
            layout.addWidget(perms_lbl)

        if has_dependencies:
            dep_parts = [_runtime_summary(runtime)]
            if packages:
                dep_parts.append(t("Packages: ") + ", ".join(packages))
            runtime_error = str(runtime.get("error") or "")
            if runtime_error:
                dep_parts.append(runtime_error)
            dep_lbl = QLabel("\n".join(dep_parts))
            dep_lbl.setWordWrap(True)
            dep_lbl.setStyleSheet("font-size: 8pt; opacity: 0.6;")
            layout.addWidget(dep_lbl)

        error = str(addon.get("error") or "")
        if error:
            err_lbl = QLabel(error.splitlines()[-1] if "\n" in error else error)
            err_lbl.setWordWrap(True)
            err_lbl.setStyleSheet("font-size: 8pt; color: #b00020;")
            layout.addWidget(err_lbl)

        def _on_toggle(checked: bool, _name=addon_id):
            """Handle toggle events."""
            if self._manager is not None:
                self._manager.set_enabled(_name, checked)
        enable.toggled.connect(_on_toggle)

        return card

    def _open_settings_window(self, addon_id: str, addon_name: str, fallback_settings: list) -> None:
        """Open settings window."""
        settings = fallback_settings
        if self._manager is not None:
            settings = self._manager.get_settings(addon_id)

        dialog = self._settings_dialogs.get(addon_id)
        if dialog is None or not dialog.isVisible():
            dialog = AddonSettingsDialog(
                manager=self._manager,
                addon_id=addon_id,
                addon_name=addon_name,
                settings=settings,
                parent=self,
            )
            dialog.destroyed.connect(lambda _obj=None, _id=addon_id: self._settings_dialogs.pop(_id, None))
            self._settings_dialogs[addon_id] = dialog
        else:
            dialog.reload_settings(settings)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _repair_environment(self, addon_id: str, addon_name: str, runtime: dict) -> None:
        """Install or rebuild an addon dependency environment."""
        if self._manager is None:
            return
        if not self._confirm_environment_install(addon_name, runtime):
            return
        try:
            status = self._manager.repair_environment(addon_id)
        except Exception as exc:
            QMessageBox.warning(self, t("Addon Environment"), str(exc))
            return
        if status.get("ready"):
            QMessageBox.information(self, t("Addon Environment"), t("Dependency environment is ready."))
        else:
            QMessageBox.warning(
                self,
                t("Addon Environment"),
                str(status.get("error") or t("Dependency environment is not ready.")),
            )

    def _install_archive(self) -> None:
        """Install archive."""
        archive, _selected_filter = QFileDialog.getOpenFileName(
            self,
            t("Install Addon Archive"),
            "",
            t("Wisp Addons (*.wisp *.zip)"),
        )
        if not archive:
            return
        try:
            from core.addon_distribution import install_addon_archive

            result = install_addon_archive(Path(archive), replace=False)
            if self._manager is not None:
                self._manager.load_all()
            self._refresh()
            QMessageBox.information(self, t("Addon Installed"), f"{t('Installed addon:')} {result.get('id')}")
        except Exception as exc:
            QMessageBox.warning(self, t("Addon Install Failed"), str(exc))

    def _install_folder(self) -> None:
        """Install folder."""
        folder = QFileDialog.getExistingDirectory(
            self,
            t("Install Addon Folder"),
            "",
        )
        if not folder:
            return
        try:
            from core.addon_distribution import install_addon_folder

            result = install_addon_folder(Path(folder), replace=False)
            if self._manager is not None:
                self._manager.load_all()
            self._refresh()
            QMessageBox.information(self, t("Addon Installed"), f"{t('Installed addon:')} {result.get('id')}")
        except Exception as exc:
            QMessageBox.warning(self, t("Addon Install Failed"), str(exc))

    def _refresh(self) -> None:
        """Refresh the addon manager dialog workflow."""
        layout = self.layout()
        AddonSettingsDialog._clear_layout(layout)
        self._build_ui()

    def _confirm_environment_install(self, addon_name: str, runtime: dict) -> bool:
        """Ask before installing an addon's declared dependency environment."""
        packages = [str(p) for p in (runtime.get("packages") or [])]
        lines = [
            f"{addon_name} {t('declares Python/package dependencies.')}",
            "",
            f"{t('Python: ')}{runtime.get('python_requirement') or t('current runtime')}",
            t("Packages:"),
        ]
        lines.extend(f"  {package}" for package in packages)
        if not packages:
            lines.append(f"  {t('No packages declared')}")
        env_path = str(runtime.get("env_path") or "")
        if env_path:
            lines.extend(["", f"{t('Environment: ')}{env_path}"])
        lines.extend(["", t("Install or rebuild this environment now?")])
        return QMessageBox.question(
            self,
            t("Approve Addon Dependencies"),
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _open_log_window(self, addon_id: str, addon_name: str, logs: str) -> None:
        """Open log window."""
        latest_logs = logs
        if self._manager is not None:
            for addon in self._manager.summaries():
                if str(addon.get("id") or addon.get("name") or "") == addon_id:
                    latest_logs = str(addon.get("logs") or "")
                    break

        dialog = self._log_dialogs.get(addon_id)
        if dialog is None or not dialog.isVisible():
            dialog = AddonLogDialog(addon_name=addon_name, logs=latest_logs, parent=self)
            dialog.destroyed.connect(lambda _obj=None, _id=addon_id: self._log_dialogs.pop(_id, None))
            self._log_dialogs[addon_id] = dialog
        else:
            dialog.reload_logs(latest_logs)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @staticmethod
    def _open_addons_folder():
        """Open addons folder."""
        from core.system.paths import ADDONS_DIR
        path = str(ADDONS_DIR)
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])


class AddonSettingsDialog(QDialog):
    """Qt dialog for addon settings dialog."""
    def __init__(self, *, manager, addon_id: str, addon_name: str, settings: list, parent=None):
        """Initialize the addon settings dialog instance."""
        super().__init__(parent)
        self._manager = manager
        self._addon_id = addon_id
        self._addon_name = addon_name
        self._settings = settings
        self.setWindowTitle(f"{addon_name} {t('Settings')}")
        self.setModal(False)
        enable_standard_window_controls(self)
        self._build_ui()
        fit_window_to_screen(self, preferred_width=560, preferred_height=460)

    def reload_settings(self, settings: list) -> None:
        """Handle reload settings for addon settings dialog."""
        self._settings = settings
        self._clear_layout(self.layout())
        self._build_ui()

    def _build_ui(self) -> None:
        """Build ui."""
        root = self.layout()
        if root is None:
            root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{self._addon_name} {t('Settings')}")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        settings_box = self._settings_box(self._settings)
        if settings_box is None:
            empty = QLabel(t("This addon does not expose settings."))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.55; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            inner_layout.addWidget(settings_box)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def _settings_box(self, settings: list) -> QWidget | None:
        """Handle settings box for addon settings dialog."""
        if not settings:
            return None
        box = QFrame()
        form = _expanding_form_layout(box)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)
        for s in settings:
            key = str(s.get("key", "")).strip()
            if not key:
                continue
            label = str(s.get("label") or key)
            stype = str(s.get("type") or "text").lower()
            value = s.get("value")
            widget = self._setting_widget(key, stype, value, s.get("options") or [])
            if widget is None:
                continue
            help_text = str(s.get("help") or "")
            if help_text:
                widget.setToolTip(help_text)
            form.addRow(label, widget)
        return box if form.rowCount() else None

    def _setting_widget(self, key, stype, value, options):
        """Handle setting widget for addon settings dialog."""
        def _save(v):
            """Save the addon settings dialog workflow."""
            if self._manager is not None:
                self._manager.set_setting(self._addon_id, key, v)

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
            edit.setPlaceholderText(t("number"))
        edit.editingFinished.connect(lambda e=edit: _save(e.text()))
        return edit

    @staticmethod
    def _clear_layout(layout) -> None:
        """Clear layout."""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                AddonSettingsDialog._clear_layout(child_layout)
            if widget is not None:
                widget.deleteLater()


class AddonLogDialog(QDialog):
    """Qt dialog for addon log dialog."""
    def __init__(self, *, addon_name: str, logs: str, parent=None):
        """Initialize the addon log dialog instance."""
        super().__init__(parent)
        self._addon_name = addon_name
        self.setWindowTitle(f"{addon_name} {t('Logs')}")
        self.setModal(False)
        enable_standard_window_controls(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{self._addon_name} {t('Logs')}")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        self._logs = QTextEdit()
        self._logs.setReadOnly(True)
        self._logs.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._logs, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self.reload_logs(logs)
        fit_window_to_screen(self, preferred_width=680, preferred_height=460)

    def reload_logs(self, logs: str) -> None:
        """Handle reload logs for addon log dialog."""
        self._logs.setPlainText(logs or t("No log output yet."))
        self._logs.moveCursor(QTextCursor.MoveOperation.End)


_dialog_instance: AddonManagerDialog | None = None


def open_addon_manager(parent=None):
    """Open addon manager."""
    global _dialog_instance
    # Don't parent to the floating icon overlay (a Qt.Tool / NSPanel window):
    # attaching a normal child window to it crashes Cocoa on show(). Match the
    # settings dialog — only Linux keeps the parent. See ui/settings_panel/dialog.py.
    dialog_parent = parent if sys.platform.startswith("linux") else None
    if _dialog_instance is None or not _dialog_instance.isVisible():
        _dialog_instance = AddonManagerDialog(dialog_parent)
    _dialog_instance.show()
    _dialog_instance.raise_()
    _dialog_instance.activateWindow()
