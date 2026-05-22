"""
ui/agent_task_mockup.py - Mockup for starting a scoped agent task.

This module is intentionally self-contained.  It defines the tray-menu action
and a dialog for collecting everything an autonomous agent runner would need,
without wiring it into the current app runtime yet.

Future tray integration in ui/overlay.py would look like:

    from ui.agent_task_mockup import make_agent_task_action
    menu.addAction(make_agent_task_action(self, parent=self))

The important design point is that ``scope_folder`` is validated and resolved
before a task spec is emitted.  A real runner should use this resolved path as
its filesystem sandbox root, not merely include it in the prompt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
import json
import math

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QGraphicsEllipseItem,
    QGraphicsItemGroup,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QMessageBox,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from ui.window_utils import fit_window_to_screen


TaskSubmitCallback = Callable[["AgentTaskSpec"], None]
_agent_run_windows: list["AgentRunWindow"] = []
_agent_task_dialogs: list["AgentTaskDialog"] = []
_agent_history_windows: list["AgentRunHistoryWindow"] = []
_diff_windows: list["DiffViewer"] = []


@dataclass(frozen=True)
class AgentRoleSpec:
    """One planned agent participating in the task."""

    name: str
    role: str
    model: str
    responsibility: str


@dataclass(frozen=True)
class AgentCommunicationSpec:
    """Planned exchange between two agents during a multi-agent task."""

    from_agent: str
    to_agent: str
    phase: str
    trigger: str
    message: str


@dataclass(frozen=True)
class AgentTaskSpec:
    """Serializable contract between the tray GUI and the scoped runner."""

    title: str
    objective: str
    scope_folder: str
    sandbox_mode: str
    approval_policy: str
    model: str
    reasoning_effort: str
    max_runtime_minutes: int
    max_turns: int
    allow_shell: bool
    allow_network: bool
    allow_git: bool
    allow_file_create: bool
    allow_file_edit: bool
    allow_file_delete: bool
    allowed_file_globs: list[str] = field(default_factory=list)
    blocked_file_globs: list[str] = field(default_factory=list)
    required_context: str = ""
    completion_criteria: str = ""
    report_format: str = "Summary + changed files + verification"
    agents: list[AgentRoleSpec] = field(default_factory=list)
    communications: list[AgentCommunicationSpec] = field(default_factory=list)


def resolve_scope_folder(raw_folder: str) -> Path:
    """
    Resolve and validate the folder that an agent may manipulate.

    This is the hard boundary for the runner. Any file operation should be
    checked by the scoped workspace before execution.
    """
    folder = Path(raw_folder).expanduser().resolve()
    if not folder.exists():
        raise ValueError("Scope folder does not exist.")
    if not folder.is_dir():
        raise ValueError("Scope must be a folder, not a file.")
    return folder


def is_inside_scope(path: str | Path, scope_folder: str | Path) -> bool:
    """Return True only when ``path`` resolves inside ``scope_folder``."""
    scope = Path(scope_folder).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    return candidate == scope or scope in candidate.parents


def make_agent_task_action(
    owner: QWidget,
    parent: QWidget | None = None,
    on_submit: TaskSubmitCallback | None = None,
) -> QAction:
    """
    Create the tray QAction for "Start agent task...".

    ``owner`` should normally be the overlay object so the QAction lifetime is
    tied to the app.  ``on_submit`` is where a future runner would be invoked.
    """
    action = QAction("Start agent task...", owner)
    action.triggered.connect(lambda: open_agent_task_dialog(None, on_submit))
    return action


def make_agent_history_action(owner: QWidget, parent: QWidget | None = None) -> QAction:
    """Create the tray QAction for browsing previous agent runs."""
    action = QAction("Agent task history...", owner)
    action.triggered.connect(lambda: open_agent_history(None))
    return action


def open_agent_history(parent: QWidget | None = None) -> None:
    window = AgentRunHistoryWindow(parent=parent)
    _agent_history_windows.append(window)
    window.destroyed.connect(
        lambda _obj=None, w=window: _agent_history_windows.remove(w)
        if w in _agent_history_windows else None
    )
    window.show()


def open_agent_task_dialog(
    parent: QWidget | None = None,
    on_submit: TaskSubmitCallback | None = None,
) -> AgentTaskSpec | None:
    """
    Show the mock dialog and return the accepted task spec.

    If ``on_submit`` is supplied, it is called after validation.  Without a real
    agent runner, the dialog shows a confirmation containing the resolved spec.
    """
    dialog = AgentTaskDialog(parent=parent, on_submit=on_submit)
    _agent_task_dialogs.append(dialog)
    dialog.destroyed.connect(
        lambda _obj=None, w=dialog: _agent_task_dialogs.remove(w)
        if w in _agent_task_dialogs else None
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return None


class AgentTaskDialog(QDialog):
    """Mock GUI for collecting a complete, sandboxed agent task request."""

    def __init__(
        self,
        parent: QWidget | None = None,
        on_submit: TaskSubmitCallback | None = None,
    ):
        super().__init__(parent)
        self._on_submit = on_submit
        self.task_spec: AgentTaskSpec | None = None
        self._agent_specs: list[dict[str, str]] = []
        self._communication_specs: list[dict[str, str]] = []
        self._current_agent_row = -1
        self._loading_agent = False
        self._communication_window: AgentCommunicationMapWindow | None = None

        self.setWindowTitle("Start Agent Task")
        self.setMinimumSize(560, 420)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self._build_ui()
        self._load_defaults()
        self._fit_to_screen()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        intro = QLabel(
            "Create a scoped autonomous task. The selected folder is the future "
            "filesystem boundary for reads and writes."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #777;")
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(self._task_group())
        content_layout.addWidget(self._agents_group())
        content_layout.addWidget(self._scope_group())
        content_layout.addWidget(self._permissions_group())
        content_layout.addWidget(self._runtime_group())
        content_layout.addWidget(self._output_group())
        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)
        root.addWidget(self._buttons())

    def _task_group(self) -> QGroupBox:
        box = QGroupBox("Task")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Example: Add tray launch action mockup")

        self.objective_edit = QTextEdit()
        self.objective_edit.setMinimumHeight(120)
        self.objective_edit.setPlaceholderText(
            "Describe the task, expected behavior, constraints, and what the "
            "agent should produce."
        )

        self.required_context_edit = QTextEdit()
        self.required_context_edit.setMinimumHeight(76)
        self.required_context_edit.setPlaceholderText(
            "Relevant files, APIs, user preferences, credentials policy, or "
            "anything the agent should know before it starts."
        )

        form.addRow("Title", self.title_edit)
        form.addRow("Objective", self.objective_edit)
        form.addRow("Context", self.required_context_edit)
        return box

    def _agents_group(self) -> QGroupBox:
        box = QGroupBox("Agents & Communication")
        root = QVBoxLayout(box)
        root.setSpacing(10)

        self.agent_list = QListWidget()
        self.agent_list.hide()
        self.communication_list = QListWidget()
        self.communication_list.hide()

        self.agent_name_edit = QLineEdit()
        self.agent_name_edit.hide()
        self.agent_name_edit.textChanged.connect(self._save_current_agent)
        self.agent_role_combo = QComboBox()
        self.agent_role_combo.hide()
        self.agent_role_combo.setEditable(True)
        self.agent_role_combo.addItems([
            "Coordinator",
            "Planner",
            "Implementer",
            "Reviewer",
            "Tester",
            "Researcher",
        ])
        self.agent_role_combo.currentTextChanged.connect(self._save_current_agent)
        self.agent_model_combo = QComboBox()
        self.agent_model_combo.hide()
        self.agent_model_combo.setEditable(True)
        self.agent_model_combo.addItems([
            "same as task",
            "gpt-5.3-codex",
            "gpt-5.4",
            "claude-sonnet-4-5",
        ])
        self.agent_model_combo.currentTextChanged.connect(self._save_current_agent)
        self.agent_responsibility_edit = QTextEdit()
        self.agent_responsibility_edit.hide()
        self.agent_responsibility_edit.textChanged.connect(self._save_current_agent)

        row = QHBoxLayout()
        open_btn = QPushButton("Open Agents Communication Window")
        open_btn.clicked.connect(self._open_communication_window)
        row.addWidget(open_btn)
        row.addStretch()
        root.addLayout(row)

        note = QLabel(
            "Define agents and their exchange rules in the separate communication window."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #777; font-size: 9pt;")
        root.addWidget(note)
        return box

    def _scope_group(self) -> QGroupBox:
        box = QGroupBox("Filesystem Scope")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)

        row = QHBoxLayout()
        self.scope_edit = QLineEdit()
        self.scope_edit.setPlaceholderText("Folder the agent is allowed to manipulate")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_scope)
        row.addWidget(self.scope_edit, stretch=1)
        row.addWidget(browse)
        layout.addLayout(row)

        form = QFormLayout()
        self.sandbox_combo = QComboBox()
        self.sandbox_combo.addItems(
            [
                "workspace-write: scope folder only",
                "read-only: inspect only",
                "approval-required: ask before every write",
            ]
        )

        self.allowed_globs_edit = QLineEdit()
        self.allowed_globs_edit.setPlaceholderText("Optional, comma-separated: *.py, ui/*.py")
        self.blocked_globs_edit = QLineEdit()
        self.blocked_globs_edit.setPlaceholderText("Optional, comma-separated: .env, private/*")

        form.addRow("Sandbox", self.sandbox_combo)
        form.addRow("Allow globs", self.allowed_globs_edit)
        form.addRow("Block globs", self.blocked_globs_edit)
        layout.addLayout(form)

        note = QLabel(
            "Runner contract: resolve the scope folder first, then reject any "
            "file operation whose resolved path is outside that folder."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #777; font-size: 9pt;")
        layout.addWidget(note)
        return box

    def _permissions_group(self) -> QGroupBox:
        box = QGroupBox("Capabilities")
        layout = QVBoxLayout(box)

        row_one = QHBoxLayout()
        self.allow_shell = QCheckBox("Shell")
        self.allow_network = QCheckBox("Network")
        self.allow_git = QCheckBox("Git")
        row_one.addWidget(self.allow_shell)
        row_one.addWidget(self.allow_network)
        row_one.addWidget(self.allow_git)
        row_one.addStretch()

        row_two = QHBoxLayout()
        self.allow_create = QCheckBox("Create files")
        self.allow_edit = QCheckBox("Edit files")
        self.allow_delete = QCheckBox("Delete files")
        row_two.addWidget(self.allow_create)
        row_two.addWidget(self.allow_edit)
        row_two.addWidget(self.allow_delete)
        row_two.addStretch()

        layout.addLayout(row_one)
        layout.addLayout(row_two)
        return box

    def _runtime_group(self) -> QGroupBox:
        box = QGroupBox("Runtime")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(
            [
                "gpt-5.3-codex",
                "gpt-5.4",
                "gpt-5.4-mini",
                "claude-sonnet-4-5",
                "claude-opus-4-5",
            ]
        )
        self.model_combo.lineEdit().setPlaceholderText("Type any model name...")

        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItems(["medium", "high", "low", "xhigh"])

        self.approval_combo = QComboBox()
        self.approval_combo.addItems(
            [
                "ask before escalation",
                "never escalate",
                "auto-approve safe reads",
            ]
        )

        self.runtime_minutes = QSpinBox()
        self.runtime_minutes.setRange(1, 480)
        self.runtime_minutes.setSuffix(" min")

        self.max_turns = QSpinBox()
        self.max_turns.setRange(1, 200)

        form.addRow("Model", self.model_combo)
        form.addRow("Reasoning", self.reasoning_combo)
        form.addRow("Approvals", self.approval_combo)
        form.addRow("Time limit", self.runtime_minutes)
        form.addRow("Turn limit", self.max_turns)
        return box

    def _output_group(self) -> QGroupBox:
        box = QGroupBox("Completion")
        form = QFormLayout(box)

        self.completion_edit = QTextEdit()
        self.completion_edit.setMinimumHeight(76)
        self.completion_edit.setPlaceholderText(
            "How the agent knows it is done: tests pass, files changed, PR opened, "
            "summary produced, etc."
        )

        self.report_combo = QComboBox()
        self.report_combo.addItems(
            [
                "Summary + changed files + verification",
                "Patch only",
                "Detailed implementation report",
                "Ask before final changes",
            ]
        )

        form.addRow("Done when", self.completion_edit)
        form.addRow("Report", self.report_combo)
        return box

    def _buttons(self) -> QWidget:
        frame = QFrame()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)

        self.preview_btn = QPushButton("Preview Spec")
        self.preview_btn.clicked.connect(self._preview_spec)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        start = QPushButton("Start Task")
        start.setDefault(True)
        start.clicked.connect(self._accept)

        row.addWidget(self.preview_btn)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(start)
        return frame

    def _load_defaults(self) -> None:
        self.scope_edit.setText(str(Path.cwd()))
        self.allow_shell.setChecked(True)
        self.allow_network.setChecked(False)
        self.allow_git.setChecked(True)
        self.allow_create.setChecked(True)
        self.allow_edit.setChecked(True)
        self.allow_delete.setChecked(False)
        self.runtime_minutes.setValue(60)
        self.max_turns.setValue(30)
        self.blocked_globs_edit.setText(".env, private/*, .git/*")
        self._agent_specs = [
            {
                "name": "Coordinator",
                "role": "Coordinator",
                "model": "same as task",
                "responsibility": "Break down the task, assign work, merge decisions, and decide when the group is done.",
            },
            {
                "name": "Builder",
                "role": "Implementer",
                "model": "same as task",
                "responsibility": "Make the code changes inside the selected scope and report risks or blockers.",
            },
            {
                "name": "Reviewer",
                "role": "Reviewer",
                "model": "same as task",
                "responsibility": "Inspect proposed changes, call out defects, and request tests or fixes before completion.",
            },
        ]
        self._communication_specs = [
            {
                "from_agent": "Coordinator",
                "to_agent": "Builder",
                "phase": "Planning",
                "trigger": "After reading the objective and scope",
                "message": "Send the implementation plan, constraints, and first files to inspect.",
            },
            {
                "from_agent": "Builder",
                "to_agent": "Reviewer",
                "phase": "Review",
                "trigger": "After changes and local verification",
                "message": "Send changed files, verification results, and known tradeoffs for review.",
            },
            {
                "from_agent": "Reviewer",
                "to_agent": "Coordinator",
                "phase": "Completion",
                "trigger": "After review is complete",
                "message": "Send approval status, remaining concerns, and final-report notes.",
            },
        ]
        self._refresh_agent_list()
        self._refresh_communication_list()
        if self.agent_list.count():
            self.agent_list.setCurrentRow(0)

    def _fit_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        available_h = screen.availableGeometry().height() if screen is not None else 680
        fit_window_to_screen(
            self,
            preferred_width=680,
            preferred_height=min(640, max(460, available_h - 80)),
        )

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._fit_to_screen()

    # ------------------------------------------------------------------ Actions

    def _choose_scope(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Agent Scope Folder",
            self.scope_edit.text() or str(Path.cwd()),
        )
        if folder:
            self.scope_edit.setText(folder)

    def _add_agent(self) -> None:
        self._save_current_agent()
        number = len(self._agent_specs) + 1
        self._agent_specs.append({
            "name": f"Agent {number}",
            "role": "Implementer",
            "model": "same as task",
            "responsibility": "",
        })
        self._refresh_agent_list()
        self.agent_list.setCurrentRow(len(self._agent_specs) - 1)

    def _remove_agent(self) -> None:
        row = self.agent_list.currentRow()
        if row < 0 or row >= len(self._agent_specs):
            return
        removed = self._agent_specs[row]["name"]
        del self._agent_specs[row]
        self._communication_specs = [
            spec for spec in self._communication_specs
            if spec.get("from_agent") != removed and spec.get("to_agent") != removed
        ]
        self._current_agent_row = -1
        self._refresh_agent_list()
        self._refresh_communication_list()
        if self.agent_list.count():
            self.agent_list.setCurrentRow(min(row, self.agent_list.count() - 1))

    def _load_selected_agent(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._loading_agent:
            return
        self._save_current_agent()
        row = self.agent_list.row(current) if current else -1
        self._current_agent_row = row
        self._loading_agent = True
        try:
            if row < 0 or row >= len(self._agent_specs):
                self.agent_name_edit.clear()
                self.agent_role_combo.setCurrentText("")
                self.agent_model_combo.setCurrentText("same as task")
                self.agent_responsibility_edit.clear()
                return
            agent = self._agent_specs[row]
            self.agent_name_edit.setText(agent.get("name", ""))
            self.agent_role_combo.setCurrentText(agent.get("role", "Implementer"))
            self.agent_model_combo.setCurrentText(agent.get("model", "same as task"))
            self.agent_responsibility_edit.setPlainText(agent.get("responsibility", ""))
        finally:
            self._loading_agent = False

    def _save_current_agent(self) -> None:
        if self._loading_agent:
            return
        row = self._current_agent_row
        if row < 0 or row >= len(self._agent_specs):
            return
        old_name = self._agent_specs[row].get("name", "")
        new_name = self.agent_name_edit.text().strip() or f"Agent {row + 1}"
        self._agent_specs[row] = {
            "name": new_name,
            "role": self.agent_role_combo.currentText().strip() or "Implementer",
            "model": self.agent_model_combo.currentText().strip() or "same as task",
            "responsibility": self.agent_responsibility_edit.toPlainText().strip(),
        }
        if old_name and old_name != new_name:
            for comm in self._communication_specs:
                if comm.get("from_agent") == old_name:
                    comm["from_agent"] = new_name
                if comm.get("to_agent") == old_name:
                    comm["to_agent"] = new_name
            self._refresh_communication_list()
        item = self.agent_list.item(row)
        if item:
            item.setText(self._agent_label(self._agent_specs[row]))

    def _add_communication(self) -> None:
        self._save_current_agent()
        agents = self._agent_names()
        if len(agents) < 2:
            QMessageBox.information(self, "Communication", "Add at least two agents first.")
            return
        dialog = AgentCommunicationDialog(agents, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.communication:
            self._communication_specs.append(dialog.communication)
            self._refresh_communication_list()

    def _edit_communication(self) -> None:
        self._save_current_agent()
        row = self.communication_list.currentRow()
        if row < 0 or row >= len(self._communication_specs):
            return
        dialog = AgentCommunicationDialog(
            self._agent_names(),
            self._communication_specs[row],
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.communication:
            self._communication_specs[row] = dialog.communication
            self._refresh_communication_list()
            self.communication_list.setCurrentRow(row)

    def _remove_communication(self) -> None:
        row = self.communication_list.currentRow()
        if row < 0 or row >= len(self._communication_specs):
            return
        del self._communication_specs[row]
        self._refresh_communication_list()

    def _create_pair_communications(self) -> None:
        self._save_current_agent()
        agents = self._agent_names()
        if len(agents) < 2:
            QMessageBox.information(self, "Communication", "Add at least two agents first.")
            return
        existing = {
            (spec.get("from_agent"), spec.get("to_agent"), spec.get("phase"))
            for spec in self._communication_specs
        }
        for source in agents:
            for target in agents:
                if source == target or (source, target, "Status update") in existing:
                    continue
                self._communication_specs.append({
                    "from_agent": source,
                    "to_agent": target,
                    "phase": "Status update",
                    "trigger": "When this agent has findings, changes, or blockers that affect the other agent",
                    "message": "Share current findings, decisions needed, changed files, and next requested action.",
                })
        self._refresh_communication_list()
        self._refresh_communication_window()

    def _open_communication_window(self) -> None:
        self._save_current_agent()
        if self._communication_window is None:
            self._communication_window = AgentCommunicationMapWindow(self, parent=None)
            self._communication_window.destroyed.connect(lambda _obj=None: setattr(self, "_communication_window", None))
        self._communication_window.refresh()
        self._communication_window.show()
        self._communication_window.raise_()
        self._communication_window.activateWindow()

    def _refresh_communication_window(self) -> None:
        if self._communication_window is not None:
            self._communication_window.refresh()

    def _refresh_agent_list(self) -> None:
        self._loading_agent = True
        try:
            self.agent_list.clear()
            for agent in self._agent_specs:
                self.agent_list.addItem(QListWidgetItem(self._agent_label(agent)))
        finally:
            self._loading_agent = False
        self._refresh_communication_window()

    def _refresh_communication_list(self) -> None:
        self.communication_list.clear()
        for spec in self._communication_specs:
            self.communication_list.addItem(QListWidgetItem(self._communication_label(spec)))
        self._refresh_communication_window()

    @staticmethod
    def _agent_label(agent: dict[str, str]) -> str:
        role = agent.get("role") or "Agent"
        name = agent.get("name") or role
        return f"{name}  -  {role}"

    @staticmethod
    def _communication_label(spec: dict[str, str]) -> str:
        return (
            f"{spec.get('from_agent', '?')} -> {spec.get('to_agent', '?')}  "
            f"[{spec.get('phase', 'Any time')}]"
        )

    def _agent_names(self) -> list[str]:
        return [
            (agent.get("name") or f"Agent {idx + 1}").strip()
            for idx, agent in enumerate(self._agent_specs)
        ]

    def _preview_spec(self) -> None:
        try:
            spec = self._collect_spec()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Agent Task", str(exc))
            return
        QMessageBox.information(self, "Agent Task Spec", self._format_spec(spec))

    def _accept(self) -> None:
        try:
            spec = self._collect_spec()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Agent Task", str(exc))
            return

        self.task_spec = spec
        if self._on_submit is not None:
            self._on_submit(spec)
        else:
            window = AgentRunWindow(spec, parent=None)
            _agent_run_windows.append(window)
            window.destroyed.connect(lambda _obj=None, w=window: _agent_run_windows.remove(w) if w in _agent_run_windows else None)
            window.show()
        self.accept()

    # ------------------------------------------------------------------ Spec

    def _collect_spec(self) -> AgentTaskSpec:
        self._save_current_agent()
        title = self.title_edit.text().strip()
        objective = self.objective_edit.toPlainText().strip()
        if not title:
            raise ValueError("Add a task title.")
        if not objective:
            raise ValueError("Describe the task objective.")
        model = self.model_combo.currentText().strip()
        if not model:
            raise ValueError("Add a model name.")
        agents = [
            AgentRoleSpec(
                name=(agent.get("name") or f"Agent {idx + 1}").strip(),
                role=(agent.get("role") or "Implementer").strip(),
                model=(agent.get("model") or "same as task").strip(),
                responsibility=agent.get("responsibility", "").strip(),
            )
            for idx, agent in enumerate(self._agent_specs)
        ]
        if not agents:
            raise ValueError("Add at least one agent.")
        if len({agent.name.lower() for agent in agents}) != len(agents):
            raise ValueError("Agent names must be unique.")
        agent_names = {agent.name for agent in agents}
        communications = [
            AgentCommunicationSpec(
                from_agent=spec.get("from_agent", "").strip(),
                to_agent=spec.get("to_agent", "").strip(),
                phase=spec.get("phase", "").strip(),
                trigger=spec.get("trigger", "").strip(),
                message=spec.get("message", "").strip(),
            )
            for spec in self._communication_specs
            if spec.get("from_agent") and spec.get("to_agent")
        ]
        for comm in communications:
            if comm.from_agent not in agent_names or comm.to_agent not in agent_names:
                raise ValueError("Every communication must reference existing agents.")

        scope = resolve_scope_folder(self.scope_edit.text().strip())
        return AgentTaskSpec(
            title=title,
            objective=objective,
            scope_folder=str(scope),
            sandbox_mode=self.sandbox_combo.currentText(),
            approval_policy=self.approval_combo.currentText(),
            model=model,
            reasoning_effort=self.reasoning_combo.currentText(),
            max_runtime_minutes=self.runtime_minutes.value(),
            max_turns=self.max_turns.value(),
            allow_shell=self.allow_shell.isChecked(),
            allow_network=self.allow_network.isChecked(),
            allow_git=self.allow_git.isChecked(),
            allow_file_create=self.allow_create.isChecked(),
            allow_file_edit=self.allow_edit.isChecked(),
            allow_file_delete=self.allow_delete.isChecked(),
            allowed_file_globs=self._split_globs(self.allowed_globs_edit.text()),
            blocked_file_globs=self._split_globs(self.blocked_globs_edit.text()),
            required_context=self.required_context_edit.toPlainText().strip(),
            completion_criteria=self.completion_edit.toPlainText().strip(),
            report_format=self.report_combo.currentText(),
            agents=agents,
            communications=communications,
        )

    @staticmethod
    def _split_globs(raw: str) -> list[str]:
        return [part.strip() for part in raw.split(",") if part.strip()]

    @staticmethod
    def _format_spec(spec: AgentTaskSpec) -> str:
        lines: list[str] = []
        for key, value in asdict(spec).items():
            if isinstance(value, list):
                value = json.dumps(value, indent=2) if value else "(none)"
            lines.append(f"{key}: {value}")
        return "\n".join(lines)


class _RelationshipItem(QGraphicsRectItem):
    def __init__(self, index: int, click_callback: Callable[[int], None], *args):
        super().__init__(*args)
        self._index = index
        self._click_callback = click_callback
        self.setAcceptHoverEvents(True)
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(5)

    def hoverEnterEvent(self, event):  # noqa: N802
        self.setBrush(QBrush(QColor(120, 167, 223, 24)))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        self._click_callback(self._index)
        event.accept()


class _RelationshipAgentItem(QGraphicsItemGroup):
    def __init__(
        self,
        index: int,
        click_callback: Callable[[int], None],
        move_callback: Callable[[int, float, float], None],
        release_callback: Callable[[], None],
        x: float,
        y: float,
        name: str,
        role: str,
    ):
        super().__init__()
        self._index = index
        self._click_callback = click_callback
        self._move_callback = move_callback
        self._release_callback = release_callback
        self._alive = True
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(3)
        shadow = QGraphicsEllipseItem(4, 5, 144, 72)
        shadow.setBrush(QBrush(QColor(86, 105, 135, 42)))
        shadow.setPen(QPen(Qt.PenStyle.NoPen))
        self.addToGroup(shadow)
        self._node = QGraphicsRectItem(0, 0, 144, 72)
        self._node.setBrush(QBrush(QColor("#ffffff")))
        self._node.setPen(QPen(QColor("#7aa7df"), 1.5))
        self.addToGroup(self._node)
        name_item = QGraphicsTextItem(name)
        name_item.setDefaultTextColor(QColor("#111111"))
        name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        name_item.setTextWidth(126)
        name_item.setPos(12, 12)
        name_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(name_item)
        role_item = QGraphicsTextItem(role)
        role_item.setDefaultTextColor(QColor("#5c6f87"))
        role_item.setFont(QFont("Segoe UI", 8))
        role_item.setTextWidth(126)
        role_item.setPos(12, 36)
        role_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(role_item)
        self.setPos(x, y)

    def hoverEnterEvent(self, event):  # noqa: N802
        self._node.setBrush(QBrush(QColor("#edf6ff")))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._node.setBrush(QBrush(QColor("#ffffff")))
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        self._click_callback(self._index)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        super().mouseReleaseEvent(event)
        self._move_callback(self._index, self.pos().x(), self.pos().y())
        self._release_callback()

    def itemChange(self, change, value):  # noqa: N802
        if self._alive and change == QGraphicsItemGroup.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            self._move_callback(self._index, pos.x(), pos.y())
        return super().itemChange(change, value)

    def mark_dead(self) -> None:
        self._alive = False


class AgentCommunicationMapWindow(QDialog):
    """Visual mockup for multi-agent communication setup."""

    def __init__(self, task_dialog: AgentTaskDialog, parent: QWidget | None = None):
        super().__init__(parent)
        self._task_dialog = task_dialog
        self._agent_map_positions: dict[str, tuple[float, float]] = {}
        self._relationship_nodes: list[_RelationshipAgentItem] = []
        self._dragging_map = False
        self.setWindowTitle("Agent Communication Map")
        self.setMinimumSize(920, 520)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        add_agent_btn = QPushButton("Add Agent")
        remove_agent_btn = QPushButton("Remove Agent")
        add_comm_btn = QPushButton("Add Communication")
        remove_comm_btn = QPushButton("Remove Communication")
        pair_btn = QPushButton("Create Pair Exchanges")
        refresh_btn = QPushButton("Refresh")
        add_agent_btn.clicked.connect(self._add_agent)
        remove_agent_btn.clicked.connect(self._remove_agent)
        add_comm_btn.clicked.connect(self._add_communication)
        remove_comm_btn.clicked.connect(self._remove_selected_exchange)
        pair_btn.clicked.connect(self._create_pairs)
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(add_agent_btn)
        toolbar.addWidget(remove_agent_btn)
        toolbar.addWidget(add_comm_btn)
        toolbar.addWidget(remove_comm_btn)
        toolbar.addWidget(pair_btn)
        toolbar.addStretch()
        toolbar.addWidget(refresh_btn)
        root.addLayout(toolbar)

        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        agent_panel = QWidget()
        agent_layout = QVBoxLayout(agent_panel)
        agent_layout.setContentsMargins(0, 0, 8, 0)
        agent_layout.setSpacing(8)
        agent_layout.addWidget(QLabel("Agents"))
        self.window_agent_list = QListWidget()
        self.window_agent_list.currentRowChanged.connect(self._load_agent_row)
        agent_layout.addWidget(self.window_agent_list)
        agent_form = QFormLayout()
        agent_form.setSpacing(8)
        self.map_agent_name = QLineEdit()
        self.map_agent_role = QComboBox()
        self.map_agent_role.setEditable(True)
        self.map_agent_role.addItems(["Coordinator", "Planner", "Implementer", "Reviewer", "Tester", "Researcher"])
        self.map_agent_model = QComboBox()
        self.map_agent_model.setEditable(True)
        self.map_agent_model.addItems(["same as task", "gpt-5.3-codex", "gpt-5.4", "claude-sonnet-4-5"])
        self.map_agent_responsibility = QTextEdit()
        self.map_agent_responsibility.setMinimumHeight(160)
        self.map_agent_name.textChanged.connect(self._save_agent_form)
        self.map_agent_role.currentTextChanged.connect(self._save_agent_form)
        self.map_agent_model.currentTextChanged.connect(self._save_agent_form)
        self.map_agent_responsibility.textChanged.connect(self._save_agent_form)
        agent_form.addRow("Name", self.map_agent_name)
        agent_form.addRow("Role", self.map_agent_role)
        agent_form.addRow("Model", self.map_agent_model)
        agent_form.addRow("Responsibility", self.map_agent_responsibility)
        agent_layout.addLayout(agent_form)
        splitter.addWidget(agent_panel)

        comm_panel = QWidget()
        comm_layout = QVBoxLayout(comm_panel)
        comm_layout.setContentsMargins(8, 0, 0, 0)
        comm_layout.setSpacing(8)
        comm_layout.addWidget(QLabel("Communications"))
        self.exchange_list = QListWidget()
        self.exchange_list.currentRowChanged.connect(self._load_exchange_row)
        comm_layout.addWidget(self.exchange_list)
        comm_form = QFormLayout()
        comm_form.setSpacing(8)
        self.map_comm_from = QComboBox()
        self.map_comm_to = QComboBox()
        self.map_comm_phase = QComboBox()
        self.map_comm_phase.setEditable(True)
        self.map_comm_phase.addItems(["Planning", "Implementation", "Review", "Testing", "Status update", "Completion"])
        self.map_comm_trigger = QLineEdit()
        self.map_comm_trigger.setPlaceholderText("When should this exchange happen?")
        self.map_comm_message = QTextEdit()
        self.map_comm_message.setMinimumHeight(160)
        self.map_comm_message.setPlaceholderText("What should be exchanged: findings, files, decisions, blockers, tests, or final notes.")
        self.map_comm_from.currentTextChanged.connect(self._save_exchange_form)
        self.map_comm_to.currentTextChanged.connect(self._save_exchange_form)
        self.map_comm_phase.currentTextChanged.connect(self._save_exchange_form)
        self.map_comm_trigger.textChanged.connect(self._save_exchange_form)
        self.map_comm_message.textChanged.connect(self._save_exchange_form)
        comm_form.addRow("From", self.map_comm_from)
        comm_form.addRow("To", self.map_comm_to)
        comm_form.addRow("Phase", self.map_comm_phase)
        comm_form.addRow("Trigger", self.map_comm_trigger)
        comm_form.addRow("Message", self.map_comm_message)
        comm_layout.addLayout(comm_form)
        splitter.addWidget(comm_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        vertical_splitter.addWidget(splitter)

        relationship_panel = QWidget()
        relationship_layout = QVBoxLayout(relationship_panel)
        relationship_layout.setContentsMargins(0, 10, 0, 0)
        relationship_layout.setSpacing(6)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #b8c2d4;")
        relationship_layout.addWidget(divider)
        relationship_layout.addWidget(QLabel("Relationship / Exchange Map"))
        self.relationship_scene = QGraphicsScene(self)
        self.relationship_view = QGraphicsView(self.relationship_scene)
        self.relationship_view.setMinimumHeight(190)
        self.relationship_view.setStyleSheet("QGraphicsView { background: #eef3f9; border: 1px solid #c2ccda; }")
        relationship_layout.addWidget(self.relationship_view, stretch=1)
        vertical_splitter.addWidget(relationship_panel)
        vertical_splitter.setStretchFactor(0, 3)
        vertical_splitter.setStretchFactor(1, 2)
        root.addWidget(vertical_splitter, stretch=1)

        self._loading = False
        footer = QLabel("Select an agent or communication, or click an exchange in the bottom relationship map to edit it above.")
        footer.setStyleSheet("color: #777;")
        root.addWidget(footer)
        fit_window_to_screen(self, preferred_width=980, preferred_height=620)

    def refresh(self) -> None:
        current_agent = self.window_agent_list.currentRow() if hasattr(self, "window_agent_list") else 0
        current_exchange = self.exchange_list.currentRow() if hasattr(self, "exchange_list") else 0
        self._loading = True
        self.window_agent_list.clear()
        self.exchange_list.clear()
        agents = self._task_dialog._agent_specs
        communications = self._task_dialog._communication_specs
        for agent in agents:
            self.window_agent_list.addItem(QListWidgetItem(self._task_dialog._agent_label(agent)))
        for idx, comm in enumerate(communications):
            item = QListWidgetItem(self._communication_label(comm))
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.exchange_list.addItem(item)
        names = self._task_dialog._agent_names()
        self.map_comm_from.clear()
        self.map_comm_to.clear()
        self.map_comm_from.addItems(names)
        self.map_comm_to.addItems(names)
        self._loading = False
        if self.window_agent_list.count():
            self.window_agent_list.setCurrentRow(max(0, min(current_agent, self.window_agent_list.count() - 1)))
        if self.exchange_list.count():
            self.exchange_list.setCurrentRow(max(0, min(current_exchange, self.exchange_list.count() - 1)))
        self._load_agent_row(self.window_agent_list.currentRow())
        self._load_exchange_row(self.exchange_list.currentRow())
        self._draw_relationship_map()

    def _draw_relationship_map(self) -> None:
        for node in self._relationship_nodes:
            node.mark_dead()
        self._relationship_nodes.clear()
        self.relationship_scene.clear()
        self.relationship_scene.setSceneRect(0, 0, 860, 260)
        bg = QPainterPath()
        bg.addRoundedRect(8, 8, 844, 244, 14, 14)
        self.relationship_scene.addPath(bg, QPen(QColor("#d1dae8"), 1), QBrush(QColor("#eef3f9")))
        for x in range(40, 840, 40):
            self.relationship_scene.addLine(x, 22, x, 246, QPen(QColor(210, 219, 232, 70), 0.8))
        for y in range(40, 240, 40):
            self.relationship_scene.addLine(22, y, 838, y, QPen(QColor(210, 219, 232, 70), 0.8))
        agents = self._task_dialog._agent_specs
        communications = self._task_dialog._communication_specs
        default_positions = self._relationship_positions(len(agents))
        centers: dict[str, tuple[float, float]] = {}

        for idx, agent in enumerate(agents):
            name = agent.get("name") or f"Agent {idx + 1}"
            role = agent.get("role") or "Agent"
            x, y = self._agent_map_positions.get(name, default_positions[idx])
            centers[name] = (x + 72, y + 36)
            node = _RelationshipAgentItem(
                idx,
                self._select_agent_from_map,
                self._move_agent_on_map,
                self._draw_relationship_map,
                x,
                y,
                name,
                role,
            )
            self._relationship_nodes.append(node)
            self.relationship_scene.addItem(node)

        for idx, comm in enumerate(communications):
            source = comm.get("from_agent", "")
            target = comm.get("to_agent", "")
            if source not in centers or target not in centers:
                continue
            sx, sy = centers[source]
            tx, ty = centers[target]
            sx_edge, sy_edge, tx_edge, ty_edge = self._edge_points(sx, sy, tx, ty)
            self._draw_double_arrow(sx_edge, sy_edge, tx_edge, ty_edge)
            mx = (sx + tx) / 2
            my = (sy + ty) / 2
            item = _RelationshipItem(idx, self._select_exchange_from_map, mx - 82, my - 18, 164, 36)
            self.relationship_scene.addItem(item)
            text = QGraphicsTextItem(comm.get("phase") or "Exchange")
            text.setDefaultTextColor(QColor("#203047"))
            text.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            text.setTextWidth(150)
            text.setPos(mx - 74, my - 14)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            text.setZValue(6)
            self.relationship_scene.addItem(text)

    def _edge_points(self, sx: float, sy: float, tx: float, ty: float) -> tuple[float, float, float, float]:
        dx, dy = tx - sx, ty - sy
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        node_rx, node_ry = 72.0, 36.0
        start_offset = 1.0 / math.sqrt((ux / node_rx) ** 2 + (uy / node_ry) ** 2)
        end_offset = start_offset
        return (
            sx + ux * start_offset,
            sy + uy * start_offset,
            tx - ux * end_offset,
            ty - uy * end_offset,
        )

    def _draw_double_arrow(self, sx: float, sy: float, tx: float, ty: float) -> None:
        pen = QPen(QColor("#5d7fa9"), 2.0)
        self.relationship_scene.addLine(sx, sy, tx, ty, pen)
        dx, dy = tx - sx, ty - sy
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        size = 10
        for ax, ay, direction in ((tx, ty, 1), (sx, sy, -1)):
            bx = ax - direction * ux * size
            by = ay - direction * uy * size
            left = (bx + px * size * 0.55, by + py * size * 0.55)
            right = (bx - px * size * 0.55, by - py * size * 0.55)
            path = QPainterPath()
            path.moveTo(ax, ay)
            path.lineTo(left[0], left[1])
            path.lineTo(right[0], right[1])
            path.closeSubpath()
            self.relationship_scene.addPath(path, QPen(QColor("#5d7fa9")), QBrush(QColor("#5d7fa9")))

    def _relationship_positions(self, count: int) -> list[tuple[float, float]]:
        if count <= 0:
            return []
        cx, cy = 430, 130
        rx, ry = 300, 74
        return [
            (
                cx + math.cos(-math.pi / 2 + 2 * math.pi * idx / count) * rx - 72,
                cy + math.sin(-math.pi / 2 + 2 * math.pi * idx / count) * ry - 36,
            )
            for idx in range(count)
        ]

    def _select_exchange_from_map(self, index: int) -> None:
        if 0 <= index < self.exchange_list.count():
            self.exchange_list.setCurrentRow(index)
            self._load_exchange_row(index)

    def _select_agent_from_map(self, index: int) -> None:
        if 0 <= index < self.window_agent_list.count():
            self.window_agent_list.setCurrentRow(index)
            self._load_agent_row(index)

    def _move_agent_on_map(self, index: int, x: float, y: float) -> None:
        if index < 0 or index >= len(self._task_dialog._agent_specs):
            return
        agent = self._task_dialog._agent_specs[index]
        name = agent.get("name") or f"Agent {index + 1}"
        self._agent_map_positions[name] = (x, y)

    def _add_agent(self) -> None:
        self._task_dialog._add_agent()
        self.refresh()

    def _remove_agent(self) -> None:
        row = self.window_agent_list.currentRow()
        if row < 0:
            return
        self._task_dialog.agent_list.setCurrentRow(row)
        self._task_dialog._remove_agent()
        self.refresh()

    def _add_communication(self) -> None:
        agents = self._task_dialog._agent_names()
        if len(agents) < 2:
            QMessageBox.information(self, "Communication", "Add at least two agents first.")
            return
        self._task_dialog._communication_specs.append({
            "from_agent": agents[0],
            "to_agent": agents[1],
            "phase": "Planning",
            "trigger": "",
            "message": "",
        })
        self._task_dialog._refresh_communication_list()
        self.refresh()
        self.exchange_list.setCurrentRow(self.exchange_list.count() - 1)

    def _create_pairs(self) -> None:
        self._task_dialog._create_pair_communications()
        self.refresh()

    def _remove_selected_exchange(self) -> None:
        item = self.exchange_list.currentItem()
        if item is None:
            return
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if 0 <= index < len(self._task_dialog._communication_specs):
            del self._task_dialog._communication_specs[index]
            self._task_dialog._refresh_communication_list()
            self.refresh()

    def _load_agent_row(self, row: int) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            if row < 0 or row >= len(self._task_dialog._agent_specs):
                self.map_agent_name.clear()
                self.map_agent_role.setCurrentText("")
                self.map_agent_model.setCurrentText("same as task")
                self.map_agent_responsibility.clear()
                return
            agent = self._task_dialog._agent_specs[row]
            self.map_agent_name.setText(agent.get("name", ""))
            self.map_agent_role.setCurrentText(agent.get("role", "Implementer"))
            self.map_agent_model.setCurrentText(agent.get("model", "same as task"))
            self.map_agent_responsibility.setPlainText(agent.get("responsibility", ""))
        finally:
            self._loading = False

    def _save_agent_form(self) -> None:
        if self._loading:
            return
        row = self.window_agent_list.currentRow()
        if row < 0 or row >= len(self._task_dialog._agent_specs):
            return
        old_name = self._task_dialog._agent_specs[row].get("name", "")
        new_name = self.map_agent_name.text().strip() or f"Agent {row + 1}"
        self._task_dialog._agent_specs[row] = {
            "name": new_name,
            "role": self.map_agent_role.currentText().strip() or "Implementer",
            "model": self.map_agent_model.currentText().strip() or "same as task",
            "responsibility": self.map_agent_responsibility.toPlainText().strip(),
        }
        if old_name and old_name != new_name:
            for comm in self._task_dialog._communication_specs:
                if comm.get("from_agent") == old_name:
                    comm["from_agent"] = new_name
                if comm.get("to_agent") == old_name:
                    comm["to_agent"] = new_name
        self._task_dialog._refresh_agent_list()
        self._task_dialog._refresh_communication_list()
        self.refresh()

    def _load_exchange_row(self, row: int) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            if row < 0 or row >= len(self._task_dialog._communication_specs):
                self.map_comm_from.setCurrentText("")
                self.map_comm_to.setCurrentText("")
                self.map_comm_phase.setCurrentText("")
                self.map_comm_trigger.clear()
                self.map_comm_message.clear()
                return
            comm = self._task_dialog._communication_specs[row]
            self.map_comm_from.setCurrentText(comm.get("from_agent", ""))
            self.map_comm_to.setCurrentText(comm.get("to_agent", ""))
            self.map_comm_phase.setCurrentText(comm.get("phase", "Status update"))
            self.map_comm_trigger.setText(comm.get("trigger", ""))
            self.map_comm_message.setPlainText(comm.get("message", ""))
        finally:
            self._loading = False

    def _save_exchange_form(self) -> None:
        if self._loading:
            return
        row = self.exchange_list.currentRow()
        if row < 0 or row >= len(self._task_dialog._communication_specs):
            return
        source = self.map_comm_from.currentText().strip()
        target = self.map_comm_to.currentText().strip()
        self._task_dialog._communication_specs[row] = {
            "from_agent": source,
            "to_agent": target,
            "phase": self.map_comm_phase.currentText().strip() or "Status update",
            "trigger": self.map_comm_trigger.text().strip(),
            "message": self.map_comm_message.toPlainText().strip(),
        }
        self._task_dialog._refresh_communication_list()
        item = self.exchange_list.item(row)
        if item:
            item.setText(self._communication_label(self._task_dialog._communication_specs[row]))

    @staticmethod
    def _communication_label(spec: dict[str, str]) -> str:
        return (
            f"{spec.get('from_agent', '?')} -> {spec.get('to_agent', '?')} "
            f"[{spec.get('phase', 'Exchange')}]"
        )


class AgentCommunicationDialog(QDialog):
    """Small editor for one planned agent-to-agent exchange."""

    def __init__(
        self,
        agents: list[str],
        communication: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.communication: dict[str, str] | None = None
        self.setWindowTitle("Communication Exchange")
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        data = communication or {}

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.from_combo = QComboBox()
        self.from_combo.addItems(agents)
        self.to_combo = QComboBox()
        self.to_combo.addItems(agents)
        self.phase_combo = QComboBox()
        self.phase_combo.setEditable(True)
        self.phase_combo.addItems([
            "Planning",
            "Implementation",
            "Review",
            "Testing",
            "Status update",
            "Completion",
        ])
        self.trigger_edit = QLineEdit()
        self.trigger_edit.setPlaceholderText("When should this exchange happen?")
        self.message_edit = QTextEdit()
        self.message_edit.setMinimumHeight(110)
        self.message_edit.setPlaceholderText(
            "What should be exchanged: findings, files, decisions, blockers, tests, or final notes."
        )

        if data.get("from_agent") in agents:
            self.from_combo.setCurrentText(data["from_agent"])
        if data.get("to_agent") in agents:
            self.to_combo.setCurrentText(data["to_agent"])
        if data.get("phase"):
            self.phase_combo.setCurrentText(data["phase"])
        self.trigger_edit.setText(data.get("trigger", ""))
        self.message_edit.setPlainText(data.get("message", ""))

        form.addRow("From", self.from_combo)
        form.addRow("To", self.to_combo)
        form.addRow("Phase", self.phase_combo)
        form.addRow("Trigger", self.trigger_edit)
        form.addRow("Message", self.message_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        source = self.from_combo.currentText().strip()
        target = self.to_combo.currentText().strip()
        if not source or not target:
            QMessageBox.warning(self, "Communication", "Choose both agents.")
            return
        if source == target:
            QMessageBox.warning(self, "Communication", "Choose two different agents.")
            return
        self.communication = {
            "from_agent": source,
            "to_agent": target,
            "phase": self.phase_combo.currentText().strip() or "Status update",
            "trigger": self.trigger_edit.text().strip(),
            "message": self.message_edit.toPlainText().strip(),
        }
        self.accept()


class AgentRunWindow(QDialog):
    """Small live log window for a background agent run."""

    log_line = pyqtSignal(str)
    finished = pyqtSignal(str)
    approval_requested = pyqtSignal(dict, object)

    def __init__(self, spec: AgentTaskSpec, parent: QWidget | None = None):
        super().__init__(parent)
        self._spec = spec
        self._thread = None
        self._control = None
        self._run_dir: str | None = None
        self.setWindowTitle(f"Agent Task - {spec.title}")
        self.setMinimumSize(620, 420)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel(f"<b>{spec.title}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title)

        self.approval_panel = QFrame()
        self.approval_panel.setStyleSheet(
            "QFrame { background: #2b2b3a; border: 1px solid #555577; border-radius: 6px; }"
            "QLabel { color: #eeeeff; background: transparent; }"
        )
        approval_layout = QHBoxLayout(self.approval_panel)
        approval_layout.setContentsMargins(10, 8, 10, 8)
        self.approval_label = QLabel()
        self.approval_label.setWordWrap(True)
        approve_btn = QPushButton("Approve")
        deny_btn = QPushButton("Decline")
        approve_btn.clicked.connect(lambda: self._finish_approval(True))
        deny_btn.clicked.connect(lambda: self._finish_approval(False))
        approval_layout.addWidget(self.approval_label, stretch=1)
        approval_layout.addWidget(approve_btn)
        approval_layout.addWidget(deny_btn)
        self.approval_panel.hide()
        self._pending_approval = None
        root.addWidget(self.approval_panel)

        self.tabs = QTabWidget()
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.trace_view = QTextEdit()
        self.trace_view.setReadOnly(True)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.final_view = QTextEdit()
        self.final_view.setReadOnly(True)
        self.final_view.setPlaceholderText("Final report appears here when the task finishes.")
        self.tabs.addTab(self.log_view, "Live Log")
        self.tabs.addTab(self.trace_view, "Model Trace")
        self.tabs.addTab(self.final_view, "Final Report")
        root.addWidget(self.tabs, stretch=1)

        row = QHBoxLayout()
        self.status_lbl = QLabel("Starting...")
        self.diff_btn = QPushButton("View Diff")
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._open_diff)
        self.open_logs_btn = QPushButton("Open Logs")
        self.open_logs_btn.setEnabled(False)
        self.open_logs_btn.clicked.connect(self._open_log_folder)
        self.open_scope_btn = QPushButton("Open Scope Folder")
        self.open_scope_btn.clicked.connect(self._open_scope_folder)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_run)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        row.addWidget(self.status_lbl)
        row.addStretch()
        row.addWidget(self.diff_btn)
        row.addWidget(self.open_logs_btn)
        row.addWidget(self.open_scope_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(close_btn)
        root.addLayout(row)

        self.log_line.connect(self._append_log)
        self.finished.connect(self._on_finished)
        self.approval_requested.connect(self._show_approval)
        fit_window_to_screen(self, preferred_width=700, preferred_height=500)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._thread is None:
            self._start_runner()

    def _start_runner(self) -> None:
        from core.agent_runner import AgentRunControl, AgentTaskRunner

        self._control = AgentRunControl()
        runner = AgentTaskRunner(
            approval_callback=self._request_approval,
            control=self._control,
        )

        def run_and_finish():
            run_dir = runner.run(self._spec, self.log_line.emit)
            self.finished.emit(str(run_dir))

        import threading

        self._thread = threading.Thread(target=run_and_finish, daemon=True)
        self._thread.start()

    def _request_approval(self, request: dict) -> bool:
        import threading

        event = threading.Event()
        state = {"event": event, "approved": False}
        self.approval_requested.emit(request, state)
        event.wait()
        return bool(state["approved"])

    def _append_log(self, line: str) -> None:
        self.log_view.append(line)

    def _on_finished(self, run_dir: str) -> None:
        self._run_dir = run_dir
        self.status_lbl.setText(f"Finished. Log: {run_dir}")
        self.cancel_btn.setEnabled(False)
        self.open_logs_btn.setEnabled(True)
        self.diff_btn.setEnabled((Path(run_dir) / "diff.patch").exists())
        self._load_finished_artifacts(Path(run_dir))

    def _load_finished_artifacts(self, run_dir: Path) -> None:
        trace_path = run_dir / "verbose.log"
        final_path = run_dir / "final.md"
        if trace_path.exists():
            self.trace_view.setPlainText(trace_path.read_text(encoding="utf-8", errors="replace"))
        if final_path.exists():
            self.final_view.setPlainText(final_path.read_text(encoding="utf-8", errors="replace"))

    def _show_approval(self, request: dict, state: object) -> None:
        details = request.get("details", {})
        detail_text = ", ".join(f"{k}={v}" for k, v in details.items())
        self._pending_approval = state
        self.approval_label.setText(f"Agent requests: {request.get('action')}\n{detail_text}")
        self.approval_panel.show()
        self.raise_()

    def _finish_approval(self, approved: bool) -> None:
        if not self._pending_approval:
            return
        self._pending_approval["approved"] = approved
        self._pending_approval["event"].set()
        self._pending_approval = None
        self.approval_panel.hide()

    def _cancel_run(self) -> None:
        if self._control is not None:
            self._control.cancel()
        self.status_lbl.setText("Cancelling...")
        self.cancel_btn.setEnabled(False)

    def _open_log_folder(self) -> None:
        if self._run_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._run_dir))

    def _open_scope_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._spec.scope_folder))

    def _open_diff(self) -> None:
        if not self._run_dir:
            return
        path = Path(self._run_dir) / "diff.patch"
        if path.exists():
            viewer = DiffViewer(path, parent=None)
            _diff_windows.append(viewer)
            viewer.destroyed.connect(lambda _obj=None, w=viewer: _diff_windows.remove(w) if w in _diff_windows else None)
            viewer.show()


class DiffViewer(QDialog):
    def __init__(self, diff_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Agent Diff")
        self.setMinimumSize(760, 520)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        layout = QVBoxLayout(self)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        viewer.setPlainText(diff_path.read_text(encoding="utf-8", errors="replace"))
        layout.addWidget(viewer)
        fit_window_to_screen(self, preferred_width=820, preferred_height=620)


class AgentRunHistoryWindow(QDialog):
    """Browse previous agent task runs without starting a new task."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._runs_root = Path(__file__).resolve().parents[1] / "memory" / "agent_runs"
        self._current_run: Path | None = None
        self.setWindowTitle("Agent Task History")
        self.setMinimumSize(820, 520)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.run_list = QListWidget()
        self.run_list.currentItemChanged.connect(self._load_selected_run)
        splitter.addWidget(self.run_list)

        self.tabs = QTabWidget()
        self.summary_view = QTextEdit()
        self.log_view = QTextEdit()
        self.trace_view = QTextEdit()
        self.diff_view = QTextEdit()
        for view in (self.summary_view, self.log_view, self.trace_view, self.diff_view):
            view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(self.summary_view, "Summary")
        self.tabs.addTab(self.log_view, "Run Log")
        self.tabs.addTab(self.trace_view, "Model Trace")
        self.tabs.addTab(self.diff_view, "Diff")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, stretch=1)

        row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_runs)
        open_logs_btn = QPushButton("Open Logs")
        open_logs_btn.clicked.connect(self._open_current_run)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        row.addStretch()
        row.addWidget(refresh_btn)
        row.addWidget(open_logs_btn)
        row.addWidget(close_btn)
        root.addLayout(row)

        self._load_runs()
        fit_window_to_screen(self, preferred_width=900, preferred_height=620)

    def _load_runs(self) -> None:
        self.run_list.clear()
        self._runs_root.mkdir(parents=True, exist_ok=True)
        runs = sorted(
            (path for path in self._runs_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        for run_dir in runs:
            item = QListWidgetItem(self._display_name(run_dir))
            item.setData(Qt.ItemDataRole.UserRole, str(run_dir))
            self.run_list.addItem(item)
        if self.run_list.count():
            self.run_list.setCurrentRow(0)
        else:
            self._clear_views("No agent task runs yet.")

    def _load_selected_run(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        run_dir = Path(current.data(Qt.ItemDataRole.UserRole))
        self._current_run = run_dir
        self.summary_view.setPlainText(self._summary_text(run_dir))
        self.log_view.setPlainText(self._read_text(run_dir / "run.log"))
        self.trace_view.setPlainText(self._read_text(run_dir / "verbose.log"))
        self.diff_view.setPlainText(self._read_text(run_dir / "diff.patch") or "(no diff artifact)")

    def _open_current_run(self) -> None:
        if self._current_run:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_run)))

    def _clear_views(self, text: str) -> None:
        for view in (self.summary_view, self.log_view, self.trace_view, self.diff_view):
            view.setPlainText(text)

    def _summary_text(self, run_dir: Path) -> str:
        task = self._read_text(run_dir / "task.json")
        final = self._read_text(run_dir / "final.md") or "(no final report)"
        return f"Run folder:\n{run_dir}\n\nFinal report:\n{final}\n\nTask spec:\n{task or '(missing task.json)'}"

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _display_name(run_dir: Path) -> str:
        task_path = run_dir / "task.json"
        if task_path.exists():
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
                title = str(task.get("title") or "").strip()
                if title:
                    return f"{run_dir.name[:15]}  {title}"
            except Exception:
                pass
        return run_dir.name
