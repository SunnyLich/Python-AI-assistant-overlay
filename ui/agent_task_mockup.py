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

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
_agent_history_windows: list["AgentRunHistoryWindow"] = []
_diff_windows: list["DiffViewer"] = []


@dataclass(frozen=True)
class AgentTaskSpec:
    """Serializable mock contract between the tray GUI and a future runner."""

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


def resolve_scope_folder(raw_folder: str) -> Path:
    """
    Resolve and validate the folder that a future agent may manipulate.

    This is the hard boundary candidate for the runner.  Any file operation in
    the eventual implementation should be checked with ``is_inside_scope``.
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
    action.triggered.connect(lambda: open_agent_task_dialog(parent or owner, on_submit))
    return action


def make_agent_history_action(owner: QWidget, parent: QWidget | None = None) -> QAction:
    """Create the tray QAction for browsing previous agent runs."""
    action = QAction("Agent task history...", owner)
    action.triggered.connect(lambda: open_agent_history(parent or owner))
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
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.task_spec
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

        self.setWindowTitle("Start Agent Task")
        self.setMinimumSize(560, 420)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

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
            window = AgentRunWindow(spec, parent=self.parentWidget())
            _agent_run_windows.append(window)
            window.destroyed.connect(lambda _obj=None, w=window: _agent_run_windows.remove(w) if w in _agent_run_windows else None)
            window.show()
        self.accept()

    # ------------------------------------------------------------------ Spec

    def _collect_spec(self) -> AgentTaskSpec:
        title = self.title_edit.text().strip()
        objective = self.objective_edit.toPlainText().strip()
        if not title:
            raise ValueError("Add a task title.")
        if not objective:
            raise ValueError("Describe the task objective.")
        model = self.model_combo.currentText().strip()
        if not model:
            raise ValueError("Add a model name.")

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
        )

    @staticmethod
    def _split_globs(raw: str) -> list[str]:
        return [part.strip() for part in raw.split(",") if part.strip()]

    @staticmethod
    def _format_spec(spec: AgentTaskSpec) -> str:
        lines: list[str] = []
        for key, value in asdict(spec).items():
            if isinstance(value, list):
                value = ", ".join(value) if value else "(none)"
            lines.append(f"{key}: {value}")
        return "\n".join(lines)


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
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

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
            viewer = DiffViewer(path, parent=self)
            _diff_windows.append(viewer)
            viewer.destroyed.connect(lambda _obj=None, w=viewer: _diff_windows.remove(w) if w in _diff_windows else None)
            viewer.show()


class DiffViewer(QDialog):
    def __init__(self, diff_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Agent Diff")
        self.setMinimumSize(760, 520)
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
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

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
