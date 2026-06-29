"""Shared context-control widgets for settings-panel invocation rows."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.system.env_utils import normalize_file_access_mode
from ui.i18n import t
from ui.settings_panel.helpers import (
    NoScrollCombo,
    context_mode_combo,
)

FILE_ACCESS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Off", "off"),
    ("Read only", "read"),
    ("Ask before writing", "ask"),
    ("Write automatically", "auto"),
)


class BooleanContextCombo(NoScrollCombo):
    """Two-state context dropdown that preserves the old checkbox API."""

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.addItem(t("Off"), "false")
        self.addItem(t("On"), "true")
        self.setChecked(checked)

    def isChecked(self) -> bool:  # noqa: N802 - checkbox compatibility
        return str(self.currentData()).lower() == "true"

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - checkbox compatibility
        idx = self.findData("true" if checked else "false")
        self.setCurrentIndex(idx if idx >= 0 else 0)


class AppContextCombo(NoScrollCombo):
    """Single App-context dropdown backed by ambient + open-docs settings."""

    _AMBIENT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, ambient: bool = True, documents_mode: str = "auto") -> None:
        super().__init__()
        self.addItem(t("Off"), "off")
        self.setItemData(0, False, self._AMBIENT_ROLE)
        self.addItem(t("On"), "off")
        self.setItemData(1, True, self._AMBIENT_ROLE)
        self.addItem(t("On + open docs"), "auto")
        self.setItemData(2, True, self._AMBIENT_ROLE)
        self.addItem(t("Let model decide"), "model")
        self.setItemData(3, True, self._AMBIENT_ROLE)
        self.set_state(ambient, documents_mode)

    def isChecked(self) -> bool:  # noqa: N802 - checkbox compatibility
        return bool(self.itemData(self.currentIndex(), self._AMBIENT_ROLE))

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - checkbox compatibility
        self.set_state(checked, str(self.currentData() or "off"))

    def set_state(self, ambient: bool, documents_mode: str) -> None:
        """Select the combined item for ambient app context + documents mode."""
        mode = (documents_mode or "off").strip().lower()
        if mode == "on":
            mode = "auto"
        if not ambient:
            target = 0
        elif mode == "model":
            target = 3
        elif mode == "auto":
            target = 2
        else:
            target = 1
        self.setCurrentIndex(target)

    def findData(  # noqa: N802 - Qt override shape
        self,
        data,
        role: int = int(Qt.ItemDataRole.UserRole),
        flags: Qt.MatchFlag = Qt.MatchFlag.MatchExactly,
    ) -> int:
        """Pick the docs-mode item while preserving current ambient state."""
        if role == int(Qt.ItemDataRole.UserRole) and str(data).lower() == "off":
            wants_ambient = self.isChecked()
            for index in range(self.count()):
                if (
                    str(self.itemData(index, role)).lower() == "off"
                    and bool(self.itemData(index, self._AMBIENT_ROLE)) == wants_ambient
                ):
                    return index
            return 1 if wants_ambient else 0
        return super().findData(data, role, flags)


def _set_combo_value(combo, value: str) -> None:
    """Set a combo by data/text, matching the settings dialog helper behavior."""
    if value == "auto" and combo.property("legacy_auto_means_on"):
        value = "on"
    idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
        return
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    elif combo.isEditable():
        combo.setCurrentText(value)


def context_source_block(label: str, key_index: int, keys: str, *controls: QWidget) -> QFrame:
    """Create a compact context source block with the overlay key embedded."""
    frame = QFrame()
    frame.setObjectName("contextSourceBlock")
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    frame.setStyleSheet(
        """
        QFrame#contextSourceBlock {
            border: 1px solid palette(mid);
            border-radius: 4px;
        }
        QFrame#contextSourceBlock QLabel {
            border: none;
            background: transparent;
        }
        """
    )
    frame.setMinimumSize(160, 112)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    box = QVBoxLayout(frame)
    box.setContentsMargins(8, 6, 8, 6)
    box.setSpacing(3)
    title = QLabel(t(label))
    title.setStyleSheet("font-weight: 600;")
    box.addWidget(title)
    if key_index >= 0:
        key_text = keys[key_index] if key_index < len(keys) else ""
        key_label = QLabel(f"{t('Keys:')} {key_text}")
        key_label.setStyleSheet("color: palette(placeholder-text);")
        box.addWidget(key_label)
    for control in controls:
        control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box.addWidget(control)
    box.addStretch()
    return frame


def build_context_controls(
    *,
    intent_context_keys: str,
    on_changed: Callable[[], None],
    context_ambient: bool = True,
    context_clipboard: bool = False,
    context_documents_mode: str = "auto",
    context_browser_mode: str = "off",
    context_github_mode: str = "off",
    context_memory_mode: str = "off",
    context_screenshot: str = "off",
    file_access: str = "off",
    screenshot_enabled: bool = True,
) -> tuple[QWidget, dict]:
    """Build the shared per-hotkey context grid."""
    context_row = QWidget()
    context_h = QGridLayout(context_row)
    context_h.setContentsMargins(0, 0, 0, 0)
    context_h.setHorizontalSpacing(8)
    context_h.setVerticalSpacing(6)
    app_tip = (
        "App context:\n"
        "Off - do not include nearby app/window context or open documents.\n"
        "On - include nearby app/window context only.\n"
        "On + open docs - include nearby app/window context and read supported open documents.\n"
        "Let model decide - include nearby app/window context and expose an open-document tool."
    )
    clipboard_tip = (
        "Clipboard:\n"
        "Off - do not include clipboard text.\n"
        "On - include clipboard text with this query."
    )
    browser_tip = (
        "Browser/Web:\n"
        "Off - no web/browser tools.\n"
        "On - read the current browser page before sending the prompt.\n"
        "Let model decide - expose web search and browser page fetch tools."
    )
    github_tip = (
        "Git/GitHub:\n"
        "Off - no git or GitHub tools.\n"
        "On - read local git status and diff before sending the prompt.\n"
        "Let model decide - expose git status/diff and GitHub repo/issue tools."
    )
    memory_tip = (
        "Memory:\n"
        "Off - do not use stored facts for this caller.\n"
        "On - fetch relevant stored facts before sending the prompt.\n"
        "Let model decide - expose a memory search tool during the answer."
    )
    screenshot_tip = (
        "Screenshot of your screen:\n"
        "Off - never capture.\n"
        "On - capture at hotkey time and send it with the query.\n"
        "Let model decide - expose a screenshot tool during the answer."
    )
    file_tip = (
        "Local files:\n"
        "Off - do not expose file tools.\n"
        "Read only - allow listing and reading configured folders.\n"
        "Ask before writing - show a diff before edits or creates.\n"
        "Write automatically - apply edits without asking."
    )
    app_combo = AppContextCombo(context_ambient, context_documents_mode)
    app_combo.setToolTip(app_tip)
    clipboard_combo = BooleanContextCombo(context_clipboard)
    clipboard_combo.setToolTip(clipboard_tip)
    browser_combo = context_mode_combo(context_browser_mode, allow_auto=True)
    github_combo = context_mode_combo(context_github_mode, allow_auto=True)
    memory_combo = context_mode_combo(context_memory_mode, allow_auto=True, on_value="on")
    memory_combo.setProperty("legacy_auto_means_on", True)
    screenshot_combo = context_mode_combo(context_screenshot, allow_auto=True)
    if not screenshot_enabled:
        _set_combo_value(screenshot_combo, "off")
        screenshot_combo.setEnabled(False)
    file_combo = NoScrollCombo()
    for label, value in FILE_ACCESS_OPTIONS:
        file_combo.addItem(t(label), value)
    _set_combo_value(file_combo, normalize_file_access_mode(file_access))
    title = QLabel(t("Context"))
    title.setStyleSheet("font-weight: 600; color: palette(placeholder-text);")
    context_h.addWidget(title, 0, 0, 1, 4)
    context_h.addWidget(context_source_block("App", 0, intent_context_keys, app_combo), 1, 0)
    context_h.addWidget(context_source_block("Browser/Web", 1, intent_context_keys, browser_combo), 1, 1)
    context_h.addWidget(context_source_block("Clipboard", 3, intent_context_keys, clipboard_combo), 1, 2)
    context_h.addWidget(context_source_block("Screenshot", 4, intent_context_keys, screenshot_combo), 1, 3)
    context_h.addWidget(context_source_block("Git/GitHub", 5, intent_context_keys, github_combo), 2, 0)
    context_h.addWidget(context_source_block("Memory", 6, intent_context_keys, memory_combo), 2, 1)
    context_h.addWidget(context_source_block("Local files", 7, intent_context_keys, file_combo), 2, 2)
    for column in range(4):
        context_h.setColumnStretch(column, 1)
    browser_combo.setToolTip(browser_tip)
    github_combo.setToolTip(github_tip)
    memory_combo.setToolTip(memory_tip)
    screenshot_combo.setToolTip(screenshot_tip)
    if not screenshot_enabled:
        screenshot_combo.setToolTip(
            "Region snips already attach the selected image; extra screenshot context is disabled."
        )
    file_combo.setToolTip(file_tip)
    controls = {
        "context_ambient": app_combo,
        "context_clipboard": clipboard_combo,
        "context_documents_mode": app_combo,
        "context_browser_mode": browser_combo,
        "context_github_mode": github_combo,
        "context_memory_mode": memory_combo,
        "context_screenshot": screenshot_combo,
        "file_access": file_combo,
    }
    for combo in (
        app_combo,
        clipboard_combo,
        browser_combo,
        github_combo,
        memory_combo,
        screenshot_combo,
        file_combo,
    ):
        combo.currentIndexChanged.connect(lambda _: on_changed())
    return context_row, controls
