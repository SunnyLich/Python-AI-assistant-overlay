"""
ui/memory_viewer.py -” Long-term memory viewer / editor.

Provides two surfaces:

  MemoryPanel(QWidget)
      Embeddable widget containing the full fact browser (scroll area + add-fact
      row + refresh button).  Used both inside MemoryViewer and as the embedded
      panel in the Settings â†’ Memory tab.

  MemoryViewer(QDialog)
      Standalone dialog that wraps MemoryPanel.  Opened from the tray menu.

Changes are written through to the MemoryManager immediately on each action;
no separate "Save" step is required.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from ui.i18n import t
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from core.memory_store.store import MemoryManager

_CATEGORIES = ("project_context", "general")
_CAT_LABELS = {
    "project_context": "Project",
    "general":         "General",
}
_memory_log = logging.getLogger("wisp.memory_viewer")


def _load_projects() -> list[dict]:
    """Named projects for the scope selectors (best-effort, never raises)."""
    try:
        from core.conversation_store import store as conversation_store
        return conversation_store.load_projects()
    except Exception:
        return []


def _build_project_combo(projects: list[dict] | None) -> QComboBox:
    """A scope dropdown: 'General' (data "") plus each named project (data id)."""
    from core.conversation_store.store import GENERAL_PROJECT_ID
    combo = QComboBox()
    combo.addItem(t("General"), userData="")
    for proj in (projects or []):
        pid = str(proj.get("id") or "")
        if not pid or pid == GENERAL_PROJECT_ID:
            continue
        combo.addItem(proj.get("name", pid), userData=pid)
    return combo


class _MemoryPanelSignals(QObject):
    loaded = Signal(int, object, str)
    mutation_done = Signal(str)


class _FactRow(QWidget):
    """A single editable fact row: [text input] [category] [delete]."""

    def __init__(
        self, fact: dict, manager: "MemoryManager", parent: QWidget, *,
        read_only: bool = False, projects: list[dict] | None = None,
    ):
        super().__init__(parent)
        self._fact_id = fact["id"]
        self._manager = manager
        self._read_only = read_only

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self._text_edit = QLineEdit(fact["text"])
        self._text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._text_edit.setReadOnly(read_only)
        if not read_only:
            self._text_edit.editingFinished.connect(self._on_text_changed)
        layout.addWidget(self._text_edit)

        # Scope selector: "General" (global) + each named project. The selected
        # data is the project id (or "" for General); category is derived.
        self._proj_combo = _build_project_combo(projects)
        current = str(fact.get("project") or "")
        idx = self._proj_combo.findData(current)
        self._proj_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._proj_combo.setFixedWidth(160)
        self._proj_combo.setEnabled(not read_only)
        if not read_only:
            self._proj_combo.currentIndexChanged.connect(self._on_scope_changed)
        layout.addWidget(self._proj_combo)

        if not read_only:
            del_btn = QPushButton("X")
            del_btn.setFixedWidth(40)
            del_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")
            del_btn.setToolTip(
                f"{t('Delete this fact')} ({t('source')}: {fact.get('source', t('unknown'))})"
            )
            del_btn.clicked.connect(self._on_delete)
            layout.addWidget(del_btn)

    def _on_text_changed(self) -> None:
        self._save_fact()

    def _on_scope_changed(self) -> None:
        self._save_fact()

    def _save_fact(self) -> None:
        new_text = self._text_edit.text().strip()
        if not new_text:
            return
        project = self._proj_combo.currentData() or ""
        self._run_background(
            lambda: self._manager.update_fact(self._fact_id, new_text, project=project),
            "update",
        )

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            t("Delete fact"),
            f"{t('Delete')}: \"{self._text_edit.text()}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._run_background(
                lambda: self._manager.delete_fact(self._fact_id),
                "delete",
            )
            parent = self.parent()
            if parent is not None:
                layout = parent.layout()
                if layout is not None:
                    layout.removeWidget(self)
            self.deleteLater()

    def _run_background(self, fn, action: str) -> None:
        def worker() -> None:
            try:
                fn()
            except Exception as exc:
                _memory_log.warning("Memory %s failed: %s", action, exc)

        threading.Thread(
            target=worker,
            name=f"wisp-memory-{action}",
            daemon=True,
        ).start()


class MemoryPanel(QWidget):
    """
    Embeddable widget: scrollable fact browser + add-fact row + refresh button.

    Used as the content of MemoryViewer (dialog) and embedded directly in the
    Settings â†’ Memory tab.
    """

    def __init__(
        self,
        manager: "MemoryManager",
        parent=None,
        initial_facts: list[dict] | None = None,
        *,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self._manager = manager
        self._read_only = read_only
        self._projects = _load_projects()
        self._load_token = 0
        self._loading = False
        self._refresh_btn = None
        self._signals = _MemoryPanelSignals(self)
        self._signals.loaded.connect(self._on_facts_loaded)
        self._signals.mutation_done.connect(self._on_mutation_done)
        self._build_ui()
        self._load_facts(initial_facts)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(0, 0, 0, 0)

        # Scroll area for facts
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(self._scroll_area, stretch=1)

        if not self._read_only:
            # --- Add fact row ---
            add_frame = QFrame()
            add_frame.setFrameShape(QFrame.Shape.StyledPanel)
            add_layout = QHBoxLayout(add_frame)
            add_layout.setContentsMargins(6, 6, 6, 6)

            add_lbl = QLabel(t("Add fact:"))
            add_layout.addWidget(add_lbl)

            self._add_text = QLineEdit()
            self._add_text.setPlaceholderText(t("Enter a new fact..."))
            self._add_text.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self._add_text.returnPressed.connect(self._on_add_fact)
            add_layout.addWidget(self._add_text)

            self._add_cat = _build_project_combo(self._projects)
            self._add_cat.setFixedWidth(160)
            add_layout.addWidget(self._add_cat)

            add_btn = QPushButton(t("Add"))
            add_btn.setFixedWidth(60)
            add_btn.clicked.connect(self._on_add_fact)
            add_layout.addWidget(add_btn)

            root.addWidget(add_frame)

        # Refresh button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._refresh_btn = QPushButton(t("Refresh"))
        self._refresh_btn.clicked.connect(lambda: self.refresh_facts())
        btn_row.addWidget(self._refresh_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_facts(self, facts: list[dict] | None = None) -> None:
        if facts is None:
            self.refresh_facts()
            return

        self._render_facts(facts)

    def refresh_facts(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._load_token += 1
        token = self._load_token
        if self._refresh_btn is not None:
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setText(t("Refreshing..."))

        def worker() -> None:
            try:
                facts = self._manager.get_all_facts()
                self._signals.loaded.emit(token, facts, "")
            except Exception as exc:
                self._signals.loaded.emit(token, [], str(exc))

        threading.Thread(
            target=worker,
            name="wisp-memory-refresh",
            daemon=True,
        ).start()

    def _on_facts_loaded(self, token: int, facts, error: str) -> None:
        if token != self._load_token:
            return
        self._loading = False
        if self._refresh_btn is not None:
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText(t("Refresh"))
        if error:
            _memory_log.warning("Memory refresh failed: %s", error)
            QMessageBox.warning(self, t("Memory refresh failed"), error)
            return
        self._render_facts(list(facts or []))

    def _render_facts(self, facts: list[dict]) -> None:
        from core.conversation_store.store import GENERAL_PROJECT_ID

        # Group by project id ("" == General). Facts referencing a project that
        # no longer exists fall back to the General bucket.
        names = {
            str(p.get("id") or ""): p.get("name", "")
            for p in self._projects
            if str(p.get("id") or "") not in ("", GENERAL_PROJECT_ID)
        }
        grouped: dict[str, list[dict]] = {}
        for fact in facts:
            pid = str(fact.get("project") or "")
            if pid and pid not in names:
                pid = ""  # orphaned project id -> General
            grouped.setdefault(pid, []).append(fact)

        # General first, then named projects in the project-list order.
        order = [""] + list(names.keys())

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        has_any = False
        for pid in order:
            group_facts = grouped.get(pid)
            if not group_facts:
                continue
            has_any = True

            title = t("General") if pid == "" else names.get(pid, pid)
            group = QGroupBox(title)
            group_layout = QVBoxLayout(group)
            group_layout.setSpacing(2)

            for fact in group_facts:
                row = _FactRow(
                    fact, self._manager, group,
                    read_only=self._read_only, projects=self._projects,
                )
                group_layout.addWidget(row)

            layout.addWidget(group)

        if not has_any:
            empty = QLabel(
                t("No facts stored yet.\n"
                  'Say "remember that ...", "note that ...", or "keep in mind ..." to store a fact.')
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #999; font-style: italic;")
            layout.addWidget(empty)

        layout.addStretch()
        self._scroll_area.setWidget(container)

    # ------------------------------------------------------------------
    # Add fact
    # ------------------------------------------------------------------

    def _on_add_fact(self) -> None:
        text = self._add_text.text().strip()
        if not text:
            return
        project = self._add_cat.currentData() or ""
        self._add_text.clear()
        self._run_add_fact(text, project)

    def _run_add_fact(self, text: str, project: str) -> None:
        def worker() -> None:
            error = ""
            try:
                self._manager.add_fact_manual(text, project=project)
            except Exception as exc:
                error = str(exc)
                _memory_log.warning("Memory add failed: %s", exc)
            self._signals.mutation_done.emit(error)

        threading.Thread(
            target=worker,
            name="wisp-memory-add",
            daemon=True,
        ).start()

    def _on_mutation_done(self, error: str) -> None:
        if error:
            QMessageBox.warning(self, t("Memory update failed"), error)
            return
        self.refresh_facts()


class MemoryViewer(QDialog):
    """
    Standalone dialog wrapping MemoryPanel.
    Opened from tray icon â†’ Memory-¦ or from Settings â†’ Memory tab.
    """

    def __init__(self, manager: "MemoryManager", parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Long-term Memory"))
        self.setMinimumSize(760, 560)
        enable_standard_window_controls(self)
        fit_window_to_screen(self, preferred_width=820, preferred_height=640)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Header
        header = QLabel(t("Long-term Memory"))
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        header.setFont(font)
        root.addWidget(header)

        desc = QLabel(
            t("Facts the assistant remembers about you across sessions. "
              "Click a fact to edit it; changes save immediately.")
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666;")
        root.addWidget(desc)

        root.addWidget(MemoryPanel(manager, self), stretch=1)

        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        fit_window_to_screen(self, preferred_width=820, preferred_height=640)

