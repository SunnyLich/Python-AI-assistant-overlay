"""wisp-ui worker: the only process allowed to own PySide6 widgets."""

from __future__ import annotations

import json
import itertools
import logging
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from macos_py.bootstrap import configure_paths
from macos_py.boundaries import boundary_status
from macos_py import VERSION, protocol

log = logging.getLogger("wisp.ui_host")


def _ui_log_dir() -> Path:
    configured = os.environ.get("WISP_RUN_LOG_DIR")
    if configured:
        root = Path(configured)
    else:
        root = configure_paths() / "build_logs" / "ui_runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _format_thread_stacks() -> str:
    frames = sys._current_frames()
    parts: list[str] = []
    for thread in threading.enumerate():
        parts.append(f"\n--- thread={thread.name} ident={thread.ident} daemon={thread.daemon} ---\n")
        frame = frames.get(thread.ident or -1)
        if frame is None:
            parts.append("(no frame)\n")
            continue
        parts.extend(traceback.format_stack(frame))
    return "".join(parts)


class QtFreezeWatchdog:
    """Background detector for a blocked Qt event loop.

    A QTimer updates the heartbeat on the main thread. If the heartbeat stops,
    the watchdog thread writes thread stacks to disk, which gives freeze reports
    something more useful than "it hung."
    """

    def __init__(self, app, status_fn) -> None:
        from PySide6.QtCore import QTimer

        self._status_fn = status_fn
        self._threshold = float(os.environ.get("WISP_UI_FREEZE_THRESHOLD_SECONDS", "2.5"))
        self._interval = float(os.environ.get("WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS", "0.5"))
        self._cooldown = max(self._threshold, float(os.environ.get("WISP_UI_FREEZE_LOG_COOLDOWN_SECONDS", "10.0")))
        self._last_beat = time.monotonic()
        self._last_log = 0.0
        self._last_drain_ticks = -1
        self._drain_progress = time.monotonic()
        self._last_ipc_log = 0.0
        self._stop = threading.Event()
        self._timer = QTimer()
        self._timer.setInterval(max(50, int(self._interval * 1000)))
        self._timer.timeout.connect(self.beat)
        self._timer.start()
        app.aboutToQuit.connect(self.stop)
        self._thread = threading.Thread(target=self._run, name="wisp-ui-freeze-watchdog", daemon=True)
        self._thread.start()

    def beat(self) -> None:
        self._last_beat = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        self._timer.stop()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            now = time.monotonic()
            stalled_for = now - self._last_beat
            status = self._status_fn() or {}

            # Track whether the IPC pump (_drain) is still advancing.
            ticks = int(status.get("drain_ticks", 0) or 0)
            if ticks != self._last_drain_ticks:
                self._last_drain_ticks = ticks
                self._drain_progress = now

            # Case 1: the Qt event loop itself is frozen (heartbeat stopped).
            if stalled_for >= self._threshold:
                if now - self._last_log >= self._cooldown:
                    self._last_log = now
                    self._write_report("ui_freeze", "event_loop_frozen", stalled_for, status)
                continue

            # Case 2 (the blind spot): the event loop is alive but the IPC pump
            # has stopped draining while requests are queued — exactly the state
            # a 'ui.* timed out' with no freeze log points to.
            drain_stalled = now - self._drain_progress
            if (
                int(status.get("queue_depth", 0) or 0) > 0
                and drain_stalled >= self._threshold
                and now - self._last_ipc_log >= self._cooldown
            ):
                self._last_ipc_log = now
                self._write_report("ui_ipc_stall", "ipc_pump_stalled", drain_stalled, status)

    def _write_report(self, prefix: str, kind: str, stalled_for: float, status: dict) -> None:
        try:
            path = _ui_log_dir() / f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}.log"
            body = [
                f"time={time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                f"kind={kind}\n",
                f"pid={os.getpid()}\n",
                f"stalled_for_seconds={stalled_for:.3f}\n",
                f"active_method={status.get('method') or ''}\n",
                f"active_for_seconds={status.get('active_for_seconds') or 0:.3f}\n",
                f"queue_depth={status.get('queue_depth') or 0}\n",
                f"drain_ticks={status.get('drain_ticks') or 0}\n",
                "\nThread stacks:\n",
                _format_thread_stacks(),
            ]
            path.write_text("".join(body), encoding="utf-8")
            print(f"[wisp-ui] watchdog wrote {path}", file=sys.stderr, flush=True)
        except Exception:
            log.exception("failed writing UI watchdog log")


class MemoryProxy:
    """Small UI-side cache that forwards memory mutations to the supervisor."""

    def __init__(self, emit_fn) -> None:
        self._emit = emit_fn
        self._facts: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def replace_facts(self, facts: list[dict[str, Any]] | None) -> None:
        self._facts = [dict(f) for f in (facts or [])]

    def get_all_facts(self) -> list[dict[str, Any]]:
        return [dict(f) for f in self._facts]

    def add_fact_manual(self, text: str, category: str = "general") -> None:
        fact = {
            "id": f"pending-{next(self._ids)}",
            "text": text,
            "category": category or "general",
            "source": "manual",
        }
        self._facts.append(fact)
        self._emit("ui.memory.add", {"text": text, "category": category})

    def update_fact(self, fact_id: str, text: str, category: str | None = None) -> None:
        for fact in self._facts:
            if str(fact.get("id")) == str(fact_id):
                fact["text"] = text
                if category is not None:
                    fact["category"] = category
                break
        self._emit("ui.memory.update", {"id": fact_id, "text": text, "category": category})

    def delete_fact(self, fact_id: str) -> None:
        self._facts = [f for f in self._facts if str(f.get("id")) != str(fact_id)]
        self._emit("ui.memory.delete", {"id": fact_id})


class MacAgentRunDialog:
    """Protocol-backed agent run window for the pure-Python target."""

    def __init__(self, host: "QtProtocolHost", spec: dict[str, Any]) -> None:
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices, QTextCursor
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
        )

        self._host = host
        self._spec = dict(spec or {})
        self._run_dir = ""
        self._pending_approval_id = ""
        self._QApplication = QApplication
        self._QDesktopServices = QDesktopServices
        self._QTextCursor = QTextCursor
        self._QUrl = QUrl

        title = str(self._spec.get("title") or "Agent Task")
        self.dialog = QDialog()
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialog.setWindowTitle(f"Agent Task - {title}")
        self.dialog.setMinimumSize(820, 560)

        root = QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title_label = QLabel(f"<b>{title}</b>")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title_label)

        self.approval_panel = QFrame()
        self.approval_panel.setStyleSheet(
            "QFrame { background: #fff3d6; border: 1px solid #f59e0b; border-radius: 6px; }"
            "QLabel { color: #3a2500; background: transparent; font-weight: 600; }"
        )
        approval_row = QHBoxLayout(self.approval_panel)
        approval_row.setContentsMargins(10, 8, 10, 8)
        self.approval_label = QLabel()
        self.approval_label.setWordWrap(True)
        approve_btn = QPushButton("Approve")
        decline_btn = QPushButton("Decline")
        approve_btn.clicked.connect(lambda: self._resolve_approval(True))
        decline_btn.clicked.connect(lambda: self._resolve_approval(False))
        approval_row.addWidget(self.approval_label, 1)
        approval_row.addWidget(approve_btn)
        approval_row.addWidget(decline_btn)
        self.approval_panel.hide()
        root.addWidget(self.approval_panel)

        self.tabs = QTabWidget()
        self.log_view = QTextEdit()
        self.trace_view = QTextEdit()
        self.final_view = QTextEdit()
        for view in (self.log_view, self.trace_view, self.final_view):
            view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.final_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.tabs.addTab(self.log_view, "Live Log")
        self.tabs.addTab(self.trace_view, "Model Trace")
        self.tabs.addTab(self.final_view, "Final Report")
        root.addWidget(self.tabs, 1)

        row = QHBoxLayout()
        self.status_label = QLabel("Running...")
        self.open_result_btn = QPushButton("Open Run Folder")
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
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_run)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.dialog.close)
        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.open_result_btn)
        row.addWidget(self.open_scope_btn)
        row.addWidget(self.retry_btn)
        row.addWidget(self.continue_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(close_btn)
        root.addLayout(row)

    def show(self) -> None:
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def close(self) -> None:
        self.dialog.close()

    def append_log(self, data: dict[str, Any]) -> None:
        payload = self._payload(data)
        line = str(payload.get("line") or payload.get("text") or payload.get("data") or "")
        if line:
            self._append_text(self.log_view, line)
            self.status_label.setText("Running...")

    def append_trace(self, data: dict[str, Any]) -> None:
        payload = self._payload(data)
        entry = str(payload.get("entry") or payload.get("text") or payload.get("data") or "")
        if entry:
            self._append_text(self.trace_view, entry)

    def finish(self, data: dict[str, Any]) -> None:
        payload = self._payload(data)
        self._run_dir = str(payload.get("run_dir") or self._run_dir or "")
        final_text = str(payload.get("final") or "")
        error_text = str(payload.get("error") or "")
        if final_text:
            self.final_view.setPlainText(final_text)
        elif error_text:
            self.final_view.setPlainText(error_text)
        elif payload.get("cancelled"):
            self.final_view.setPlainText("Agent task cancelled.")
        else:
            self.final_view.setPlainText("(no final report)")

        if error_text:
            self.status_label.setText("Failed")
        elif payload.get("cancelled"):
            self.status_label.setText("Cancelled")
        else:
            self.status_label.setText("Finished")
        self.approval_panel.hide()
        self.cancel_btn.setEnabled(False)
        self.open_result_btn.setEnabled(bool(self._run_dir))
        self.retry_btn.setEnabled(True)
        self.continue_btn.setEnabled(bool(self._run_dir))

    def request_approval(self, data: dict[str, Any]) -> None:
        payload = self._payload(data)
        self._pending_approval_id = str(payload.get("approval_id") or "")
        action = str(payload.get("action") or "approval")
        details = payload.get("details")
        detail_text = ""
        if isinstance(details, dict):
            detail_text = ", ".join(f"{key}={value}" for key, value in details.items())
        if not detail_text:
            detail_text = str(payload.get("detail") or payload.get("reason") or "")
        text = f"Permission needed: {action}"
        if detail_text:
            text += f"\n{detail_text}"
        self.approval_label.setText(text)
        self.approval_panel.show()
        self.status_label.setText("Permission needed")
        self._host._agent_notify_approval(
            text + "\nApprove or decline in the Agent Task window.",
            resolved=False,
            data=payload,
        )
        self.dialog.raise_()
        self._QApplication.alert(self.dialog, 0)

    @staticmethod
    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data, dict) and isinstance(data.get("data"), dict) and len(data) == 1:
            return data["data"]
        return data if isinstance(data, dict) else {"data": data}

    def _append_text(self, view, text: str) -> None:
        if not text:
            return
        cursor = view.textCursor()
        cursor.movePosition(self._QTextCursor.MoveOperation.End)
        if view.toPlainText():
            cursor.insertText("\n")
        cursor.insertText(text.rstrip("\n"))
        bar = view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _resolve_approval(self, approved: bool) -> None:
        if not self._pending_approval_id:
            return
        approval_id = self._pending_approval_id
        self._pending_approval_id = ""
        self.approval_panel.hide()
        self._host.emit(
            "ui.agent.approval.respond",
            {"approval_id": approval_id, "approved": bool(approved)},
        )
        self._host._agent_notify_approval(
            "Permission approved." if approved else "Permission declined.",
            resolved=True,
            data={"approval_id": approval_id, "approved": bool(approved)},
        )

    def _cancel_run(self) -> None:
        self.status_label.setText("Cancelling...")
        self.cancel_btn.setEnabled(False)
        self._host.emit("ui.agent.cancel_requested", {})

    def _retry_run(self) -> None:
        self._host._start_agent_run(self._spec)

    def _continue_run(self) -> None:
        if self._run_dir:
            self._host.emit("ui.agent.history.continue", {"run_dir": self._run_dir})

    def _open_result_folder(self) -> None:
        if self._run_dir:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._run_dir))

    def _open_scope_folder(self) -> None:
        scope = str(self._spec.get("scope_folder") or "")
        if scope:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(scope))


class MacAgentHistoryDialog:
    """Protocol-backed agent history browser for the pure-Python target."""

    def __init__(self, host: "QtProtocolHost") -> None:
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QSplitter,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
        )

        self._host = host
        self._QDesktopServices = QDesktopServices
        self._QListWidgetItem = QListWidgetItem
        self._Qt = Qt
        self._QUrl = QUrl
        self._loading = False
        self._current_run_dir = ""
        self._runs_root = ""

        self.dialog = QDialog()
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialog.setWindowTitle("Agent Task History")
        self.dialog.setMinimumSize(820, 520)

        root = QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.run_list = QListWidget()
        self.run_list.currentItemChanged.connect(self._selected_run_changed)
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
        root.addWidget(splitter, 1)

        row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(lambda: self._host.emit("ui.agent.history.refresh", {}))
        open_btn = QPushButton("Open Run Folder")
        open_btn.clicked.connect(self._open_current_run)
        retry_btn = QPushButton("Retry")
        retry_btn.clicked.connect(lambda: self._emit_current("ui.agent.history.retry"))
        continue_btn = QPushButton("Continue")
        continue_btn.clicked.connect(lambda: self._emit_current("ui.agent.history.continue"))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.dialog.close)
        row.addStretch()
        row.addWidget(refresh_btn)
        row.addWidget(open_btn)
        row.addWidget(retry_btn)
        row.addWidget(continue_btn)
        row.addWidget(close_btn)
        root.addLayout(row)

    def show(self) -> None:
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def replace_runs(self, runs: list[dict[str, Any]] | None, runs_root: str = "") -> None:
        self._runs_root = str(runs_root or "")
        self._loading = True
        self.run_list.clear()
        for run in runs or []:
            if not isinstance(run, dict):
                continue
            run_dir = str(run.get("run_dir") or "")
            if not run_dir:
                continue
            item = self._QListWidgetItem(self._display_name(run))
            item.setData(self._Qt.ItemDataRole.UserRole, run_dir)
            self.run_list.addItem(item)
        self._loading = False
        if self.run_list.count():
            self.run_list.setCurrentRow(0)
        else:
            self._current_run_dir = ""
            self._clear_views("No agent task runs yet.")

    def update_detail(self, data: dict[str, Any]) -> None:
        payload = data if isinstance(data, dict) else {"error": str(data)}
        if isinstance(payload.get("data"), dict) and len(payload) == 1:
            payload = payload["data"]
        run_dir = str(payload.get("run_dir") or self._current_run_dir or "")
        if run_dir:
            self._current_run_dir = run_dir
        error = str(payload.get("error") or "")
        final = str(payload.get("final") or "")
        task_json = str(payload.get("task_json") or "")
        if error and not final:
            summary = f"Run folder:\n{run_dir}\n\nError:\n{error}"
        else:
            summary = (
                f"Run folder:\n{run_dir}\n\n"
                f"Final report:\n{final or '(no final report)'}\n\n"
                f"Task spec:\n{task_json or '(missing task.json)'}"
            )
            if error:
                summary += f"\n\nError:\n{error}"
        self.summary_view.setPlainText(summary)
        self.log_view.setPlainText(str(payload.get("run_log") or ""))
        self.trace_view.setPlainText(str(payload.get("verbose_log") or ""))
        self.diff_view.setPlainText(str(payload.get("diff_patch") or "(no diff artifact)"))

    def _selected_run_changed(self, current, _previous) -> None:
        if self._loading or current is None:
            return
        run_dir = str(current.data(self._Qt.ItemDataRole.UserRole) or "")
        self._current_run_dir = run_dir
        if run_dir:
            self._host.emit("ui.agent.history.read", {"run_dir": run_dir})

    def _emit_current(self, event: str) -> None:
        if self._current_run_dir:
            self._host.emit(event, {"run_dir": self._current_run_dir})

    def _open_current_run(self) -> None:
        if self._current_run_dir:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._current_run_dir))
        elif self._runs_root:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._runs_root))

    def _clear_views(self, text: str) -> None:
        for view in (self.summary_view, self.log_view, self.trace_view, self.diff_view):
            view.setPlainText(text)

    @staticmethod
    def _display_name(run: dict[str, Any]) -> str:
        status = str(run.get("status") or "unknown")
        modified = str(run.get("modified_display") or "")
        title = str(run.get("title") or run.get("id") or "Agent run")
        suffix = f" - {modified}" if modified else ""
        return f"{title} [{status}]{suffix}"


def _protect_stdout():
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


class QtProtocolHost:
    def __init__(self, app, out) -> None:
        from PySide6.QtCore import QTimer

        self._app = app
        self._out = out
        self._write_lock = threading.Lock()
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._closing = False

        self._overlay_signals = None
        self._overlay = None
        self._intent = None
        self._snip = None
        self._bubble = None
        self._chat = None
        self._memory = None
        self._memory_viewer = None
        self._plugins_dialog = None
        self._agent_run_dialog: MacAgentRunDialog | None = None
        self._agent_history_dialog: MacAgentHistoryDialog | None = None
        self._all_conversations: list[dict] = []
        self._chat_request_ids = itertools.count(1)
        self._chat_streams: dict[str, "queue.Queue[tuple[str, Any]]"] = {}
        self._chat_streams_lock = threading.Lock()
        self._active_dispatch_method = ""
        self._active_dispatch_started = 0.0
        self._drain_ticks = 0  # bumped each pump tick so the watchdog can tell the IPC drain apart from an event-loop freeze
        self._watchdog = QtFreezeWatchdog(app, self._watchdog_status)

        self._pump = QTimer()
        self._pump.setInterval(20)
        self._pump.timeout.connect(self._drain)
        self._pump.start()

        self._reader = threading.Thread(target=self._read_loop, name="wisp-ui-stdin", daemon=True)
        self._reader.start()

    def _send(self, obj: dict[str, Any]) -> None:
        with self._write_lock:
            protocol.write_message(self._out, obj)

    def emit(self, event: str, data: Any = None, req_id: Any = None) -> None:
        self._send(protocol.make_event(event, data=data, req_id=req_id))

    def _respond(self, req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> None:
        self._send(protocol.make_response(req_id, ok, result=result, error=error))

    def _read_loop(self) -> None:
        stream = sys.stdin.buffer
        while True:
            line = stream.readline()
            if not line:
                self._lines.put(None)
                return
            self._lines.put(line)

    def _drain(self) -> None:
        self._drain_ticks += 1  # proof the IPC pump fired this tick
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                self._closing = True
                self._pump.stop()
                self._app.quit()
                return
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        req_id = None
        try:
            msg = json.loads(raw.decode("utf-8"))
            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "__shutdown__":
                self._respond(req_id, True, result=None)
                self._closing = True
                self._pump.stop()
                self._app.quit()
                return
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            method_name = str(method)
            self._active_dispatch_method = method_name
            self._active_dispatch_started = time.monotonic()
            try:
                result = self._dispatch(method_name, params)
            finally:
                elapsed = time.monotonic() - self._active_dispatch_started
                slow_threshold = float(os.environ.get("WISP_UI_SLOW_DISPATCH_SECONDS", "1.0"))
                if elapsed >= slow_threshold:
                    self._write_slow_dispatch_log(method_name, elapsed)
                self._active_dispatch_method = ""
                self._active_dispatch_started = 0.0
            self._respond(req_id, True, result=result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._respond(req_id, False, error=f"{type(exc).__name__}: {exc}")

    def _watchdog_status(self) -> dict[str, Any]:
        started = self._active_dispatch_started
        return {
            "method": self._active_dispatch_method,
            "active_for_seconds": max(0.0, time.monotonic() - started) if started else 0.0,
            "drain_ticks": self._drain_ticks,
            "queue_depth": self._lines.qsize(),
        }

    def _write_slow_dispatch_log(self, method: str, elapsed: float) -> None:
        try:
            path = _ui_log_dir() / f"ui_slow_dispatch_{time.strftime('%Y%m%d-%H%M%S')}.log"
            path.write_text(
                "".join(
                    [
                        f"time={time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                        f"pid={os.getpid()}\n",
                        f"method={method}\n",
                        f"elapsed_seconds={elapsed:.3f}\n",
                        "\nThread stacks:\n",
                        _format_thread_stacks(),
                    ]
                ),
                encoding="utf-8",
            )
            print(f"[wisp-ui] slow dispatch wrote {path}", file=sys.stderr, flush=True)
        except Exception:
            log.exception("failed writing UI slow-dispatch log")

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method in {"ping", "ui.ping"}:
            return {
                "pong": True,
                "pid": os.getpid(),
                "role": "ui",
                "version": VERSION,
                "repo_root": str(configure_paths()),
                "boundary": boundary_status("ui"),
            }
        if method == "boundary.status":
            return boundary_status("ui")
        if method == "ui.debug.block_event_loop" and os.environ.get("WISP_UI_DEBUG_METHODS"):
            seconds = max(0.0, min(10.0, float(params.get("seconds") or 0.0)))
            time.sleep(seconds)
            return {"blocked_seconds": seconds}
        if method == "ui.reload_config":
            return self._reload_config()
        if method == "ui.show_overlay":
            return self._show_overlay()
        if method == "ui.show_intent":
            return self._show_intent(**params)
        if method == "ui.show_snip":
            return self._show_snip()
        if method == "ui.overlay.state":
            return self._overlay_state(**params)
        if method == "ui.reply.reset":
            return self._reply_reset()
        if method == "ui.reply.thinking":
            return self._reply_thinking()
        if method == "ui.reply.listening":
            return self._reply_listening()
        if method == "ui.reply.start_reveal":
            return self._reply_start_reveal()
        if method == "ui.reply.schedule_words":
            return self._reply_schedule_words(**params)
        if method == "ui.reply.notice":
            return self._reply_notice(**params)
        if method == "ui.reply.chunk":
            return self._reply_chunk(**params)
        if method == "ui.reply.done":
            return self._reply_done(**params)
        if method == "ui.context.clear":
            return self._context_clear()
        if method == "ui.context.add_item":
            return self._context_add_item(**params)
        if method == "ui.context.summary":
            return self._context_summary(**params)
        if method == "ui.chat.chunk":
            return self._chat_chunk(**params)
        if method == "ui.chat.done":
            return self._chat_done(**params)
        if method == "ui.chat.error":
            return self._chat_error(**params)
        if method == "ui.chat.add_conversation":
            return self._chat_add_conversation(**params)
        if method == "ui.chat.ingest":
            return self._chat_ingest()
        if method == "ui.show_chat":
            return self._show_chat(force_new=bool(params.get("new", False)))
        if method == "ui.show_settings":
            return self._show_settings()
        if method == "ui.show_memory":
            return self._show_memory(**params)
        if method == "ui.show_plugins":
            return self._show_plugins(**params)
        if method == "ui.show_agent_task":
            return self._show_agent_task(**params)
        if method == "ui.show_agent_history":
            return self._show_agent_history(**params)
        if method == "ui.agent.notify_approval":
            return self._agent_notify_approval(**params)
        if method == "ui.agent.log":
            return self._agent_log(**params)
        if method == "ui.agent.trace":
            return self._agent_trace(**params)
        if method == "ui.agent.done":
            return self._agent_done(**params)
        if method == "ui.agent.approval.request":
            return self._agent_approval_request(**params)
        if method == "ui.agent.history.detail":
            return self._agent_history_detail(**params)
        raise ValueError(f"unknown method: {method}")

    def _reload_config(self) -> dict[str, Any]:
        import config

        config.reload()
        try:
            if self._overlay is not None:
                self._overlay.apply_settings()
        except Exception:
            traceback.print_exc()
        try:
            from ui.shared.theme import apply_app_theme

            apply_app_theme(self._app)
        except Exception:
            traceback.print_exc()
        return {"ok": True}

    def _ensure_overlay(self):
        if self._overlay is None:
            from ui.overlay import IconOverlay, OverlaySignals

            self._overlay_signals = OverlaySignals()
            self._overlay = IconOverlay(self._overlay_signals)
            # Keep audio ownership out of the UI process. Bubble hold gestures
            # and hide/reset callbacks are routed over protocol to the supervisor.
            try:
                self._overlay._bubble.set_speed_callback(
                    lambda enabled: self.emit("ui.bubble.speed", {"enabled": bool(enabled)})
                )
            except Exception:
                traceback.print_exc()
            self._overlay_signals.bubble_speed.connect(
                lambda enabled: self.emit("ui.bubble.speed", {"enabled": bool(enabled)})
            )
            self._overlay_signals.summon_caller.connect(
                lambda idx: self.emit("ui.summon_caller", {"caller_idx": int(idx)})
            )
            self._overlay_signals.show_snip_overlay.connect(
                lambda: self.emit("ui.request_snip", {})
            )
            self._overlay_signals.show_new_chat.connect(lambda: self._show_chat(force_new=True))
            self._overlay_signals.show_last_chat.connect(lambda: self._show_chat(force_new=False))
            self._overlay_signals.show_memory_viewer.connect(
                lambda: self.emit("ui.memory.open_requested", {})
            )
            self._overlay_signals.show_plugin_manager.connect(
                lambda: self.emit("ui.plugins.open_requested", {})
            )
            self._overlay_signals.show_agent_task.connect(
                lambda: self.emit("ui.agent.task_requested", {})
            )
            self._overlay_signals.show_agent_history.connect(
                lambda: self.emit("ui.agent.history_requested", {})
            )
            self._overlay_signals.context_items_dropped.connect(self._context_items_dropped)
            self._overlay_signals.remove_dropped_item.connect(
                lambda idx: self.emit("ui.context.remove", {"index": int(idx)})
            )
            self._overlay_signals.bubble_highlight.connect(self._bubble_highlight)
            self._overlay_signals.settings_applied.connect(self._settings_applied)
        return self._overlay

    def _settings_applied(self) -> None:
        self._reload_config()
        self.emit("ui.settings.applied", {})

    def _ensure_bubble(self):
        if self._bubble is None:
            overlay = self._ensure_overlay()
            self._bubble = getattr(overlay, "_bubble", None)
            if self._bubble is None:
                from ui.bubble import SpeechBubble

                self._bubble = SpeechBubble()
        return self._bubble

    def _show_overlay(self) -> dict[str, Any]:
        overlay = self._ensure_overlay()
        overlay.show()
        overlay.raise_()
        return {"shown": True}

    def _show_intent(self, caller_idx: int = 0, target_hwnd: int = 0) -> dict[str, Any]:
        t0 = time.monotonic()
        from ui.intent_overlay import IntentOverlay
        t_import = time.monotonic()

        if self._intent is not None:
            self._intent.close()
            self._intent = None
        t_closed = time.monotonic()
        self._intent = IntentOverlay(caller_idx=caller_idx, target_hwnd=target_hwnd)
        t_built = time.monotonic()
        self._intent.intent_chosen.connect(
            lambda intent, custom: self.emit(
                "ui.intent.chosen",
                {"caller_idx": caller_idx, "intent": intent, "custom": custom},
            )
        )
        self._intent.cancelled.connect(lambda: self.emit("ui.intent.cancelled", {"caller_idx": caller_idx}))
        self._intent.destroyed.connect(lambda: setattr(self, "_intent", None))
        self._intent.show()
        self._intent.raise_()
        self._intent.activateWindow()
        print(
            "[ui.show_intent] "
            f"caller={caller_idx} target_hwnd={target_hwnd} "
            f"import={t_import - t0:.3f}s close={t_closed - t_import:.3f}s "
            f"build={t_built - t_closed:.3f}s show={time.monotonic() - t_built:.3f}s",
            flush=True,
        )
        return {"shown": True, "caller_idx": caller_idx}

    def _show_snip(self) -> dict[str, Any]:
        from ui.snip_overlay import SnipOverlay

        # Always present a FRESH selector (mirrors _show_intent). The old reuse
        # guard returned early when self._snip was non-None, but WA_DeleteOnClose
        # defers the destroyed callback that clears it -- so after the first snip
        # closed, the stale handle made the next press a silent no-op until the
        # old object was finally collected. That was the "overlay appears much
        # later" on repeat snips. Close any existing overlay and rebuild.
        t0 = time.monotonic()
        if self._snip is not None:
            try:
                self._snip.close()
            except Exception:
                pass
            self._snip = None
        t_closed = time.monotonic()
        snip = SnipOverlay()
        t_built = time.monotonic()
        self._snip = snip
        snip.region_selected.connect(lambda region: self.emit("ui.snip.region", region))
        snip.cancelled.connect(lambda: self.emit("ui.snip.cancelled", {}))
        # Identity-guard the clear so a previous overlay's deferred destroyed
        # signal can't null out a newer one's reference.
        snip.destroyed.connect(
            lambda *_: setattr(self, "_snip", None) if self._snip is snip else None
        )
        snip.show()
        snip.raise_()
        snip.activateWindow()
        t_shown = time.monotonic()
        print(
            f"[snip.timing] close_old={t_closed - t0:.2f}s build={t_built - t_closed:.2f}s "
            f"show={t_shown - t_built:.2f}s total={t_shown - t0:.2f}s",
            file=sys.stderr,
            flush=True,
        )
        return {"shown": True}

    def _overlay_state(self, state: str = "idle") -> dict[str, Any]:
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.set_state.emit(state)
        if state != "idle":
            overlay.show()
            overlay.raise_()
        return {"state": state}

    def _reply_reset(self) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.clear()
        return {"reset": True}

    def _reply_thinking(self) -> dict[str, Any]:
        self._ensure_bubble().start_thinking()
        return {"thinking": True}

    def _reply_listening(self) -> dict[str, Any]:
        self._ensure_bubble().show_listening()
        return {"listening": True}

    def _reply_start_reveal(self) -> dict[str, Any]:
        self._ensure_bubble().start_word_reveal()
        return {"started": True}

    def _reply_schedule_words(
        self, words: list | None = None, start_ms: list | None = None
    ) -> dict[str, Any]:
        """Buffer Cartesia word timestamps so the reveal tracks the spoken voice.

        Delivered before playback starts; the bubble holds them in
        ``_pre_audio_timestamps`` until ``start_word_reveal`` (fired by the
        audio.playback.started event) drains them anchored to the audio clock.
        """
        words = list(words or [])
        start_ms = [int(x) for x in (start_ms or [])]
        self._ensure_bubble().schedule_words(words, start_ms)
        return {"scheduled": len(words)}

    def _reply_notice(self, text: str = "", timeout_ms: int = 12000) -> dict[str, Any]:
        self._ensure_bubble().show_notice(text, timeout_ms=timeout_ms)
        return {"shown": True, "text": text}

    def _reply_chunk(self, text: str = "", is_thought: bool = False) -> dict[str, Any]:
        bubble = self._ensure_bubble()
        bubble.append_chunk(text, is_thought=is_thought)
        return {"appended": len(text or "")}

    def _reply_done(self, flush: bool = True) -> dict[str, Any]:
        """Finish the reply bubble.

        flush=True reveals everything immediately (TTS playback ended, errors).
        flush=False lets a running WPM reveal drain at the configured speed —
        used when TTS is off, so BUBBLE_REVEAL_WPM is honored instead of the
        whole reply slamming in the moment the LLM finishes streaming.
        """
        bubble = self._ensure_bubble()
        bubble.finish(flush_remaining=bool(flush))
        return {"done": True}

    @staticmethod
    def _context_item_payload(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return {
                "name": str(item.get("name") or item.get("label") or "Context"),
                "content": item.get("content", ""),
                "type": str(item.get("type") or item.get("item_type") or "text"),
            }
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            return {"name": str(item[0]), "content": item[1], "type": str(item[2])}
        return {"name": "Context", "content": str(item), "type": "text"}

    def _context_items_dropped(self, items: Any) -> None:
        payload = [self._context_item_payload(item) for item in (items or [])]
        self.emit("ui.context.dropped", {"items": payload})

    def _context_clear(self) -> dict[str, Any]:
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.drop_context_cleared.emit()
        return {"cleared": True, "overlay": bool(overlay)}

    def _context_add_item(self, name: str = "", item_type: str = "text") -> dict[str, Any]:
        self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.add_context_item.emit(
                str(name or "Context"), str(item_type or "text")
            )
        return {"added": True}

    def _context_summary(self, items: list | None = None) -> dict[str, Any]:
        self._ensure_overlay()
        if self._overlay_signals is not None:
            pairs = [
                (str(item.get("label") or item.get("name") or "Context"), str(item.get("type") or "text"))
                for item in (items or [])
                if isinstance(item, dict)
            ]
            self._overlay_signals.show_context_summary.emit(pairs)
        return {"shown": len(items or [])}

    def _bubble_highlight(self, text: str, revealed_count: int, finished: bool) -> None:
        if self._chat is not None:
            self._chat.update_live_highlight(text, int(revealed_count), bool(finished))
        self.emit(
            "ui.bubble.highlight",
            {"text": text, "revealed_count": int(revealed_count), "finished": bool(finished)},
        )

    def _memory_manager(self):
        if self._memory is None:
            self._memory = MemoryProxy(self.emit)
        return self._memory

    def _make_chat_send_fn(self):
        def send_with_memory(messages: list):
            request_id = f"chat-{next(self._chat_request_ids)}"
            stream: "queue.Queue[tuple[str, Any]]" = queue.Queue()
            with self._chat_streams_lock:
                self._chat_streams[request_id] = stream
            self.emit("ui.chat.request", {"request_id": request_id, "messages": messages})
            try:
                while True:
                    kind, payload = stream.get()
                    if kind == "chunk":
                        yield str(payload or "")
                    elif kind == "done":
                        return
                    elif kind == "error":
                        raise RuntimeError(str(payload or "chat failed"))
            finally:
                with self._chat_streams_lock:
                    self._chat_streams.pop(request_id, None)

        return send_with_memory

    def _chat_stream(self, request_id: str):
        with self._chat_streams_lock:
            return self._chat_streams.get(str(request_id))

    def _chat_chunk(self, request_id: str = "", text: str = "") -> dict[str, Any]:
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("chunk", text))
        return {"queued": stream is not None}

    def _chat_done(self, request_id: str = "") -> dict[str, Any]:
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("done", None))
        return {"queued": stream is not None}

    def _chat_error(self, request_id: str = "", error: str = "") -> dict[str, Any]:
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("error", error))
        return {"queued": stream is not None}

    def _chat_add_conversation(
        self,
        user: str = "",
        assistant: str = "",
        context: str = "",
        image_base64: str | None = None,
    ) -> dict[str, Any]:
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if image_base64:
            user_msg["image_base64"] = image_base64
        self._all_conversations.append(
            {
                "messages": [
                    user_msg,
                    {"role": "assistant", "content": assistant},
                ],
                "context": context or "",
            }
        )
        if self._chat is not None:
            self._chat.ingest_new_conversations()
        return {"count": len(self._all_conversations)}

    def _chat_ingest(self) -> dict[str, Any]:
        if self._chat is not None:
            self._chat.ingest_new_conversations()
            return {"ingested": True}
        return {"ingested": False}

    def _show_chat(self, force_new: bool = False) -> dict[str, Any]:
        from ui.chat_window import ChatWindow

        if self._chat is not None:
            if force_new:
                self._chat.start_new_conversation()
            self._chat.raise_()
            self._chat.activateWindow()
            return {"shown": True, "reused": True}
        start_new = force_new or not self._all_conversations
        self._chat = ChatWindow(
            conversations=self._all_conversations,
            send_fn=self._make_chat_send_fn(),
            start_new=start_new,
        )
        self._chat.destroyed.connect(lambda: setattr(self, "_chat", None))
        self._chat.show()
        self._chat.raise_()
        self._chat.activateWindow()
        return {"shown": True, "reused": False}

    def _show_settings(self) -> dict[str, Any]:
        from PySide6.QtCore import QTimer
        from ui.settings import open_settings

        def _open() -> None:
            try:
                open_settings(parent=None, on_apply=self._settings_applied)
            except Exception:
                traceback.print_exc()

        QTimer.singleShot(0, _open)
        return {"queued": True}

    def _show_memory(self, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        from PySide6.QtCore import QTimer

        manager = self._memory_manager()
        if hasattr(manager, "replace_facts"):
            manager.replace_facts(facts or [])

        def _open() -> None:
            try:
                from ui.memory_viewer import MemoryViewer

                if self._memory_viewer is not None and self._memory_viewer.isVisible():
                    self._memory_viewer.raise_()
                    self._memory_viewer.activateWindow()
                    return

                viewer = MemoryViewer(manager, parent=None)
                if viewer is None:
                    print("[ui] MemoryViewer construction returned None")
                    return
                self._memory_viewer = viewer
                viewer.destroyed.connect(lambda: setattr(self, "_memory_viewer", None))
                viewer.show()
                viewer.raise_()
                viewer.activateWindow()
            except Exception:
                traceback.print_exc()

        QTimer.singleShot(0, _open)
        return {"queued": True}

    def _show_plugins(
        self,
        plugins: list[dict[str, Any]] | None = None,
        plugins_dir: str = "",
    ) -> dict[str, Any]:
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QScrollArea,
            QVBoxLayout,
            QWidget,
        )

        # Always rebuild from the latest payload: this method is also called to
        # refresh the list after an enable/disable toggle, so reusing the open
        # dialog would show stale state.
        reused = self._plugins_dialog is not None and self._plugins_dialog.isVisible()
        if self._plugins_dialog is not None:
            self._plugins_dialog.close()
            self._plugins_dialog = None

        dialog = QDialog()
        dialog.setWindowTitle("Plugin Manager")
        dialog.setModal(False)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Plugins")
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        plugin_rows = plugins or []
        if not plugin_rows:
            empty = QLabel("No plugins found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.55; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for plugin in plugin_rows:
                inner_layout.addWidget(self._plugin_card(plugin))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        if plugins_dir:
            open_btn = QPushButton("Open plugins folder")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(plugins_dir)))
            footer.addWidget(open_btn)
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(520, 420)
        self._plugins_dialog = dialog
        self._plugins_dialog.destroyed.connect(lambda: setattr(self, "_plugins_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": reused}

    def _plugin_card(self, plugin: dict[str, Any]):
        from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout

        card = QFrame()
        card.setObjectName("pluginCard")
        card.setStyleSheet(
            "QFrame#pluginCard { border: 1px solid rgba(128,128,128,0.25); "
            "border-radius: 8px; padding: 2px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        name_row = QHBoxLayout()
        name = str(plugin.get("name") or "Plugin")
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        enabled = bool(plugin.get("enabled", True))
        enable = QCheckBox("Enabled")
        enable.setChecked(enabled)
        # "discovered" (not yet loaded into a manager) plugins can't be toggled live.
        enable.setEnabled(str(plugin.get("status") or "") == "loaded")
        enable.toggled.connect(
            lambda checked, plugin_name=name: self.emit(
                "ui.plugins.set_enabled",
                {"plugin_name": plugin_name, "enabled": bool(checked)},
            )
        )
        name_row.addWidget(enable)
        layout.addLayout(name_row)

        path = str(plugin.get("path") or "")
        if path:
            path_lbl = QLabel(path)
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        hooks = plugin.get("hooks") or []
        tools = plugin.get("tools") or []
        details = []
        if hooks:
            details.append("Hooks: " + ", ".join(str(h) for h in hooks))
        if tools:
            details.append("Tools: " + ", ".join(str(t) for t in tools))
        if details:
            detail_lbl = QLabel("\n".join(details))
            detail_lbl.setWordWrap(True)
            detail_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
            layout.addWidget(detail_lbl)

        # Per-mod settings (replaces the old tray-action buttons)
        settings_box = self._plugin_settings_box(name, plugin.get("settings") or [])
        if settings_box is not None:
            settings_box.setEnabled(enabled)
            layout.addWidget(settings_box)

        error = str(plugin.get("error") or "")
        if error:
            error_lbl = QLabel(error)
            error_lbl.setWordWrap(True)
            error_lbl.setStyleSheet("font-size: 8pt; color: #b42318;")
            layout.addWidget(error_lbl)
        return card

    def _plugin_settings_box(self, plugin_name: str, settings: list):
        from PySide6.QtWidgets import (
            QCheckBox, QComboBox, QFormLayout, QFrame, QLineEdit,
        )

        if not settings:
            return None
        box = QFrame()
        form = QFormLayout(box)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)
        truthy = {"1", "true", "yes", "on"}

        def save(key, value):
            self.emit(
                "ui.plugins.set_setting",
                {"plugin_name": plugin_name, "key": key, "value": value},
            )

        for s in settings:
            if not isinstance(s, dict):
                continue
            key = str(s.get("key") or "").strip()
            if not key:
                continue
            label = str(s.get("label") or key)
            stype = str(s.get("type") or "text").lower()
            value = s.get("value")
            options = s.get("options") or []

            if stype == "bool":
                w = QCheckBox()
                w.setChecked(str(value).strip().lower() in truthy)
                w.toggled.connect(
                    lambda checked, k=key: save(k, "true" if checked else "false")
                )
            elif stype == "choice" and options:
                w = QComboBox()
                opts = [str(o) for o in options]
                w.addItems(opts)
                if str(value) in opts:
                    w.setCurrentText(str(value))
                w.currentTextChanged.connect(lambda text, k=key: save(k, text))
            else:
                w = QLineEdit("" if value is None else str(value))
                if stype == "number":
                    w.setPlaceholderText("number")
                w.editingFinished.connect(lambda k=key, e=w: save(k, e.text()))

            help_text = str(s.get("help") or "")
            if help_text:
                w.setToolTip(help_text)
            form.addRow(label, w)
        return box

    def _show_agent_task(self, spec: dict[str, Any] | None = None) -> dict[str, Any]:
        from core.agent.task_spec import agent_task_spec_from_dict
        from ui.agent.task_window import open_agent_task_dialog

        initial_spec = None
        if isinstance(spec, dict) and spec:
            initial_spec = agent_task_spec_from_dict(spec)

        def on_submit(task_spec) -> None:
            from dataclasses import asdict

            self._start_agent_run(asdict(task_spec))

        open_agent_task_dialog(
            parent=None,
            on_submit=on_submit,
            approval_notice_callback=lambda text, resolved: self._agent_notify_approval(
                text,
                resolved=resolved,
            ),
            initial_spec=initial_spec,
        )
        return {"shown": True, "prefilled": initial_spec is not None}

    def _start_agent_run(self, spec: dict[str, Any]) -> None:
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.close()
        dialog = MacAgentRunDialog(self, spec)
        self._agent_run_dialog = dialog
        dialog.dialog.destroyed.connect(
            lambda _obj=None, w=dialog: setattr(self, "_agent_run_dialog", None)
            if self._agent_run_dialog is w else None
        )
        dialog.show()
        self.emit("ui.agent.run_requested", {"spec": spec})

    def _show_agent_history(
        self,
        runs: list[dict[str, Any]] | None = None,
        runs_root: str = "",
    ) -> dict[str, Any]:
        if self._agent_history_dialog is None:
            dialog = MacAgentHistoryDialog(self)
            self._agent_history_dialog = dialog
            dialog.dialog.destroyed.connect(
                lambda _obj=None, w=dialog: setattr(self, "_agent_history_dialog", None)
                if self._agent_history_dialog is w else None
            )
        self._agent_history_dialog.replace_runs(runs or [], runs_root)
        self._agent_history_dialog.show()
        return {"shown": True, "runs": len(runs or [])}

    def _agent_log(self, **params: Any) -> dict[str, Any]:
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.append_log(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_trace(self, **params: Any) -> dict[str, Any]:
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.append_trace(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_done(self, **params: Any) -> dict[str, Any]:
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.finish(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_approval_request(self, **params: Any) -> dict[str, Any]:
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.request_approval(params)
            return {"accepted": True}
        self._agent_notify_approval("Agent approval requested.", resolved=False, data=params)
        return {"accepted": False}

    def _agent_history_detail(self, **params: Any) -> dict[str, Any]:
        if self._agent_history_dialog is not None:
            self._agent_history_dialog.update_detail(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_notify_approval(
        self,
        text: str = "",
        resolved: bool = False,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        overlay = self._ensure_overlay()
        notify = getattr(overlay, "notify_agent_approval", None)
        if callable(notify):
            notify(text or "Agent approval requested.", resolved=bool(resolved))
        return {"shown": bool(callable(notify)), "data": data or {}}


def main() -> int:
    root = configure_paths()
    os.chdir(root)
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")
    os.environ.setdefault("WISP_MACOS_PY_UI_HOST", "1")
    real_out = _protect_stdout()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Wisp Python UI")
    app.setApplicationDisplayName("Wisp")
    app.setQuitOnLastWindowClosed(False)

    icon_path = Path(root) / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        from ui.shared.theme import apply_app_theme

        apply_app_theme(app)
    except Exception:
        traceback.print_exc()

    host = QtProtocolHost(app, real_out)
    app._wisp_macos_py_ui_host = host
    host.emit("ui.ready", {"repo": str(root)})
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
