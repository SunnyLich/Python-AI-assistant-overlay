"""
ui/memory_viewer.py â€” Long-term memory viewer / editor.

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

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen
from PyQt6.QtWidgets import (
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
    from core.memory import MemoryManager

_CATEGORIES = ("project_context", "general")
_CAT_LABELS = {
    "project_context": "Project",
    "general":         "General",
}


class _FactRow(QWidget):
    """A single editable fact row: [text input] [category] [delete]."""

    def __init__(self, fact: dict, manager: "MemoryManager", parent: QWidget):
        super().__init__(parent)
        self._fact_id = fact["id"]
        self._manager = manager

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self._text_edit = QLineEdit(fact["text"])
        self._text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._text_edit.editingFinished.connect(self._on_text_changed)
        layout.addWidget(self._text_edit)

        self._cat_combo = QComboBox()
        for cat in _CATEGORIES:
            self._cat_combo.addItem(_CAT_LABELS[cat], userData=cat)
        idx = _CATEGORIES.index(fact.get("category", "general"))
        self._cat_combo.setCurrentIndex(idx)
        self._cat_combo.setFixedWidth(160)
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        layout.addWidget(self._cat_combo)

        source_label = QLabel(fact.get("source", "")[:3].upper())
        source_label.setFixedWidth(36)
        source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source_label.setStyleSheet("color: #888; font-size: 10px;")
        source_label.setToolTip(f"Source: {fact.get('source', 'unknown')}")
        layout.addWidget(source_label)

        del_btn = QPushButton("âœ•")
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("Delete this fact")
        del_btn.clicked.connect(self._on_delete)
        layout.addWidget(del_btn)

    def _on_text_changed(self) -> None:
        new_text = self._text_edit.text().strip()
        if not new_text:
            return
        cat = self._cat_combo.currentData()
        self._manager.update_fact(self._fact_id, new_text, cat)

    def _on_category_changed(self) -> None:
        new_text = self._text_edit.text().strip()
        if not new_text:
            return
        cat = self._cat_combo.currentData()
        self._manager.update_fact(self._fact_id, new_text, cat)

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete fact",
            f"Delete: \"{self._text_edit.text()}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._manager.delete_fact(self._fact_id)
            parent = self.parent()
            if parent is not None:
                layout = parent.layout()
                if layout is not None:
                    layout.removeWidget(self)
            self.deleteLater()


class MemoryPanel(QWidget):
    """
    Embeddable widget: scrollable fact browser + add-fact row + refresh button.

    Used as the content of MemoryViewer (dialog) and embedded directly in the
    Settings â†’ Memory tab.
    """

    def __init__(self, manager: "MemoryManager", parent=None):
        super().__init__(parent)
        self._manager = manager
        self._build_ui()
        self._load_facts()

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

        # --- Add fact row ---
        add_frame = QFrame()
        add_frame.setFrameShape(QFrame.Shape.StyledPanel)
        add_layout = QHBoxLayout(add_frame)
        add_layout.setContentsMargins(6, 6, 6, 6)

        add_lbl = QLabel("Add fact:")
        add_layout.addWidget(add_lbl)

        self._add_text = QLineEdit()
        self._add_text.setPlaceholderText("Enter a new factâ€¦")
        self._add_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._add_text.returnPressed.connect(self._on_add_fact)
        add_layout.addWidget(self._add_text)

        self._add_cat = QComboBox()
        for cat in _CATEGORIES:
            self._add_cat.addItem(_CAT_LABELS[cat], userData=cat)
        self._add_cat.setFixedWidth(160)
        add_layout.addWidget(self._add_cat)

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._on_add_fact)
        add_layout.addWidget(add_btn)

        root.addWidget(add_frame)

        # Refresh button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_facts)
        btn_row.addWidget(refresh_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_facts(self) -> None:
        facts = self._manager.get_all_facts()

        grouped: dict[str, list[dict]] = {cat: [] for cat in _CATEGORIES}
        for fact in facts:
            cat = fact.get("category", "general")
            if cat not in grouped:
                cat = "general"
            grouped[cat].append(fact)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        has_any = False
        for cat in _CATEGORIES:
            cat_facts = grouped[cat]
            if not cat_facts:
                continue
            has_any = True

            group = QGroupBox(_CAT_LABELS[cat])
            group_layout = QVBoxLayout(group)
            group_layout.setSpacing(2)

            for fact in cat_facts:
                row = _FactRow(fact, self._manager, group)
                group_layout.addWidget(row)

            layout.addWidget(group)

        if not has_any:
            empty = QLabel(
                "No facts stored yet.\n"
                'Say "remember that ...", "note that ...", or "keep in mind ..." to store a fact.'
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
        category = self._add_cat.currentData()
        self._manager.add_fact_manual(text, category)
        self._add_text.clear()
        self._load_facts()


class MemoryViewer(QDialog):
    """
    Standalone dialog wrapping MemoryPanel.
    Opened from tray icon â†’ Memoryâ€¦ or from Settings â†’ Memory tab.
    """

    def __init__(self, manager: "MemoryManager", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Long-term Memory")
        self.setMinimumSize(620, 480)
        enable_standard_window_controls(self)
        fit_window_to_screen(self, preferred_width=620, preferred_height=520)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Header
        header = QLabel("Long-term Memory")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        header.setFont(font)
        root.addWidget(header)

        desc = QLabel(
            "Facts the assistant remembers about you across sessions. "
            "Click a fact to edit it; changes save immediately."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666;")
        root.addWidget(desc)

        root.addWidget(MemoryPanel(manager, self), stretch=1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        fit_window_to_screen(self, preferred_width=620, preferred_height=520)

