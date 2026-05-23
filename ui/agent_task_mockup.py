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

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable
import html
import json
import math

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QPainterPath, QPen, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from ui.agent_run_log_parser import parse_live_log_event
from ui.window_utils import enable_standard_window_controls, fit_window_to_screen


TaskSubmitCallback = Callable[["AgentTaskSpec"], None]
ApprovalNoticeCallback = Callable[[str, bool], None]
_agent_run_windows: list["AgentRunWindow"] = []
_agent_task_dialogs: list["AgentTaskDialog"] = []
_agent_history_windows: list["AgentRunHistoryWindow"] = []
_diff_windows: list["DiffViewer"] = []


ROLE_RESPONSIBILITIES = {
    "Coordinator": "Break down the task, assign work, merge decisions, arbitrate conflicts, and decide when the group is done.",
    "Planner": "Turn the objective into a concrete plan, identify risks and dependencies, and hand clear next steps to the right agent.",
    "Implementer": "Make the code changes inside the selected scope, keep edits focused, run relevant checks, and report risks or blockers.",
    "Reviewer": "Inspect proposed changes, call out defects or missing tests, request fixes when needed, and approve completion when no Coordinator exists.",
    "Tester": "Design and run focused verification, reproduce failures, report exact commands and results, and confirm fixes.",
    "Researcher": "Gather read-only context before implementation: inspect files, docs, logs, APIs, and constraints, then brief the team with findings and recommendations.",
}


def role_responsibility(role: str) -> str:
    return ROLE_RESPONSIBILITIES.get(role.strip(), "")


def is_role_template(text: str) -> bool:
    normalized = " ".join(text.split())
    return any(" ".join(value.split()) == normalized for value in ROLE_RESPONSIBILITIES.values())


@dataclass(frozen=True)
class AgentRoleSpec:
    """One planned agent participating in the task."""

    name: str
    role: str
    provider: str
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
    provider: str
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
    shell_permission_mode: str = "auto"
    network_permission_mode: str = "never"
    git_permission_mode: str = "auto"
    file_create_permission_mode: str = "auto"
    file_edit_permission_mode: str = "auto"
    file_delete_permission_mode: str = "never"
    allowed_file_globs: list[str] = field(default_factory=list)
    blocked_file_globs: list[str] = field(default_factory=list)
    required_context: str = ""
    completion_criteria: str = ""
    report_format: str = "Summary + changed files + verification"
    agents: list[AgentRoleSpec] = field(default_factory=list)
    communications: list[AgentCommunicationSpec] = field(default_factory=list)
    parallel_read_only_briefing: bool = True
    full_turn_max_tokens: int = 4096
    delta_turn_max_tokens: int = 3072
    read_only_max_tokens: int = 1536
    agent_temperature: float = 0.0
    tool_result_text_limit: int = 6000
    tool_result_command_limit: int = 8000
    tool_result_value_limit: int = 3000
    tool_result_list_limit: int = 120
    visible_files_full_limit: int = 200
    visible_files_delta_limit: int = 80


def retry_spec_from_run(run_dir: Path) -> AgentTaskSpec:
    """Load the original task spec for a previous run."""
    task_path = run_dir / "task.json"
    if not task_path.exists():
        raise ValueError("Selected run does not have task.json.")
    data = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Selected run task.json is not an object.")
    return AgentTaskDialog._spec_from_dict(data)


def continue_spec_from_run(run_dir: Path) -> AgentTaskSpec:
    """Load a previous run as a continuation task with compact prior context."""
    spec = retry_spec_from_run(run_dir)
    context = _previous_run_context(run_dir)
    required = spec.required_context.strip()
    combined = (required + "\n\n" if required else "") + context
    return replace(spec, title=f"Continue: {spec.title}", required_context=combined)


def _previous_run_context(run_dir: Path) -> str:
    final = _read_run_text(run_dir / "final.md")
    error = _read_run_text(run_dir / "error.txt")
    run_log = _read_run_text(run_dir / "run.log")
    parts = [f"Continuing from previous agent run: {run_dir}"]
    if final:
        parts.append("Previous final report:\n" + _compact_for_continue(final, 3000))
    if error:
        parts.append("Previous error:\n" + _compact_for_continue(error, 3000))
    if run_log:
        useful_log = _filtered_continue_run_log(run_log)
        if useful_log:
            parts.append("Previous useful run events:\n" + _compact_for_continue(useful_log, 2200, tail_only=True))
    return "\n\n".join(parts)


def _read_run_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _compact_for_continue(text: str, max_chars: int, *, tail_only: bool = False) -> str:
    if len(text) <= max_chars:
        return text
    if tail_only:
        return f"... [earlier content omitted; showing last {max_chars} chars] ...\n" + text[-max_chars:]
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + f"\n... [middle truncated {len(text) - max_chars} chars] ...\n" + text[-tail:]


def _filtered_continue_run_log(text: str, *, max_lines: int = 40) -> str:
    noisy_patterns = (
        "model streaming response:",
        "model response still streaming",
        "model call still waiting",
        "model first token after",
        "requesting LLM tool response",
        "requesting JSON repair",
        "JSON repair response received",
        "prompt prepared for",
    )
    useful_markers = (
        "agent turn ",
        "agent read-only turn:",
        " thought:",
        " tool call:",
        "tool ",
        "message:",
        "LLM call failed:",
        "agent response parse failed:",
        "JSON repair response invalid:",
        "using local fallback",
        "returned final response",
        "agent run paused",
        "agent run finished",
        "agent reached turn limit",
        "completion requires ",
        "routing by ",
    )
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(pattern in line for pattern in noisy_patterns):
            continue
        if any(marker in line for marker in useful_markers):
            lines.append(line)
    return "\n".join(lines[-max_lines:])


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
    notice_callback = _approval_notice_callback_for(owner)
    action.triggered.connect(lambda: open_agent_task_dialog(None, on_submit, notice_callback))
    return action


def make_agent_history_action(owner: QWidget, parent: QWidget | None = None) -> QAction:
    """Create the tray QAction for browsing previous agent runs."""
    action = QAction("Agent task history...", owner)
    notice_callback = _approval_notice_callback_for(owner)
    action.triggered.connect(lambda: open_agent_history(None, notice_callback))
    return action


def open_agent_history(
    parent: QWidget | None = None,
    approval_notice_callback: ApprovalNoticeCallback | None = None,
) -> None:
    window = AgentRunHistoryWindow(parent=parent, approval_notice_callback=approval_notice_callback)
    _agent_history_windows.append(window)
    window.destroyed.connect(
        lambda _obj=None, w=window: _agent_history_windows.remove(w)
        if w in _agent_history_windows else None
    )
    window.show()


def launch_agent_run_window(
    spec: "AgentTaskSpec",
    parent: QWidget | None = None,
    approval_notice_callback: ApprovalNoticeCallback | None = None,
) -> "AgentRunWindow":
    window = AgentRunWindow(spec, parent=parent, approval_notice_callback=approval_notice_callback)
    _agent_run_windows.append(window)
    window.destroyed.connect(
        lambda _obj=None, w=window: _agent_run_windows.remove(w)
        if w in _agent_run_windows else None
    )
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def open_agent_task_dialog(
    parent: QWidget | None = None,
    on_submit: TaskSubmitCallback | None = None,
    approval_notice_callback: ApprovalNoticeCallback | None = None,
) -> AgentTaskSpec | None:
    """
    Show the mock dialog and return the accepted task spec.

    If ``on_submit`` is supplied, it is called after validation.  Without a real
    agent runner, the dialog shows a confirmation containing the resolved spec.
    """
    dialog = AgentTaskDialog(
        parent=parent,
        on_submit=on_submit,
        approval_notice_callback=approval_notice_callback,
    )
    _agent_task_dialogs.append(dialog)
    dialog.destroyed.connect(
        lambda _obj=None, w=dialog: _agent_task_dialogs.remove(w)
        if w in _agent_task_dialogs else None
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return None


def _approval_notice_callback_for(owner: object) -> ApprovalNoticeCallback | None:
    callback = getattr(owner, "notify_agent_approval", None)
    if not callable(callback):
        return None

    def notify(text: str, resolved: bool) -> None:
        callback(text, resolved=resolved)

    return notify


class AgentTaskDialog(QDialog):
    _last_task_spec: AgentTaskSpec | None = None  # Class variable to store last submitted task
    """Mock GUI for collecting a complete, sandboxed agent task request."""

    def __init__(
        self,
        parent: QWidget | None = None,
        on_submit: TaskSubmitCallback | None = None,
        approval_notice_callback: ApprovalNoticeCallback | None = None,
    ):
        super().__init__(parent)
        self._on_submit = on_submit
        self._approval_notice_callback = approval_notice_callback
        self.task_spec: AgentTaskSpec | None = None
        self._agent_specs: list[dict[str, str]] = []
        self._communication_specs: list[dict[str, str]] = []
        self._current_agent_row = -1
        self._loading_agent = False
        self._communication_window: AgentCommunicationMapWindow | None = None
        self._advanced_groups: list[QGroupBox] = []
        self._advanced_visible = False

        self.setWindowTitle("Start Agent Task")
        self.setMinimumSize(560, 420)
        enable_standard_window_controls(self)

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

        # Add Copy from Last Task button
        copy_btn = QPushButton("Copy from Last Task")
        copy_btn.setToolTip("Prefill all fields from the last started task.")
        copy_btn.clicked.connect(self._copy_from_last_task)
        root.addWidget(copy_btn)

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
        content_layout.addWidget(self._output_group())
        advanced_btn = QPushButton("Advanced Settings")
        advanced_btn.clicked.connect(self._toggle_advanced_settings)
        content_layout.addWidget(advanced_btn)
        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(10)
        for group in (
            self._scope_filters_group(),
            self._permissions_group(),
            self._runtime_group(),
            self._prompt_limits_group(),
        ):
            self._advanced_groups.append(group)
            advanced_layout.addWidget(group)
        self.advanced_panel.hide()
        content_layout.addWidget(self.advanced_panel)
        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)
        root.addWidget(self._buttons())

    def _copy_from_last_task(self):
        spec = AgentTaskDialog._last_task_spec or self._load_last_task_spec()
        if spec:
            AgentTaskDialog._last_task_spec = spec
            self._load_from_spec(spec)
            QMessageBox.information(self, "Copied", "Fields have been filled from the last task.")
        else:
            QMessageBox.information(self, "No Previous Task", "No previous task found to copy.")

    def _load_from_spec(self, spec: AgentTaskSpec):
        # Fill all fields from the given AgentTaskSpec
        self.title_edit.setText(spec.title)
        self.objective_edit.setPlainText(spec.objective)
        self.scope_edit.setText(spec.scope_folder)
        self.sandbox_combo.setCurrentText(spec.sandbox_mode)
        self.approval_combo.setCurrentText(spec.approval_policy)
        self.provider_combo.setCurrentText(getattr(spec, "provider", "same as app"))
        self.model_edit.setText(spec.model)
        self.reasoning_combo.setCurrentText(spec.reasoning_effort)
        self.runtime_minutes.setValue(spec.max_runtime_minutes)
        self.max_turns.setValue(spec.max_turns)
        self.allow_shell.setCurrentText(getattr(spec, "shell_permission_mode", self._permission_mode_from_bool(spec.allow_shell)))
        self.allow_network.setCurrentText(getattr(spec, "network_permission_mode", self._permission_mode_from_bool(spec.allow_network)))
        self.allow_git.setCurrentText(getattr(spec, "git_permission_mode", self._permission_mode_from_bool(spec.allow_git)))
        self.allow_create.setCurrentText(getattr(spec, "file_create_permission_mode", self._permission_mode_from_bool(spec.allow_file_create)))
        self.allow_edit.setCurrentText(getattr(spec, "file_edit_permission_mode", self._permission_mode_from_bool(spec.allow_file_edit)))
        self.allow_delete.setCurrentText(getattr(spec, "file_delete_permission_mode", self._permission_mode_from_bool(spec.allow_file_delete)))
        self.allowed_globs_edit.setText(", ".join(spec.allowed_file_globs))
        self.blocked_globs_edit.setText(", ".join(spec.blocked_file_globs))
        self.full_turn_tokens.setValue(getattr(spec, "full_turn_max_tokens", 4096))
        self.delta_turn_tokens.setValue(getattr(spec, "delta_turn_max_tokens", 3072))
        self.read_only_tokens.setValue(getattr(spec, "read_only_max_tokens", 1536))
        self.agent_temperature.setValue(float(getattr(spec, "agent_temperature", 0.0)))
        self.tool_text_limit.setValue(getattr(spec, "tool_result_text_limit", 6000))
        self.tool_command_limit.setValue(getattr(spec, "tool_result_command_limit", 8000))
        self.tool_value_limit.setValue(getattr(spec, "tool_result_value_limit", 3000))
        self.tool_list_limit.setValue(getattr(spec, "tool_result_list_limit", 120))
        self.visible_files_full_limit.setValue(getattr(spec, "visible_files_full_limit", 200))
        self.visible_files_delta_limit.setValue(getattr(spec, "visible_files_delta_limit", 80))
        self.required_context_edit.setPlainText(spec.required_context)
        self.completion_edit.setPlainText(spec.completion_criteria)
        self.report_combo.setCurrentText(spec.report_format)
        self.parallel_briefing.setChecked(bool(getattr(spec, "parallel_read_only_briefing", True)))
        # Agents and communications
        self._agent_specs = [
            {
                "name": a.name,
                "role": a.role,
                "provider": getattr(a, "provider", "same as task"),
                "model": a.model,
                "responsibility": a.responsibility,
            }
            for a in spec.agents
        ]
        self._communication_specs = [
            {
                "from_agent": c.from_agent,
                "to_agent": c.to_agent,
                "phase": c.phase,
                "trigger": c.trigger,
                "message": c.message,
            }
            for c in spec.communications
        ]
        self._refresh_agent_list()
        self._refresh_communication_list()
        if self.agent_list.count():
            self.agent_list.setCurrentRow(0)

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
        model_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.setEditable(True)
        self.provider_combo.addItems([
            "same as app",
            "copilot",
            "chatgpt",
            "openai",
            "anthropic",
            "groq",
            "google",
        ])
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Type any model name...")
        model_row.addWidget(self.provider_combo, stretch=1)
        model_row.addWidget(self.model_edit, stretch=2)
        model_widget = QWidget()
        model_widget.setLayout(model_row)
        form.addRow("Model", model_widget)
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
        self.agent_role_combo.currentTextChanged.connect(self._agent_role_changed)
        self.agent_provider_combo = QComboBox()
        self.agent_provider_combo.hide()
        self.agent_provider_combo.setEditable(True)
        self.agent_provider_combo.addItems([
            "same as task",
            "groq",
            "openai",
            "anthropic",
            "chatgpt",
            "copilot",
        ])
        self.agent_provider_combo.currentTextChanged.connect(self._save_current_agent)
        self.agent_model_edit = QLineEdit()
        self.agent_model_edit.hide()
        self.agent_model_edit.setPlaceholderText("same as task")
        self.agent_model_edit.textChanged.connect(self._save_current_agent)
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
        return box

    def _scope_filters_group(self) -> QGroupBox:
        box = QGroupBox("Scope Filters")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)
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

    def _toggle_advanced_settings(self) -> None:
        self._advanced_visible = not self._advanced_visible
        self.advanced_panel.setVisible(self._advanced_visible)
        self._fit_to_screen()

    def _permissions_group(self) -> QGroupBox:
        box = QGroupBox("Permission Modes")
        form = QFormLayout(box)
        form.setSpacing(8)

        self.allow_shell = self._permission_combo("auto")
        self.allow_network = self._permission_combo("never")
        self.allow_git = self._permission_combo("auto")
        self.allow_create = self._permission_combo("auto")
        self.allow_edit = self._permission_combo("auto")
        self.allow_delete = self._permission_combo("never")

        form.addRow("Shell", self.allow_shell)
        form.addRow("Network", self.allow_network)
        form.addRow("Git", self.allow_git)
        form.addRow("Create files", self.allow_create)
        form.addRow("Edit files", self.allow_edit)
        form.addRow("Delete files", self.allow_delete)
        return box

    @staticmethod
    def _permission_combo(default: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(["auto", "ask permission", "never permit"])
        combo.setCurrentText(default)
        return combo

    @staticmethod
    def _permission_enabled(combo: QComboBox) -> bool:
        return combo.currentText() != "never permit"

    @staticmethod
    def _permission_mode_from_bool(enabled: bool) -> str:
        return "auto" if enabled else "never permit"

    def _runtime_group(self) -> QGroupBox:
        box = QGroupBox("Runtime")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItems(["low", "medium", "high", "xhigh"])

        self.approval_combo = QComboBox()
        self.approval_combo.addItems(
            [
                "never escalate",
                "auto-approve safe reads",
                "ask before escalation",
            ]
        )
        self.approval_combo.setCurrentText("ask before escalation")

        self.runtime_minutes = QSpinBox()
        self.runtime_minutes.setRange(1, 480)
        self.runtime_minutes.setSuffix(" min")

        self.max_turns = QSpinBox()
        self.max_turns.setRange(1, 200)
        self.parallel_briefing = QCheckBox("Start with parallel read-only briefing")
        self.parallel_briefing.setChecked(True)
        self.agent_temperature = QDoubleSpinBox()
        self.agent_temperature.setRange(0.0, 2.0)
        self.agent_temperature.setSingleStep(0.1)
        self.agent_temperature.setDecimals(1)
        self.agent_temperature.setValue(0.0)

        form.addRow("Reasoning", self.reasoning_combo)
        form.addRow("Approvals", self.approval_combo)
        form.addRow("Time limit", self.runtime_minutes)
        form.addRow("Turn limit", self.max_turns)
        form.addRow("Briefing", self.parallel_briefing)
        form.addRow("Agent temperature", self.agent_temperature)
        return box

    def _prompt_limits_group(self) -> QGroupBox:
        box = QGroupBox("Prompt & Token Limits")
        form = QFormLayout(box)
        form.setSpacing(10)

        self.full_turn_tokens = self._number_spin(256, 16000, 4096)
        self.delta_turn_tokens = self._number_spin(256, 16000, 3072)
        self.read_only_tokens = self._number_spin(256, 8000, 1536)
        self.tool_text_limit = self._number_spin(500, 50000, 6000)
        self.tool_command_limit = self._number_spin(500, 50000, 8000)
        self.tool_value_limit = self._number_spin(200, 30000, 3000)
        self.tool_list_limit = self._number_spin(10, 1000, 120)
        self.visible_files_full_limit = self._number_spin(10, 1000, 200)
        self.visible_files_delta_limit = self._number_spin(10, 1000, 80)

        form.addRow("Full turn max tokens", self.full_turn_tokens)
        form.addRow("Delta turn max tokens", self.delta_turn_tokens)
        form.addRow("Read-only max tokens", self.read_only_tokens)
        form.addRow("Tool text chars", self.tool_text_limit)
        form.addRow("Command output chars", self.tool_command_limit)
        form.addRow("Nested value chars", self.tool_value_limit)
        form.addRow("Tool list items", self.tool_list_limit)
        form.addRow("Full visible files", self.visible_files_full_limit)
        form.addRow("Delta visible files", self.visible_files_delta_limit)
        return box

    @staticmethod
    def _number_spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

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
        self.provider_combo.setCurrentText("same as app")
        self.model_edit.setText("gpt-5.3-codex")
        self.allow_shell.setCurrentText("auto")
        self.allow_network.setCurrentText("never permit")
        self.allow_git.setCurrentText("auto")
        self.allow_create.setCurrentText("auto")
        self.allow_edit.setCurrentText("auto")
        self.allow_delete.setCurrentText("never permit")
        self.runtime_minutes.setValue(60)
        self.max_turns.setValue(30)
        self.blocked_globs_edit.setText(".env, private/*, .git/*")
        self.full_turn_tokens.setValue(4096)
        self.delta_turn_tokens.setValue(3072)
        self.read_only_tokens.setValue(1536)
        self.tool_text_limit.setValue(6000)
        self.tool_command_limit.setValue(8000)
        self.tool_value_limit.setValue(3000)
        self.tool_list_limit.setValue(120)
        self.visible_files_full_limit.setValue(200)
        self.visible_files_delta_limit.setValue(80)
        self._agent_specs = [
            {
                "name": "Coordinator",
                "role": "Coordinator",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": role_responsibility("Coordinator"),
            },
            {
                "name": "Builder",
                "role": "Implementer",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": role_responsibility("Implementer"),
            },
            {
                "name": "Reviewer",
                "role": "Reviewer",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": role_responsibility("Reviewer"),
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
            "provider": "same as task",
            "model": "same as task",
            "responsibility": role_responsibility("Implementer"),
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
                self.agent_provider_combo.setCurrentText("same as task")
                self.agent_model_edit.clear()
                self.agent_responsibility_edit.clear()
                return
            agent = self._agent_specs[row]
            self.agent_name_edit.setText(agent.get("name", ""))
            self.agent_role_combo.setCurrentText(agent.get("role", "Implementer"))
            self.agent_provider_combo.setCurrentText(agent.get("provider", "same as task"))
            self.agent_model_edit.setText("" if agent.get("model", "same as task") == "same as task" else agent.get("model", ""))
            self.agent_responsibility_edit.setPlainText(agent.get("responsibility", ""))
        finally:
            self._loading_agent = False

    def _agent_role_changed(self, role: str) -> None:
        if self._loading_agent:
            return
        current = self.agent_responsibility_edit.toPlainText().strip()
        template = role_responsibility(role)
        if template and (not current or is_role_template(current)):
            self.agent_responsibility_edit.setPlainText(template)
        self._save_current_agent()

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
            "provider": self.agent_provider_combo.currentText().strip() or "same as task",
            "model": self.agent_model_edit.text().strip() or "same as task",
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
        # Save as last task spec for future dialogs
        AgentTaskDialog._last_task_spec = spec
        self._save_last_task_spec(spec)
        if self._on_submit is not None:
            self._on_submit(spec)
        else:
            launch_agent_run_window(
                spec,
                parent=None,
                approval_notice_callback=self._approval_notice_callback,
            )
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
        provider = self.provider_combo.currentText().strip()
        model = self.model_edit.text().strip()
        if not provider:
            raise ValueError("Choose a model provider.")
        if not model:
            raise ValueError("Add a model name.")
        agents = [
            AgentRoleSpec(
                name=(agent.get("name") or f"Agent {idx + 1}").strip(),
                role=(agent.get("role") or "Implementer").strip(),
                provider=(agent.get("provider") or "same as task").strip(),
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
            provider=provider,
            model=model,
            reasoning_effort=self.reasoning_combo.currentText(),
            max_runtime_minutes=self.runtime_minutes.value(),
            max_turns=self.max_turns.value(),
            allow_shell=self._permission_enabled(self.allow_shell),
            allow_network=self._permission_enabled(self.allow_network),
            allow_git=self._permission_enabled(self.allow_git),
            allow_file_create=self._permission_enabled(self.allow_create),
            allow_file_edit=self._permission_enabled(self.allow_edit),
            allow_file_delete=self._permission_enabled(self.allow_delete),
            shell_permission_mode=self.allow_shell.currentText(),
            network_permission_mode=self.allow_network.currentText(),
            git_permission_mode=self.allow_git.currentText(),
            file_create_permission_mode=self.allow_create.currentText(),
            file_edit_permission_mode=self.allow_edit.currentText(),
            file_delete_permission_mode=self.allow_delete.currentText(),
            allowed_file_globs=self._split_globs(self.allowed_globs_edit.text()),
            blocked_file_globs=self._split_globs(self.blocked_globs_edit.text()),
            required_context=self.required_context_edit.toPlainText().strip(),
            completion_criteria=self.completion_edit.toPlainText().strip(),
            report_format=self.report_combo.currentText(),
            agents=agents,
            communications=communications,
            parallel_read_only_briefing=self.parallel_briefing.isChecked(),
            full_turn_max_tokens=self.full_turn_tokens.value(),
            delta_turn_max_tokens=self.delta_turn_tokens.value(),
            read_only_max_tokens=self.read_only_tokens.value(),
            agent_temperature=self.agent_temperature.value(),
            tool_result_text_limit=self.tool_text_limit.value(),
            tool_result_command_limit=self.tool_command_limit.value(),
            tool_result_value_limit=self.tool_value_limit.value(),
            tool_result_list_limit=self.tool_list_limit.value(),
            visible_files_full_limit=self.visible_files_full_limit.value(),
            visible_files_delta_limit=self.visible_files_delta_limit.value(),
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

    @classmethod
    def _task_history_root(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "memory" / "agent_runs"

    @classmethod
    def _last_task_path(cls) -> Path:
        return cls._task_history_root() / "last_task.json"

    @classmethod
    def _save_last_task_spec(cls, spec: AgentTaskSpec) -> None:
        path = cls._last_task_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(spec), indent=2), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def _load_last_task_spec(cls) -> AgentTaskSpec | None:
        candidates = [cls._last_task_path()]
        root = cls._task_history_root()
        if root.exists():
            run_task_files = sorted(
                (path / "task.json" for path in root.iterdir() if path.is_dir() and (path / "task.json").exists()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(run_task_files)
        for path in candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls._spec_from_dict(data)
            except Exception:
                continue
        return None

    @staticmethod
    def _spec_from_dict(data: dict) -> AgentTaskSpec:
        agents = [
            AgentRoleSpec(
                name=str(agent.get("name") or "Agent"),
                role=str(agent.get("role") or "Implementer"),
                provider=str(agent.get("provider") or "same as task"),
                model=str(agent.get("model") or "same as task"),
                responsibility=str(agent.get("responsibility") or ""),
            )
            for agent in data.get("agents", []) or []
            if isinstance(agent, dict)
        ]
        communications = [
            AgentCommunicationSpec(
                from_agent=str(comm.get("from_agent") or ""),
                to_agent=str(comm.get("to_agent") or ""),
                phase=str(comm.get("phase") or ""),
                trigger=str(comm.get("trigger") or ""),
                message=str(comm.get("message") or ""),
            )
            for comm in data.get("communications", []) or []
            if isinstance(comm, dict)
        ]
        return AgentTaskSpec(
            title=str(data.get("title") or ""),
            objective=str(data.get("objective") or ""),
            scope_folder=str(data.get("scope_folder") or Path.cwd()),
            sandbox_mode=str(data.get("sandbox_mode") or "workspace-write: scope folder only"),
            approval_policy=str(data.get("approval_policy") or "ask before escalation"),
            provider=str(data.get("provider") or "same as app"),
            model=str(data.get("model") or ""),
            reasoning_effort=str(data.get("reasoning_effort") or "medium"),
            max_runtime_minutes=int(data.get("max_runtime_minutes") or 60),
            max_turns=int(data.get("max_turns") or 30),
            allow_shell=bool(data.get("allow_shell", True)),
            allow_network=bool(data.get("allow_network", False)),
            allow_git=bool(data.get("allow_git", True)),
            allow_file_create=bool(data.get("allow_file_create", True)),
            allow_file_edit=bool(data.get("allow_file_edit", True)),
            allow_file_delete=bool(data.get("allow_file_delete", False)),
            shell_permission_mode=str(data.get("shell_permission_mode") or ("auto" if data.get("allow_shell", True) else "never permit")),
            network_permission_mode=str(data.get("network_permission_mode") or ("auto" if data.get("allow_network", False) else "never permit")),
            git_permission_mode=str(data.get("git_permission_mode") or ("auto" if data.get("allow_git", True) else "never permit")),
            file_create_permission_mode=str(data.get("file_create_permission_mode") or ("auto" if data.get("allow_file_create", True) else "never permit")),
            file_edit_permission_mode=str(data.get("file_edit_permission_mode") or ("auto" if data.get("allow_file_edit", True) else "never permit")),
            file_delete_permission_mode=str(data.get("file_delete_permission_mode") or ("auto" if data.get("allow_file_delete", False) else "never permit")),
            allowed_file_globs=list(data.get("allowed_file_globs") or []),
            blocked_file_globs=list(data.get("blocked_file_globs") or []),
            required_context=str(data.get("required_context") or ""),
            completion_criteria=str(data.get("completion_criteria") or ""),
            report_format=str(data.get("report_format") or "Summary + changed files + verification"),
            agents=agents,
            communications=communications,
            parallel_read_only_briefing=bool(data.get("parallel_read_only_briefing", True)),
            full_turn_max_tokens=int(data.get("full_turn_max_tokens") or 4096),
            delta_turn_max_tokens=int(data.get("delta_turn_max_tokens") or 3072),
            read_only_max_tokens=int(data.get("read_only_max_tokens") or 1536),
            agent_temperature=float(data.get("agent_temperature", 0.0) or 0.0),
            tool_result_text_limit=int(data.get("tool_result_text_limit") or 6000),
            tool_result_command_limit=int(data.get("tool_result_command_limit") or 8000),
            tool_result_value_limit=int(data.get("tool_result_value_limit") or 3000),
            tool_result_list_limit=int(data.get("tool_result_list_limit") or 120),
            visible_files_full_limit=int(data.get("visible_files_full_limit") or 200),
            visible_files_delta_limit=int(data.get("visible_files_delta_limit") or 80),
        )


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


class _LiveAgentItem(QGraphicsItemGroup):
    WIDTH = 220
    HEIGHT = 150
    TEXT_WIDTH = 196

    def __init__(
        self,
        index: int,
        click_callback: Callable[[int], None],
        x: float,
        y: float,
        name: str,
        role: str,
        status: str,
        objective: str,
        health: str,
        active: bool,
        selected: bool,
    ):
        super().__init__()
        self._index = index
        self._click_callback = click_callback
        self.setAcceptHoverEvents(True)
        self.setZValue(5 if active or selected else 3)

        border = "#2f80ed" if active else "#7aa7df"
        fill = "#eaf4ff" if active else "#ffffff"
        if selected:
            border = "#1f6fd1"
            fill = "#dceeff"

        if active:
            for offset, alpha in ((-10, 62), (-5, 42), (0, 24)):
                glow = QGraphicsEllipseItem(
                    offset,
                    offset + 1,
                    self.WIDTH - offset * 2,
                    self.HEIGHT - offset * 2,
                )
                glow.setBrush(QBrush(QColor(47, 128, 237, alpha)))
                glow.setPen(QPen(Qt.PenStyle.NoPen))
                self.addToGroup(glow)

        shadow = QGraphicsEllipseItem(4, 6, self.WIDTH, self.HEIGHT)
        shadow.setBrush(QBrush(QColor(86, 105, 135, 34)))
        shadow.setPen(QPen(Qt.PenStyle.NoPen))
        self.addToGroup(shadow)

        node = QGraphicsRectItem(0, 0, self.WIDTH, self.HEIGHT)
        node.setBrush(QBrush(QColor(fill)))
        node.setPen(QPen(QColor(border), 2.2 if active or selected else 1.3))
        self.addToGroup(node)

        name_item = QGraphicsTextItem(name)
        name_item.setDefaultTextColor(QColor("#172033"))
        name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        name_item.setTextWidth(self.TEXT_WIDTH)
        name_item.setPos(12, 10)
        name_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(name_item)

        role_item = QGraphicsTextItem(role)
        role_item.setDefaultTextColor(QColor("#5f7088"))
        role_item.setFont(QFont("Segoe UI", 8))
        role_item.setTextWidth(self.TEXT_WIDTH)
        role_item.setPos(12, 32)
        role_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(role_item)

        status_item = QGraphicsTextItem(status or "Waiting")
        status_item.setDefaultTextColor(QColor("#24405f" if active else "#667085"))
        status_item.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold if active else QFont.Weight.Normal))
        status_item.setTextWidth(self.TEXT_WIDTH)
        status_item.setPos(12, 54)
        status_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(status_item)

        objective_item = QGraphicsTextItem(objective or "No current objective")
        objective_item.setDefaultTextColor(QColor("#344054"))
        objective_item.setFont(QFont("Segoe UI", 7))
        objective_item.setTextWidth(self.TEXT_WIDTH)
        objective_item.setPos(12, 78)
        objective_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(objective_item)

        health_item = QGraphicsTextItem(health)
        health_item.setDefaultTextColor(QColor("#697586"))
        health_item.setFont(QFont("Segoe UI", 7))
        health_item.setTextWidth(self.TEXT_WIDTH)
        health_item.setPos(12, 122)
        health_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addToGroup(health_item)
        self.setPos(x, y)

    def hoverEnterEvent(self, event):  # noqa: N802
        self.setOpacity(0.88)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self.setOpacity(1.0)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        self._click_callback(self._index)
        event.accept()


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
        enable_standard_window_controls(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        content_root = QVBoxLayout(scroll_content)
        content_root.setContentsMargins(0, 0, 0, 0)
        content_root.setSpacing(10)
        scroll.setWidget(scroll_content)
        root.addWidget(scroll, stretch=1)

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
        content_root.addLayout(toolbar)

        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        vertical_splitter.setChildrenCollapsible(False)
        vertical_splitter.setHandleWidth(10)
        vertical_splitter.setStyleSheet(
            "QSplitter::handle:vertical { background: #c6d0df; margin: 3px 0px; }"
            "QSplitter::handle:vertical:hover { background: #8ea6c4; }"
        )
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

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
        self.map_agent_provider = QComboBox()
        self.map_agent_provider.setEditable(True)
        self.map_agent_provider.addItems([
            "same as task",
            "copilot",
            "chatgpt",
            "openai",
            "anthropic",
            "groq",
            "google",
        ])
        self.map_agent_model = QLineEdit()
        self.map_agent_model.setPlaceholderText("same as task")
        self.map_agent_responsibility = QTextEdit()
        self.map_agent_responsibility.setMinimumHeight(70)
        self.map_agent_responsibility.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.map_agent_name.textChanged.connect(self._save_agent_form)
        self.map_agent_role.currentTextChanged.connect(self._map_agent_role_changed)
        self.map_agent_provider.currentTextChanged.connect(self._save_agent_form)
        self.map_agent_model.textChanged.connect(self._save_agent_form)
        self.map_agent_responsibility.textChanged.connect(self._save_agent_form)
        agent_form.addRow("Name", self.map_agent_name)
        agent_form.addRow("Role", self.map_agent_role)
        agent_form.addRow("Provider", self.map_agent_provider)
        agent_form.addRow("Model", self.map_agent_model)
        agent_form.addRow("Responsibility", self.map_agent_responsibility)
        agent_layout.addLayout(agent_form, stretch=0)
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
        self.map_comm_message.setMinimumHeight(80)
        self.map_comm_message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        comm_layout.addLayout(comm_form, stretch=1)
        splitter.addWidget(comm_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        vertical_splitter.addWidget(splitter)

        relationship_panel = QWidget()
        relationship_layout = QVBoxLayout(relationship_panel)
        relationship_layout.setContentsMargins(0, 10, 0, 0)
        relationship_layout.setSpacing(6)
        relationship_layout.addWidget(QLabel("Relationship / Exchange Map"))
        self.relationship_scene = QGraphicsScene(self)
        self.relationship_view = QGraphicsView(self.relationship_scene)
        self.relationship_view.setMinimumHeight(190)
        self.relationship_view.setStyleSheet("QGraphicsView { background: #eef3f9; border: 1px solid #c2ccda; }")
        relationship_layout.addWidget(self.relationship_view, stretch=1)
        vertical_splitter.addWidget(relationship_panel)
        vertical_splitter.setStretchFactor(0, 2)
        vertical_splitter.setStretchFactor(1, 3)
        vertical_splitter.setSizes([330, 260])
        content_root.addWidget(vertical_splitter, stretch=1)

        self._loading = False
        footer = QLabel("Select an agent or communication, or click an exchange in the bottom relationship map to edit it above.")
        footer.setStyleSheet("color: #777;")
        content_root.addWidget(footer)
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
        half_w, half_h = 72.0, 36.0
        border_offset = min(
            half_w / max(abs(ux), 0.001),
            half_h / max(abs(uy), 0.001),
        )
        gap = 8.0
        start_offset = border_offset + gap
        end_offset = border_offset + gap
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
        for ax, ay, tip_dir in ((tx, ty, 1), (sx, sy, -1)):
            bx = ax - tip_dir * ux * size
            by = ay - tip_dir * uy * size
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
                self.map_agent_provider.setCurrentText("same as task")
                self.map_agent_model.clear()
                self.map_agent_responsibility.clear()
                return
            agent = self._task_dialog._agent_specs[row]
            self.map_agent_name.setText(agent.get("name", ""))
            self.map_agent_role.setCurrentText(agent.get("role", "Implementer"))
            self.map_agent_provider.setCurrentText(agent.get("provider", "same as task"))
            self.map_agent_model.setText("" if agent.get("model", "same as task") == "same as task" else agent.get("model", ""))
            self.map_agent_responsibility.setPlainText(agent.get("responsibility", ""))
        finally:
            self._loading = False

    def _map_agent_role_changed(self, role: str) -> None:
        if self._loading:
            return
        current = self.map_agent_responsibility.toPlainText().strip()
        template = role_responsibility(role)
        if template and (not current or is_role_template(current)):
            self.map_agent_responsibility.setPlainText(template)
        self._save_agent_form()

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
            "provider": self.map_agent_provider.currentText().strip() or "same as task",
            "model": self.map_agent_model.text().strip() or "same as task",
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
        enable_standard_window_controls(self)
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


class AgentNudgeDialog(QDialog):
    """Single-window prompt for injecting a manual message into a live run."""

    def __init__(self, targets: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.nudge: dict[str, str] | None = None
        self.setWindowTitle("Nudge Agent")
        self.setMinimumWidth(460)
        enable_standard_window_controls(self)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.target_combo = QComboBox()
        self.target_combo.addItems(targets)
        self.message_edit = QTextEdit()
        self.message_edit.setMinimumHeight(130)
        self.message_edit.setPlaceholderText("Add a short instruction, correction, or context update.")

        form.addRow("To", self.target_combo)
        form.addRow("Message", self.message_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        target = self.target_combo.currentText().strip()
        message = self.message_edit.toPlainText().strip()
        if not target:
            QMessageBox.warning(self, "Nudge Agent", "Choose a target.")
            return
        if not message:
            QMessageBox.warning(self, "Nudge Agent", "Add a message.")
            return
        self.nudge = {"to": target, "message": message}
        self.accept()


class AgentRunWindow(QDialog):
    """Small live log window for a background agent run."""

    log_line = pyqtSignal(str)
    trace_entry = pyqtSignal(str)
    finished = pyqtSignal(str)
    approval_requested = pyqtSignal(dict, object)

    def __init__(
        self,
        spec: AgentTaskSpec,
        parent: QWidget | None = None,
        approval_notice_callback: ApprovalNoticeCallback | None = None,
    ):
        super().__init__(parent)
        self._spec = spec
        self._approval_notice_callback = approval_notice_callback
        self._thread = None
        self._control = None
        self._run_dir: str | None = None
        self._agent_roles = {agent.name: agent.role for agent in spec.agents}
        self._agent_names = [agent.name for agent in spec.agents] or ["Solo"]
        self._active_agent = self._agent_names[0]
        self._selected_agent = self._active_agent
        self._meeting_messages: list[dict[str, str]] = []
        self._pending_trace_entries: list[str] = []
        self._agent_states = {
            name: {
                "role": self._agent_roles.get(name, "Agent"),
                "status": "Waiting",
                "thought": "",
                "objective": "",
                "tool": "",
                "health": {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
                "history": [],
            }
            for name in self._agent_names
        }
        self.setWindowTitle(f"Agent Task - {spec.title}")
        self.setMinimumSize(1100, 680)
        enable_standard_window_controls(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel(f"<b>{spec.title}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title)

        self.approval_panel = QFrame()
        self._approval_style_dark = (
            "QFrame { background: #2b2b3a; border: 1px solid #555577; border-radius: 6px; }"
            "QLabel { color: #eeeeff; background: transparent; }"
        )
        self._approval_style_alert = (
            "QFrame { background: #fff3d6; border: 2px solid #f59e0b; border-radius: 6px; }"
            "QLabel { color: #3a2500; background: transparent; font-weight: 600; }"
        )
        self.approval_panel.setStyleSheet(self._approval_style_dark)
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
        self.meeting_scene = QGraphicsScene(self)
        self.meeting_view = QGraphicsView(self.meeting_scene)
        self.meeting_view.setMinimumSize(760, 500)
        self.meeting_view.setStyleSheet("QGraphicsView { background: #edf3fa; border: 1px solid #c2ccda; }")
        agent_detail_panel = QWidget()
        agent_detail_layout = QVBoxLayout(agent_detail_panel)
        agent_detail_layout.setContentsMargins(0, 0, 0, 0)
        agent_detail_layout.setSpacing(8)
        self.agent_summary_view = QTextEdit()
        self.agent_summary_view.setReadOnly(True)
        self.agent_summary_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.agent_summary_view.setMaximumHeight(250)
        self.agent_activity_view = QTextEdit()
        self.agent_activity_view.setReadOnly(True)
        self.agent_activity_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        agent_detail_layout.addWidget(self.agent_summary_view)
        agent_detail_layout.addWidget(QLabel("Recent Activity"))
        agent_detail_layout.addWidget(self.agent_activity_view, stretch=1)
        board_panel = QWidget()
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(8)
        board_layout.addWidget(QLabel("Shared Board"))
        self.shared_board_view = QTextEdit()
        self.shared_board_view.setReadOnly(True)
        self.shared_board_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.shared_board_view.setMinimumWidth(250)
        board_layout.addWidget(self.shared_board_view, stretch=1)
        meeting_splitter = QSplitter(Qt.Orientation.Horizontal)
        meeting_splitter.setChildrenCollapsible(False)
        meeting_splitter.addWidget(self.meeting_view)
        meeting_splitter.addWidget(board_panel)
        meeting_splitter.addWidget(agent_detail_panel)
        meeting_splitter.setStretchFactor(0, 6)
        meeting_splitter.setStretchFactor(1, 1)
        meeting_splitter.setStretchFactor(2, 2)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.trace_view = QTextEdit()
        self.trace_view.setReadOnly(True)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.trace_view.setPlaceholderText("Model prompts, responses, parsed JSON, and tool payloads appear here while the task runs.")
        self.final_view = QTextEdit()
        self.final_view.setReadOnly(True)
        self.final_view.setPlaceholderText("Final report appears here when the task finishes.")
        self.tabs.addTab(meeting_splitter, "Meeting")
        self.tabs.addTab(self.log_view, "Live Log")
        self.tabs.addTab(self.trace_view, "Model Trace")
        self.tabs.addTab(self.final_view, "Final Report")
        self.tabs.currentChanged.connect(self._flush_trace_if_visible)
        root.addWidget(self.tabs, stretch=1)
        self._refresh_shared_board()
        self._draw_live_meeting()

        row = QHBoxLayout()
        self.status_lbl = QLabel("Starting...")
        self.diff_btn = QPushButton("View Diff")
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._open_diff)
        self.open_result_btn = QPushButton("Open Result Folder")
        self.open_result_btn.setEnabled(False)
        self.open_result_btn.clicked.connect(self._open_result_folder)
        self.open_scope_btn = QPushButton("Open Scope Folder")
        self.open_scope_btn.clicked.connect(self._open_scope_folder)
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_run)
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self._continue_run)
        self.nudge_btn = QPushButton("Nudge Agent")
        self.nudge_btn.clicked.connect(self._send_manual_nudge)
        self.pause_btn = QPushButton("Pause After Turn")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_run)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        row.addWidget(self.status_lbl)
        row.addStretch()
        row.addWidget(self.diff_btn)
        row.addWidget(self.open_result_btn)
        row.addWidget(self.open_scope_btn)
        row.addWidget(self.retry_btn)
        row.addWidget(self.continue_btn)
        row.addWidget(self.nudge_btn)
        row.addWidget(self.pause_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(close_btn)
        root.addLayout(row)

        self.log_line.connect(self._append_log)
        self.trace_entry.connect(self._append_trace)
        self.finished.connect(self._on_finished)
        self.approval_requested.connect(self._show_approval)
        fit_window_to_screen(self, preferred_width=1380, preferred_height=820)

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
            run_dir = runner.run(self._spec, self.log_line.emit, self.trace_entry.emit)
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

    @staticmethod
    def _scroll_snapshot(view: QTextEdit) -> tuple[int, bool]:
        bar = view.verticalScrollBar()
        value = bar.value()
        return value, value >= bar.maximum() - 4

    @staticmethod
    def _restore_scroll(view: QTextEdit, old_value: int, was_at_bottom: bool) -> None:
        bar = view.verticalScrollBar()
        if was_at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(old_value, bar.maximum()))

    @classmethod
    def _set_plain_text_preserving_scroll(cls, view: QTextEdit, text: str) -> None:
        old_value, was_at_bottom = cls._scroll_snapshot(view)
        view.setPlainText(text)
        cls._restore_scroll(view, old_value, was_at_bottom)

    @classmethod
    def _set_html_preserving_scroll(cls, view: QTextEdit, text: str) -> None:
        old_value, was_at_bottom = cls._scroll_snapshot(view)
        view.setHtml(text)
        cls._restore_scroll(view, old_value, was_at_bottom)

    @classmethod
    def _append_html_preserving_scroll(cls, view: QTextEdit, text: str) -> None:
        old_value, was_at_bottom = cls._scroll_snapshot(view)
        view.append(text)
        cls._restore_scroll(view, old_value, was_at_bottom)

    @classmethod
    def _append_plain_text_preserving_scroll(cls, view: QTextEdit, text: str) -> None:
        old_value, was_at_bottom = cls._scroll_snapshot(view)
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        cls._restore_scroll(view, old_value, was_at_bottom)

    def _append_log(self, line: str) -> None:
        self._update_live_meeting(line)
        if self._is_hidden_live_log_line(line):
            return
        self._append_html_preserving_scroll(self.log_view, self._format_live_log_line(line))
        self._draw_live_meeting()

    def _append_trace(self, entry: str) -> None:
        if self.tabs.currentWidget() is not self.trace_view:
            self._pending_trace_entries.append(entry)
            return
        self._append_trace_text(entry)

    def _flush_trace_if_visible(self, _index: int | None = None) -> None:
        if self.tabs.currentWidget() is not self.trace_view or not self._pending_trace_entries:
            return
        entry = "".join(self._pending_trace_entries)
        self._pending_trace_entries.clear()
        self._append_trace_text(entry)

    def _append_trace_text(self, entry: str) -> None:
        self._append_plain_text_preserving_scroll(self.trace_view, entry)

    def _update_live_meeting(self, line: str) -> None:
        event = parse_live_log_event(line)
        body = event.body
        if event.kind == "agent_turn":
            name = event.agent
            self._ensure_agent_state(name)
            self._active_agent = name
            self._selected_agent = name if self._selected_agent not in self._agent_states else self._selected_agent
            self._set_agent_status(name, "Thinking", body)
            return
        if event.kind == "agent_read_only_turn":
            name = event.agent
            self._ensure_agent_state(name)
            self._active_agent = name
            self._selected_agent = name if self._selected_agent not in self._agent_states else self._selected_agent
            self._set_agent_status(name, "Read-only briefing", body)
            return
        if body.startswith("parallel read-only briefing started"):
            for name in self._agent_names:
                self._set_agent_status(name, "Joining briefing", body)
            return
        if body.startswith("parallel read-only briefing finished"):
            for name in self._agent_names:
                if self._agent_states[name].get("status") in {"Joining briefing", "Read-only briefing", "Calling model"}:
                    self._set_agent_status(name, "Briefed", body)
            return
        if body.startswith("requesting LLM tool response"):
            self._set_agent_status(self._active_agent, "Calling model", body)
            return
        if body.startswith("model call still waiting after "):
            elapsed = body.split(" after ", 1)[1].split(" via ", 1)[0]
            self._set_agent_status(self._active_agent, f"Waiting {elapsed}", body)
            return
        if body.startswith("model first token after "):
            elapsed = body.split(" after ", 1)[1].split(" via ", 1)[0]
            self._set_agent_status(self._active_agent, f"Receiving response ({elapsed})", body)
            return
        if body.startswith("model streaming response: "):
            self._set_agent_status(self._active_agent, "Receiving response", body)
            return
        if body.startswith("model response still streaming after "):
            self._set_agent_status(self._active_agent, "Still receiving response", body)
            return
        if body.startswith("LLM call failed: "):
            self._set_agent_status(self._active_agent, "Model error; retrying", body)
            self._agent_health(self._active_agent)["fallbacks"] += 1
            return
        if body.startswith("routing by latest directed message: "):
            route = body[len("routing by latest directed message: "):].strip()
            target = route.split(" -> ", 1)[1] if " -> " in route else route
            self._set_agent_status(self._active_agent, f"Handing off to {target}", body)
            return
        if body.startswith("routing by explicit next_agent: "):
            route = body[len("routing by explicit next_agent: "):].split(" (", 1)[0].strip()
            target = route.split(" -> ", 1)[1] if " -> " in route else route
            self._set_agent_status(self._active_agent, f"Explicit handoff to {target}", body)
            return
        if body.startswith("prompt prepared for "):
            summary = body
            if ": " in body:
                summary = body.split(": ", 1)[1]
            self._set_agent_status(self._active_agent, f"Prompt {summary}", body)
            return
        if body.startswith("model response received") or body.startswith("model callback response received"):
            self._record_model_latency(self._active_agent, body)
            self._set_agent_status(self._active_agent, "Parsing response", body)
            return
        if body.startswith("file payload in JSON response"):
            self._append_agent_history(self._active_agent, body)
            return
        if body.startswith("agent response parse failed"):
            self._agent_health(self._active_agent)["invalid_json"] += 1
            self._set_agent_status(self._active_agent, "Repairing response", body)
            return
        if body.startswith("requesting JSON repair"):
            self._set_agent_status(self._active_agent, "Repairing JSON", body)
            return
        if body.startswith("repaired invalid JSON locally") or body.startswith("JSON repair response received"):
            self._agent_health(self._active_agent)["repairs"] += 1
            self._set_agent_status(self._active_agent, "Parsing repaired JSON", body)
            return
        if body.startswith("using local fallback"):
            self._agent_health(self._active_agent)["fallbacks"] += 1
            self._set_agent_status(self._active_agent, "Retrying", body)
            return
        if body.startswith("agent run paused"):
            self.status_lbl.setText("Paused after current turn")
            return
        if body.startswith("agent run resumed"):
            self.status_lbl.setText("Running...")
            return
        if body.startswith("agent reached turn limit"):
            self._set_agent_status(self._active_agent, "Turn limit reached", body)
            return
        if body.startswith("message: ") and " -> " in body and ": " in body[9:]:
            self._record_meeting_message(body)
            return
        for name in list(self._agent_states):
            thought_prefix = f"{name} thought: "
            tool_prefix = f"{name} tool call: "
            final_prefix = f"{name} returned final response"
            if body.startswith(thought_prefix):
                thought = body[len(thought_prefix):].strip()
                self._agent_states[name]["thought"] = thought
                self._agent_states[name]["objective"] = self._objective_from_thought(thought)
                self._set_agent_status(name, "Thinking", "Thought: " + thought)
                return
            if body.startswith(tool_prefix):
                tool = body[len(tool_prefix):].strip()
                self._agent_states[name]["tool"] = tool
                self._set_agent_status(name, f"Using {tool}", body)
                return
            if body.startswith(final_prefix):
                self._set_agent_status(name, "Done", body)
                return
        if body.startswith("tool "):
            self._append_agent_history(self._active_agent, body)

    def _record_meeting_message(self, body: str) -> None:
        payload = body[len("message: "):]
        route, message = payload.split(": ", 1)
        source, target = route.split(" -> ", 1)
        item = {
            "from": source.strip(),
            "to": target.strip(),
            "message": message.strip(),
        }
        if any(
            existing.get("from") == item["from"]
            and existing.get("to") == item["to"]
            and existing.get("message") == item["message"]
            for existing in self._meeting_messages[-6:]
        ):
            return
        self._meeting_messages.append(item)
        del self._meeting_messages[:-8]
        if item["from"] in self._agent_states:
            self._append_agent_history(item["from"], f"Told {item['to']}: {item['message']}")
        if item["to"] in self._agent_states:
            self._append_agent_history(item["to"], f"Heard from {item['from']}: {item['message']}")
        self._refresh_shared_board()

    def _ensure_agent_state(self, name: str) -> None:
        if name in self._agent_states:
            return
        self._agent_names.append(name)
        self._agent_states[name] = {
            "role": self._agent_roles.get(name, "Agent"),
            "status": "Waiting",
            "thought": "",
            "objective": "",
            "tool": "",
            "health": {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
            "history": [],
        }

    def _set_agent_status(self, name: str, status: str, event: str) -> None:
        self._ensure_agent_state(name)
        self._agent_states[name]["status"] = status
        self._append_agent_history(name, event)

    def _agent_health(self, name: str) -> dict:
        self._ensure_agent_state(name)
        return self._agent_states[name].setdefault(
            "health",
            {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
        )

    def _record_model_latency(self, name: str, body: str) -> None:
        health = self._agent_health(name)
        marker = " received in "
        if marker not in body:
            return
        try:
            seconds = float(body.split(marker, 1)[1].split("s", 1)[0])
        except ValueError:
            return
        health["calls"] = int(health.get("calls", 0)) + 1
        health["total_latency"] = float(health.get("total_latency", 0.0)) + seconds

    @staticmethod
    def _objective_from_thought(thought: str) -> str:
        clean = " ".join(thought.split())
        for prefix in ("I need to ", "I will ", "I'll ", "Need to "):
            if clean.startswith(prefix):
                return AgentRunWindow._shorten(clean, 120)
        return AgentRunWindow._shorten(clean, 120)

    def _append_agent_history(self, name: str, event: str) -> None:
        self._ensure_agent_state(name)
        history = self._agent_states[name]["history"]
        history.append(event)
        del history[:-40]

    def _draw_live_meeting(self) -> None:
        self.meeting_scene.clear()
        self.meeting_scene.setSceneRect(0, 0, 1080, 560)
        bg = QPainterPath()
        bg.addRoundedRect(10, 10, 1060, 540, 16, 16)
        self.meeting_scene.addPath(bg, QPen(QColor("#cfd9e6"), 1), QBrush(QColor("#edf3fa")))

        table = QPainterPath()
        table.addRoundedRect(445, 230, 190, 100, 24, 24)
        self.meeting_scene.addPath(table, QPen(QColor("#9fb2c8"), 1.5), QBrush(QColor("#dbe6f2")))
        title = QGraphicsTextItem("Agent Meeting")
        title.setDefaultTextColor(QColor("#26384f"))
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title.setTextWidth(150)
        title.setPos(465, 267)
        title.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.meeting_scene.addItem(title)

        positions = self._live_agent_positions(len(self._agent_names))
        centers: dict[str, tuple[float, float]] = {}
        for idx, name in enumerate(self._agent_names):
            state = self._agent_states.get(name, {})
            x, y = positions[idx]
            centers[name] = (x + _LiveAgentItem.WIDTH / 2, y + _LiveAgentItem.HEIGHT / 2)
            item = _LiveAgentItem(
                idx,
                self._select_live_agent,
                x,
                y,
                name,
                str(state.get("role") or "Agent"),
                str(state.get("status") or "Waiting"),
                self._shorten(str(state.get("objective") or ""), 96),
                self._health_badge(name),
                name == self._active_agent,
                name == self._selected_agent,
            )
            self.meeting_scene.addItem(item)
        self._draw_last_message_arrow(centers)
        self._refresh_agent_detail()

    def _draw_last_message_arrow(self, centers: dict[str, tuple[float, float]]) -> None:
        if not self._meeting_messages:
            return
        item = self._meeting_messages[-1]
        source = item.get("from", "")
        target = item.get("to", "")
        if source not in centers:
            return
        if target.upper() == "ALL":
            for name in self._agent_names:
                if name != source and name in centers:
                    self._draw_live_arrow(centers[source], centers[name])
            return
        if target not in centers:
            return
        self._draw_live_arrow(centers[source], centers[target])

    def _draw_live_arrow(self, source: tuple[float, float], target: tuple[float, float]) -> None:
        sx, sy = source
        tx, ty = target
        sx_edge, sy_edge, tx_edge, ty_edge = self._live_edge_points(sx, sy, tx, ty)
        pen = QPen(QColor("#2f80ed"), 2.3)
        self.meeting_scene.addLine(sx_edge, sy_edge, tx_edge, ty_edge, pen)
        dx, dy = tx_edge - sx_edge, ty_edge - sy_edge
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        size = 11
        bx = tx_edge - ux * size
        by = ty_edge - uy * size
        path = QPainterPath()
        path.moveTo(tx_edge, ty_edge)
        path.lineTo(bx + px * size * 0.55, by + py * size * 0.55)
        path.lineTo(bx - px * size * 0.55, by - py * size * 0.55)
        path.closeSubpath()
        self.meeting_scene.addPath(path, QPen(QColor("#2f80ed")), QBrush(QColor("#2f80ed")))

    @staticmethod
    def _live_edge_points(sx: float, sy: float, tx: float, ty: float) -> tuple[float, float, float, float]:
        dx, dy = tx - sx, ty - sy
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        half_w, half_h = _LiveAgentItem.WIDTH / 2, _LiveAgentItem.HEIGHT / 2
        border_offset = min(
            half_w / max(abs(ux), 0.001),
            half_h / max(abs(uy), 0.001),
        )
        gap = 10.0
        return (
            sx + ux * (border_offset + gap),
            sy + uy * (border_offset + gap),
            tx - ux * (border_offset + gap),
            ty - uy * (border_offset + gap),
        )

    @staticmethod
    def _shorten(text: str, max_chars: int) -> str:
        clean = " ".join(text.split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max(0, max_chars - 1)].rstrip() + "..."

    @staticmethod
    def _live_agent_positions(count: int) -> list[tuple[float, float]]:
        if count <= 0:
            return []
        cx, cy = 540, 280
        rx, ry = 390, 200
        return [
            (
                cx + math.cos(-math.pi / 2 + 2 * math.pi * idx / count) * rx - _LiveAgentItem.WIDTH / 2,
                cy + math.sin(-math.pi / 2 + 2 * math.pi * idx / count) * ry - _LiveAgentItem.HEIGHT / 2,
            )
            for idx in range(count)
        ]

    def _select_live_agent(self, index: int) -> None:
        if 0 <= index < len(self._agent_names):
            self._selected_agent = self._agent_names[index]
            self._draw_live_meeting()

    def _refresh_agent_detail(self) -> None:
        name = self._selected_agent
        state = self._agent_states.get(name)
        if not state:
            self.agent_summary_view.clear()
            self.agent_activity_view.clear()
            return
        history = "\n".join(f"- {item}" for item in state.get("history", []))
        health = self._health_detail(name)
        summary = (
            f"<h3>{html.escape(name)}</h3>"
            f"<p><b>Role:</b> {html.escape(str(state.get('role') or 'Agent'))}<br>"
            f"<b>Status:</b> {html.escape(str(state.get('status') or 'Waiting'))}<br>"
            f"<b>Last tool:</b> {html.escape(str(state.get('tool') or 'None'))}</p>"
            f"<p><b>Current objective</b><br>{html.escape(str(state.get('objective') or 'No current objective.'))}</p>"
            f"<p><b>Model health</b><br>{html.escape(health)}</p>"
            f"<p><b>Latest thought</b><br>{html.escape(str(state.get('thought') or 'No thought yet.'))}</p>"
        )
        self._set_html_preserving_scroll(self.agent_summary_view, summary)
        self._set_plain_text_preserving_scroll(self.agent_activity_view, history or "- No activity yet.")

    def _health_badge(self, name: str) -> str:
        health = self._agent_health(name)
        calls = int(health.get("calls", 0))
        if not calls:
            avg = "-"
        else:
            avg = f"{float(health.get('total_latency', 0.0)) / calls:.1f}s"
        return (
            f"avg {avg} | invalid {int(health.get('invalid_json', 0))} | "
            f"repair {int(health.get('repairs', 0))} | fallback {int(health.get('fallbacks', 0))}"
        )

    def _health_detail(self, name: str) -> str:
        health = self._agent_health(name)
        calls = int(health.get("calls", 0))
        avg = 0.0 if not calls else float(health.get("total_latency", 0.0)) / calls
        return (
            f"calls {calls}, average latency {avg:.1f}s, "
            f"invalid JSON {int(health.get('invalid_json', 0))}, "
            f"repairs {int(health.get('repairs', 0))}, "
            f"fallbacks {int(health.get('fallbacks', 0))}"
        )

    def _refresh_shared_board(self) -> None:
        if not hasattr(self, "shared_board_view"):
            return
        if not self._meeting_messages:
            self._set_plain_text_preserving_scroll(self.shared_board_view, "No messages yet.")
        else:
            lines = []
            for item in self._meeting_messages:
                lines.append(f"{item['from']} -> {item['to']}")
                lines.append(item["message"])
                lines.append("")
            self._set_plain_text_preserving_scroll(self.shared_board_view, "\n".join(lines).strip())

    @staticmethod
    def _is_hidden_live_log_line(line: str) -> bool:
        return "] next agent:" in line

    def _format_live_log_line(self, line: str) -> str:
        stamp = ""
        body = line
        if line.startswith("[") and "] " in line:
            stamp, body = line.split("] ", 1)
            stamp += "] "

        for name, role in self._agent_roles.items():
            prefix = f"{name} "
            if body.startswith(prefix):
                label = self._agent_label(name, role)
                return f'<span style="color:#8f8f9e;">{html.escape(stamp)}</span>{label} {html.escape(body[len(prefix):])}'
            turn_marker = f": {name}"
            if body.startswith("agent turn ") and body.endswith(turn_marker):
                before = body[: -len(name)]
                return f'<span style="color:#8f8f9e;">{html.escape(stamp + before)}</span>{self._agent_label(name, role)}'

        return html.escape(line)

    @staticmethod
    def _agent_label(name: str, role: str) -> str:
        safe_name = html.escape(name)
        safe_role = html.escape(role)
        if safe_role and safe_role.lower() != safe_name.lower():
            return f'<b>{safe_name}</b> <span style="color:#8f8f9e;">({safe_role})</span>'
        return f"<b>{safe_name}</b>"

    def _on_finished(self, run_dir: str) -> None:
        self._run_dir = run_dir
        self.status_lbl.setText(f"Finished. Log: {run_dir}")
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.nudge_btn.setEnabled(False)
        self.open_result_btn.setEnabled(True)
        self.diff_btn.setEnabled((Path(run_dir) / "diff.patch").exists())
        self.retry_btn.setEnabled(True)
        self.continue_btn.setEnabled(True)
        self._load_finished_artifacts(Path(run_dir))

    def _load_finished_artifacts(self, run_dir: Path) -> None:
        trace_path = run_dir / "verbose.log"
        final_path = run_dir / "final.md"
        if trace_path.exists():
            self._set_plain_text_preserving_scroll(self.trace_view, trace_path.read_text(encoding="utf-8", errors="replace"))
        if final_path.exists():
            self._set_plain_text_preserving_scroll(self.final_view, final_path.read_text(encoding="utf-8", errors="replace"))

    def _show_approval(self, request: dict, state: object) -> None:
        details = request.get("details", {})
        detail_text = ", ".join(f"{k}={v}" for k, v in details.items())
        self._pending_approval = state
        self.approval_label.setText(f"Permission needed: {request.get('action')}\n{detail_text}")
        self.approval_panel.setStyleSheet(self._approval_style_alert)
        self.approval_panel.show()
        self.status_lbl.setText("Permission needed")
        if self._approval_notice_callback:
            notice = f"Permission needed: {request.get('action')}"
            if detail_text:
                notice += f"\n{detail_text}"
            notice += "\nApprove or decline in the Agent Task window."
            self._approval_notice_callback(notice, False)
        self.raise_()
        QApplication.alert(self, 0)

    def _finish_approval(self, approved: bool) -> None:
        if not self._pending_approval:
            return
        self._pending_approval["approved"] = approved
        self._pending_approval["event"].set()
        self._pending_approval = None
        self.approval_panel.setStyleSheet(self._approval_style_dark)
        self.approval_panel.hide()
        if self._approval_notice_callback:
            self._approval_notice_callback(
                "Permission approved." if approved else "Permission declined.",
                True,
            )

    def _toggle_pause(self) -> None:
        if self._control is None:
            return
        if self._control.is_pause_requested():
            self._control.resume()
            self.pause_btn.setText("Pause After Turn")
            self.status_lbl.setText("Running...")
        else:
            self._control.pause_after_turn()
            self.pause_btn.setText("Resume")
            self.status_lbl.setText("Will pause after current turn")

    def _send_manual_nudge(self) -> None:
        if self._control is None:
            return
        dialog = AgentNudgeDialog(self._agent_names + ["ALL"], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.nudge:
            return
        target = dialog.nudge["to"]
        message = dialog.nudge["message"]
        self._control.add_nudge(target, message)
        self.status_lbl.setText(f"Nudge queued for {target}")
        self._record_meeting_message(f"message: User -> {target}: {message.replace(chr(10), ' ')}")
        self._draw_live_meeting()

    def _cancel_run(self) -> None:
        if self._control is not None:
            self._control.cancel()
        self.status_lbl.setText("Cancelling...")
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.nudge_btn.setEnabled(False)

    def _open_result_folder(self) -> None:
        if self._run_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._run_dir))

    def _open_scope_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._spec.scope_folder))

    def _retry_run(self) -> None:
        launch_agent_run_window(
            self._spec,
            parent=None,
            approval_notice_callback=self._approval_notice_callback,
        )

    def _continue_run(self) -> None:
        if not self._run_dir:
            return
        try:
            spec = continue_spec_from_run(Path(self._run_dir))
        except Exception as exc:
            QMessageBox.warning(self, "Continue Run", str(exc))
            return
        launch_agent_run_window(
            spec,
            parent=None,
            approval_notice_callback=self._approval_notice_callback,
        )

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
        enable_standard_window_controls(self)
        layout = QVBoxLayout(self)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        viewer.setPlainText(diff_path.read_text(encoding="utf-8", errors="replace"))
        layout.addWidget(viewer)
        fit_window_to_screen(self, preferred_width=820, preferred_height=620)


class AgentRunHistoryWindow(QDialog):
    """Browse previous agent task runs without starting a new task."""

    def __init__(
        self,
        parent: QWidget | None = None,
        approval_notice_callback: ApprovalNoticeCallback | None = None,
    ):
        super().__init__(parent)
        self._approval_notice_callback = approval_notice_callback
        self._runs_root = Path(__file__).resolve().parents[1] / "memory" / "agent_runs"
        self._current_run: Path | None = None
        self.setWindowTitle("Agent Task History")
        self.setMinimumSize(820, 520)
        enable_standard_window_controls(self)

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
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
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
        open_result_btn = QPushButton("Open Result Folder")
        open_result_btn.clicked.connect(self._open_current_run)
        retry_btn = QPushButton("Retry")
        retry_btn.clicked.connect(self._retry_current_run)
        continue_btn = QPushButton("Continue")
        continue_btn.clicked.connect(self._continue_current_run)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        row.addStretch()
        row.addWidget(refresh_btn)
        row.addWidget(open_result_btn)
        row.addWidget(retry_btn)
        row.addWidget(continue_btn)
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

    def _retry_current_run(self) -> None:
        if not self._current_run:
            return
        try:
            spec = retry_spec_from_run(self._current_run)
        except Exception as exc:
            QMessageBox.warning(self, "Retry Run", str(exc))
            return
        launch_agent_run_window(
            spec,
            parent=None,
            approval_notice_callback=self._approval_notice_callback,
        )

    def _continue_current_run(self) -> None:
        if not self._current_run:
            return
        try:
            spec = continue_spec_from_run(self._current_run)
        except Exception as exc:
            QMessageBox.warning(self, "Continue Run", str(exc))
            return
        launch_agent_run_window(
            spec,
            parent=None,
            approval_notice_callback=self._approval_notice_callback,
        )

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
