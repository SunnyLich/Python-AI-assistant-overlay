"""wisp-ui worker: the only process allowed to own PySide6 widgets."""

from __future__ import annotations

import html
import json
import itertools
import logging
import math
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


def _make_fit_graphics_view(QGraphicsView, Qt):
    class _MacFitGraphicsView(QGraphicsView):
        def __init__(self, scene):
            super().__init__(scene)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        def fit_scene(self) -> None:
            rect = self.scene().sceneRect() if self.scene() else None
            if rect is not None and not rect.isEmpty():
                self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

        def resizeEvent(self, event):  # noqa: N802
            super().resizeEvent(event)
            self.fit_scene()

        def showEvent(self, event):  # noqa: N802
            super().showEvent(event)
            self.fit_scene()

    return _MacFitGraphicsView


def _make_live_agent_item(
    QGraphicsEllipseItem,
    QGraphicsItemGroup,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QBrush,
    QColor,
    QFont,
    QPen,
    QPointF,
    Qt,
):
    class _MacLiveAgentItem(QGraphicsItemGroup):
        WIDTH = 220
        HEIGHT = 150
        TEXT_WIDTH = 196
        GRIP = 16
        MIN_SCALE = 0.6
        MAX_SCALE = 2.2
        _CLICK_SLOP = 5

        def __init__(
            self,
            index: int,
            click_callback,
            x: float,
            y: float,
            name: str,
            role: str,
            status: str,
            objective: str,
            health: str,
            active: bool,
            selected: bool,
            scale: float = 1.0,
            on_geometry_change=None,
        ):
            super().__init__()
            self._index = index
            self._click_callback = click_callback
            self._on_geometry_change = on_geometry_change
            self._scale_factor = scale
            self._resizing = False
            self._press_scene = QPointF()
            self.setAcceptHoverEvents(True)
            self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable, True)
            self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemSendsGeometryChanges, True)
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

            grip = QGraphicsRectItem(self.WIDTH - self.GRIP, self.HEIGHT - self.GRIP, self.GRIP, self.GRIP)
            grip.setBrush(QBrush(QColor(border)))
            grip.setPen(QPen(QColor("#ffffff"), 1))
            grip.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(grip)

            self.setScale(scale)
            self.setPos(x, y)

        def hoverEnterEvent(self, event):  # noqa: N802
            self.setOpacity(0.88)
            super().hoverEnterEvent(event)

        def hoverLeaveEvent(self, event):  # noqa: N802
            self.setOpacity(1.0)
            super().hoverLeaveEvent(event)

        def _in_grip(self, event) -> bool:
            pos = event.pos()
            return pos.x() >= self.WIDTH - self.GRIP and pos.y() >= self.HEIGHT - self.GRIP

        def _emit_geometry(self) -> None:
            if self._on_geometry_change is not None:
                pos = self.pos()
                self._on_geometry_change(self._index, pos.x(), pos.y(), self._scale_factor)

        def mousePressEvent(self, event):  # noqa: N802
            self._press_scene = event.scenePos()
            if self._in_grip(event):
                self._resizing = True
                event.accept()
                return
            self._resizing = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):  # noqa: N802
            if self._resizing:
                origin = self.scenePos()
                dx = event.scenePos().x() - origin.x()
                dy = event.scenePos().y() - origin.y()
                raw = max(dx / self.WIDTH, dy / self.HEIGHT)
                self._scale_factor = max(self.MIN_SCALE, min(self.MAX_SCALE, raw))
                self.setScale(self._scale_factor)
                event.accept()
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):  # noqa: N802
            if self._resizing:
                self._resizing = False
                event.accept()
                self._emit_geometry()
                return
            super().mouseReleaseEvent(event)
            moved = (event.scenePos() - self._press_scene).manhattanLength() >= self._CLICK_SLOP
            event.accept()
            if moved:
                self._emit_geometry()
            else:
                self._click_callback(self._index)

    return _MacLiveAgentItem


class MacAgentRunDialog:
    """Protocol-backed agent run window for the pure-Python target."""

    def __init__(self, host: "QtProtocolHost", spec: dict[str, Any]) -> None:
        from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QPainterPath, QPen, QTextCursor
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QFrame,
            QGraphicsEllipseItem,
            QGraphicsItemGroup,
            QGraphicsRectItem,
            QGraphicsScene,
            QGraphicsTextItem,
            QGraphicsView,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QSplitter,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )

        self._host = host
        self._spec = dict(spec or {})
        self._run_dir = ""
        self._pending_approval_id = ""
        self._QApplication = QApplication
        self._QDesktopServices = QDesktopServices
        self._QTextCursor = QTextCursor
        self._QTimer = QTimer
        self._QUrl = QUrl
        self._QBrush = QBrush
        self._QColor = QColor
        self._QFont = QFont
        self._QPainterPath = QPainterPath
        self._QPen = QPen
        self._Qt = Qt
        self._LiveAgentItem = _make_live_agent_item(
            QGraphicsEllipseItem,
            QGraphicsItemGroup,
            QGraphicsRectItem,
            QGraphicsTextItem,
            QBrush,
            QColor,
            QFont,
            QPen,
            QPointF,
            Qt,
        )
        fit_graphics_view = _make_fit_graphics_view(QGraphicsView, Qt)
        self._agent_roles = self._agent_roles_from_spec(self._spec)
        self._agent_names = list(self._agent_roles) or ["Solo"]
        self._active_agent = self._agent_names[0]
        self._selected_agent = self._active_agent
        self._meeting_messages: list[dict[str, str]] = []
        self._agent_layout: dict[str, dict[str, float]] = {}
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

        title = str(self._spec.get("title") or "Agent Task")
        self.dialog = QDialog()
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialog.setWindowTitle(f"Agent Task - {title}")
        self.dialog.setMinimumSize(980, 620)

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
        self.meeting_scene = QGraphicsScene(self.dialog)
        self.meeting_view = fit_graphics_view(self.meeting_scene)
        self.meeting_view.setMinimumSize(280, 220)
        self.meeting_view.setStyleSheet("QGraphicsView { background: #edf3fa; border: 1px solid #c2ccda; }")
        meeting_splitter = QSplitter(Qt.Orientation.Horizontal)
        meeting_splitter.setChildrenCollapsible(False)

        meeting_panel = QWidget()
        meeting_layout = QVBoxLayout(meeting_panel)
        meeting_layout.setContentsMargins(0, 0, 0, 0)
        meeting_layout.setSpacing(6)
        meeting_header = QHBoxLayout()
        meeting_header.addWidget(QLabel("Meeting"))
        meeting_header.addStretch()
        self.reset_layout_btn = QPushButton("Reset Layout")
        self.reset_layout_btn.setToolTip("Restore every agent card to its default position and size")
        self.reset_layout_btn.clicked.connect(self._reset_agent_layout)
        meeting_header.addWidget(self.reset_layout_btn)
        meeting_layout.addLayout(meeting_header)
        meeting_layout.addWidget(self.meeting_view, 1)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(QLabel("Agent Detail"))
        self.agent_summary_view = QTextEdit()
        self.agent_summary_view.setReadOnly(True)
        self.agent_summary_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.agent_summary_view.setMaximumHeight(250)
        self.agent_summary_view.setMinimumWidth(240)
        self.agent_detail_view = self.agent_summary_view
        detail_layout.addWidget(self.agent_summary_view, 1)
        detail_layout.addWidget(QLabel("Recent Activity"))
        self.agent_activity_view = QTextEdit()
        self.agent_activity_view.setReadOnly(True)
        self.agent_activity_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        detail_layout.addWidget(self.agent_activity_view, 1)

        board_panel = QWidget()
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(6)
        board_layout.addWidget(QLabel("Shared Board"))
        self.shared_board_view = QTextEdit()
        self.shared_board_view.setReadOnly(True)
        self.shared_board_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        board_layout.addWidget(self.shared_board_view, 1)

        meeting_splitter.addWidget(meeting_panel)
        meeting_splitter.addWidget(board_panel)
        meeting_splitter.addWidget(detail_panel)
        meeting_splitter.setStretchFactor(0, 4)
        meeting_splitter.setStretchFactor(1, 2)
        meeting_splitter.setStretchFactor(2, 4)
        meeting_splitter.setSizes([460, 240, 460])

        self.log_view = QTextEdit()
        self.trace_view = QTextEdit()
        self.final_view = QTextEdit()
        for view in (
            self.agent_detail_view,
            self.agent_activity_view,
            self.shared_board_view,
            self.log_view,
            self.trace_view,
            self.final_view,
        ):
            view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.trace_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.final_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.tabs.addTab(meeting_splitter, "Meeting Room")
        self.tabs.addTab(self.log_view, "Live Log")
        self.tabs.addTab(self.trace_view, "Model Trace")
        self.tabs.addTab(self.final_view, "Final Report")
        root.addWidget(self.tabs, 1)
        self._refresh_meeting_room()

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
            self._update_live_meeting(line)
            self._append_text(self.log_view, line)
            self.status_label.setText("Running...")
            self._refresh_meeting_room()

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
            self._set_agent_status(self._active_agent, "Failed", error_text)
        elif payload.get("cancelled"):
            self.status_label.setText("Cancelled")
            self._set_agent_status(self._active_agent, "Cancelled", "Agent task cancelled.")
        else:
            self.status_label.setText("Finished")
            for name in self._agent_names:
                status = str(self._agent_states.get(name, {}).get("status") or "")
                if status not in {"Done", "Failed", "Cancelled"}:
                    self._set_agent_status(name, "Finished", "Agent run finished.")
        self.approval_panel.hide()
        self.cancel_btn.setEnabled(False)
        self.open_result_btn.setEnabled(bool(self._run_dir))
        self.retry_btn.setEnabled(True)
        self.continue_btn.setEnabled(bool(self._run_dir))
        self._refresh_meeting_room()

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
        self._set_agent_status(self._active_agent, "Needs approval", text)
        self._refresh_meeting_room()
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

    @staticmethod
    def _agent_roles_from_spec(spec: dict[str, Any]) -> dict[str, str]:
        roles: dict[str, str] = {}
        agents = spec.get("agents") if isinstance(spec, dict) else None
        if isinstance(agents, list):
            for idx, agent in enumerate(agents):
                if isinstance(agent, dict):
                    name = str(agent.get("name") or f"Agent {idx + 1}").strip()
                    role = str(agent.get("role") or "Agent").strip()
                else:
                    name = str(getattr(agent, "name", "") or f"Agent {idx + 1}").strip()
                    role = str(getattr(agent, "role", "") or "Agent").strip()
                if name:
                    roles[name] = role or "Agent"
        return roles

    def _update_live_meeting(self, line: str) -> None:
        from ui.agent.log_parser import parse_live_log_event

        event = parse_live_log_event(line)
        body = event.body
        if event.kind in {"agent_turn", "agent_read_only_turn"}:
            name = event.agent
            self._ensure_agent_state(name)
            self._active_agent = name
            if self._selected_agent not in self._agent_states:
                self._selected_agent = name
            status = "Read-only briefing" if event.kind == "agent_read_only_turn" else "Thinking"
            self._set_agent_status(name, status, body)
            return
        if body.startswith("parallel read-only briefing started"):
            for name in self._agent_names:
                self._set_agent_status(name, "Joining briefing", body)
            return
        if body.startswith("parallel read-only briefing finished"):
            for name in self._agent_names:
                current = str(self._agent_states[name].get("status") or "")
                if current in {"Joining briefing", "Read-only briefing", "Calling model"}:
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
            self._agent_health(self._active_agent)["fallbacks"] += 1
            self._set_agent_status(self._active_agent, "Model error; retrying", body)
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
            summary = body.split(": ", 1)[1] if ": " in body else body
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
            self.status_label.setText("Paused after current turn")
            return
        if body.startswith("agent run resumed"):
            self.status_label.setText("Running...")
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
        try:
            payload = body[len("message: "):]
            route, message = payload.split(": ", 1)
            source, target = route.split(" -> ", 1)
        except ValueError:
            return
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
        del self._meeting_messages[:-12]
        if item["from"] in self._agent_states:
            self._append_agent_history(item["from"], f"Told {item['to']}: {item['message']}")
        if item["to"] in self._agent_states:
            self._append_agent_history(item["to"], f"Heard from {item['from']}: {item['message']}")

    def _ensure_agent_state(self, name: str) -> None:
        name = (name or "Agent").strip() or "Agent"
        if name in self._agent_states:
            return
        self._agent_names.append(name)
        self._agent_roles.setdefault(name, "Agent")
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
        marker = " received in "
        if marker not in body:
            return
        try:
            seconds = float(body.split(marker, 1)[1].split("s", 1)[0])
        except ValueError:
            return
        health = self._agent_health(name)
        health["calls"] = int(health.get("calls", 0)) + 1
        health["total_latency"] = float(health.get("total_latency", 0.0)) + seconds

    @staticmethod
    def _objective_from_thought(thought: str) -> str:
        clean = " ".join(thought.split())
        return MacAgentRunDialog._shorten(clean, 120)

    def _append_agent_history(self, name: str, event: str) -> None:
        self._ensure_agent_state(name)
        history = self._agent_states[name]["history"]
        history.append(event)
        del history[:-40]

    def _refresh_meeting_room(self) -> None:
        self._refresh_shared_board()
        self._draw_live_meeting()

    def _draw_live_meeting(self) -> None:
        self.meeting_scene.clear()
        self.meeting_scene.setSceneRect(0, 0, 1080, 560)

        bg = self._QPainterPath()
        bg.addRoundedRect(10, 10, 1060, 540, 16, 16)
        self.meeting_scene.addPath(bg, self._QPen(self._QColor("#cfd9e6"), 1), self._QBrush(self._QColor("#edf3fa")))

        table = self._QPainterPath()
        table.addRoundedRect(445, 230, 190, 100, 24, 24)
        self.meeting_scene.addPath(
            table,
            self._QPen(self._QColor("#9fb2c8"), 1.5),
            self._QBrush(self._QColor("#dbe6f2")),
        )

        title = self.meeting_scene.addText("Agent Meeting", self._QFont("Segoe UI", 11, self._QFont.Weight.DemiBold))
        title.setDefaultTextColor(self._QColor("#26384f"))
        title.setTextWidth(150)
        title.setPos(465, 267)
        title.setAcceptedMouseButtons(self._Qt.MouseButton.NoButton)

        positions = self._live_agent_positions(len(self._agent_names))
        centers: dict[str, tuple[float, float]] = {}
        live_item = self._LiveAgentItem
        for idx, name in enumerate(self._agent_names):
            state = self._agent_states.get(name, {})
            x, y = positions[idx]
            override = self._agent_layout.get(name) or {}
            x = float(override.get("x", x))
            y = float(override.get("y", y))
            scale = float(override.get("scale", 1.0))
            centers[name] = (
                x + live_item.WIDTH * scale / 2,
                y + live_item.HEIGHT * scale / 2,
            )
            item = live_item(
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
                scale=scale,
                on_geometry_change=self._on_agent_geometry_change,
            )
            self.meeting_scene.addItem(item)

        self._draw_last_message_arrow(centers)
        self.meeting_view.fit_scene()
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
        pen = self._QPen(self._QColor("#2f80ed"), 2.3)
        self.meeting_scene.addLine(sx_edge, sy_edge, tx_edge, ty_edge, pen)
        dx, dy = tx_edge - sx_edge, ty_edge - sy_edge
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        size = 11
        bx = tx_edge - ux * size
        by = ty_edge - uy * size
        path = self._QPainterPath()
        path.moveTo(tx_edge, ty_edge)
        path.lineTo(bx + px * size * 0.55, by + py * size * 0.55)
        path.lineTo(bx - px * size * 0.55, by - py * size * 0.55)
        path.closeSubpath()
        self.meeting_scene.addPath(path, self._QPen(self._QColor("#2f80ed")), self._QBrush(self._QColor("#2f80ed")))

    def _live_edge_points(self, sx: float, sy: float, tx: float, ty: float) -> tuple[float, float, float, float]:
        dx, dy = tx - sx, ty - sy
        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        live_item = self._LiveAgentItem
        half_w, half_h = live_item.WIDTH / 2, live_item.HEIGHT / 2
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

    def _live_agent_positions(self, count: int) -> list[tuple[float, float]]:
        if count <= 0:
            return []
        live_item = self._LiveAgentItem
        cx, cy = 540, 280
        rx, ry = 390, 200
        return [
            (
                cx + math.cos(-math.pi / 2 + 2 * math.pi * idx / count) * rx - live_item.WIDTH / 2,
                cy + math.sin(-math.pi / 2 + 2 * math.pi * idx / count) * ry - live_item.HEIGHT / 2,
            )
            for idx in range(count)
        ]

    def _select_live_agent(self, index: int) -> None:
        if 0 <= index < len(self._agent_names):
            self._selected_agent = self._agent_names[index]
            self._draw_live_meeting()

    def _on_agent_geometry_change(self, index: int, x: float, y: float, scale: float) -> None:
        if not (0 <= index < len(self._agent_names)):
            return
        rect = self.meeting_scene.sceneRect()
        live_item = self._LiveAgentItem
        width = live_item.WIDTH * scale
        height = live_item.HEIGHT * scale
        x = max(rect.left(), min(x, rect.right() - width))
        y = max(rect.top(), min(y, rect.bottom() - height))
        self._agent_layout[self._agent_names[index]] = {"x": x, "y": y, "scale": scale}
        self._QTimer.singleShot(0, self._draw_live_meeting)

    def _reset_agent_layout(self) -> None:
        if not self._agent_layout:
            return
        self._agent_layout.clear()
        self._draw_live_meeting()

    def _refresh_agent_detail(self) -> None:
        state = self._agent_states.get(self._selected_agent)
        if not state:
            self.agent_detail_view.clear()
            self.agent_activity_view.clear()
            return
        health = self._health_detail(self._selected_agent)
        detail = (
            f"<h3>{html.escape(self._selected_agent)}</h3>"
            f"<p><b>Role:</b> {html.escape(str(state.get('role') or 'Agent'))}<br>"
            f"<b>Status:</b> {html.escape(str(state.get('status') or 'Waiting'))}<br>"
            f"<b>Last tool:</b> {html.escape(str(state.get('tool') or 'None'))}</p>"
            f"<p><b>Current objective</b><br>"
            f"{html.escape(str(state.get('objective') or 'No current objective.'))}</p>"
            f"<p><b>Model health</b><br>{html.escape(health)}</p>"
            f"<p><b>Latest thought</b><br>"
            f"{html.escape(str(state.get('thought') or 'No thought yet.'))}</p>"
        )
        self.agent_detail_view.setHtml(detail)
        history = state.get("history") or []
        self.agent_activity_view.setPlainText(
            "\n".join(f"- {item}" for item in history[-18:]) or "- No activity yet."
        )

    def _refresh_shared_board(self) -> None:
        if not self._meeting_messages:
            self.shared_board_view.setPlainText("No messages yet.")
            return
        lines: list[str] = []
        for item in self._meeting_messages:
            lines.append(f"{item['from']} -> {item['to']}")
            lines.append(item["message"])
            lines.append("")
        self.shared_board_view.setPlainText("\n".join(lines).strip())

    def _health_badge(self, name: str) -> str:
        health = self._agent_health(name)
        calls = int(health.get("calls", 0))
        avg = "-" if not calls else f"{float(health.get('total_latency', 0.0)) / calls:.1f}s"
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

    @staticmethod
    def _shorten(text: str, max_chars: int) -> str:
        clean = " ".join(text.split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max(0, max_chars - 1)].rstrip() + "..."

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
        self._plugin_settings_dialogs: dict[str, Any] = {}
        self._plugin_log_dialogs: dict[str, Any] = {}
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
        if method == "ui.reply.transcript":
            return self._reply_transcript(**params)
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

    def _reply_transcript(self, text: str = "") -> dict[str, Any]:
        self._ensure_bubble().show_transcript(text)
        return {"shown": bool((text or "").strip()), "text": text}

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
            QFileDialog,
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
        dialog.setWindowTitle("Addon Manager")
        dialog.setModal(False)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Addons")
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel(
            "Addons are Python packages in the addons/ folder. "
            "Each addon runs in its own host process."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 9pt; opacity: 0.7;")
        root.addWidget(subtitle)

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
            open_btn = QPushButton("Open addons folder")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(plugins_dir)))
            footer.addWidget(open_btn)
            install_btn = QPushButton("Install archive")
            install_btn.clicked.connect(lambda: self._install_plugin_archive_dialog())
            footer.addWidget(install_btn)
            install_folder_btn = QPushButton("Install folder")
            install_folder_btn.clicked.connect(lambda: self._install_plugin_folder_dialog())
            footer.addWidget(install_folder_btn)
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

    def _install_plugin_archive_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        archive, _selected_filter = QFileDialog.getOpenFileName(
            self._plugins_dialog,
            "Install Addon Archive",
            "",
            "Wisp Addons (*.wisp *.zip)",
        )
        if archive:
            self.emit("ui.plugins.install_archive", {"path": archive})

    def _install_plugin_folder_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self._plugins_dialog,
            "Install Addon Folder",
            "",
        )
        if folder:
            self.emit("ui.plugins.install_folder", {"path": folder})

    def _plugin_card(self, plugin: dict[str, Any]):
        from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

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
        name = str(plugin.get("name") or plugin.get("id") or "Addon")
        addon_id = str(plugin.get("id") or name)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 11pt; font-weight: 600;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        settings_btn = QPushButton("Settings")
        settings_btn.setToolTip("Open this addon's settings")
        settings_btn.clicked.connect(
            lambda _checked=False, p=plugin, aid=addon_id, n=name: self._show_plugin_settings_dialog(
                aid,
                n,
                p.get("settings") or [],
            )
        )
        name_row.addWidget(settings_btn)

        logs_btn = QPushButton("Logs")
        logs_btn.setToolTip("Open this addon's diagnostic log")
        logs_btn.clicked.connect(
            lambda _checked=False, p=plugin, aid=addon_id, n=name: self._show_plugin_log_dialog(
                aid,
                n,
                str(p.get("logs") or ""),
            )
        )
        name_row.addWidget(logs_btn)

        runtime = plugin.get("runtime") if isinstance(plugin.get("runtime"), dict) else {}
        packages = [str(p) for p in (runtime.get("packages") or [])]
        has_dependencies = str(runtime.get("tier") or "1") == "2"
        if has_dependencies:
            repair_btn = QPushButton(self._plugin_runtime_action_label(runtime))
            repair_btn.setToolTip("Install or rebuild this addon's dependency environment")
            repair_btn.clicked.connect(
                lambda _checked=False, plugin_name=addon_id, display_name=name, rt=runtime: self._confirm_plugin_environment(
                    plugin_name,
                    display_name,
                    rt,
                )
            )
            name_row.addWidget(repair_btn)

        enabled = bool(plugin.get("enabled", True))
        enable = QCheckBox("Enabled")
        enable.setChecked(enabled)
        # "discovered" (not yet loaded into a manager) plugins can't be toggled live.
        enable.setEnabled(str(plugin.get("status") or "") in {"loaded", "disabled", "needs_dependencies", "needs_approval"})
        enable.toggled.connect(
            lambda checked, plugin_name=addon_id: self.emit(
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

        if has_dependencies:
            dep_parts = [self._plugin_runtime_summary(runtime)]
            if packages:
                dep_parts.append("Packages: " + ", ".join(packages))
            runtime_error = str(runtime.get("error") or "")
            if runtime_error:
                dep_parts.append(runtime_error)
            dep_lbl = QLabel("\n".join(dep_parts))
            dep_lbl.setWordWrap(True)
            dep_lbl.setStyleSheet("font-size: 8pt; opacity: 0.6;")
            layout.addWidget(dep_lbl)

        error = str(plugin.get("error") or "")
        if error:
            error_lbl = QLabel(error)
            error_lbl.setWordWrap(True)
            error_lbl.setStyleSheet("font-size: 8pt; color: #b42318;")
            layout.addWidget(error_lbl)
        return card

    def _confirm_plugin_environment(self, plugin_name: str, display_name: str, runtime: dict[str, Any]) -> None:
        from PySide6.QtWidgets import QMessageBox

        packages = [str(p) for p in (runtime.get("packages") or [])]
        lines = [
            f"{display_name} declares Python/package dependencies.",
            "",
            f"Python: {runtime.get('python_requirement') or 'current runtime'}",
            "Packages:",
        ]
        lines.extend(f"  {package}" for package in packages)
        if not packages:
            lines.append("  No packages declared")
        env_path = str(runtime.get("env_path") or "")
        if env_path:
            lines.extend(["", f"Environment: {env_path}"])
        lines.extend(["", "Install or rebuild this environment now?"])
        choice = QMessageBox.question(
            self._plugins_dialog,
            "Approve Addon Dependencies",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.emit("ui.plugins.repair_environment", {"plugin_name": plugin_name})

    @staticmethod
    def _plugin_runtime_action_label(runtime: dict[str, Any]) -> str:
        if runtime.get("needs_approval"):
            return "Approve env"
        return "Repair env" if runtime.get("ready") else "Install env"

    @staticmethod
    def _plugin_runtime_summary(runtime: dict[str, Any]) -> str:
        if runtime.get("needs_approval"):
            return "Dependency env: needs approval"
        return "Dependency env: ready" if runtime.get("ready") else "Dependency env: needs install"

    def _show_plugin_settings_dialog(self, plugin_name: str, display_name: str, settings: list):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

        existing = self._plugin_settings_dialogs.get(plugin_name)
        if existing is not None and existing.isVisible():
            existing.close()

        dialog = QDialog(self._plugins_dialog)
        dialog.setWindowTitle(f"{display_name} Settings")
        dialog.setModal(False)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{display_name} Settings")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        settings_box = self._plugin_settings_box(plugin_name, settings)
        if settings_box is None:
            empty = QLabel("This addon does not expose settings.")
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
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(420, 360)
        dialog.destroyed.connect(lambda _obj=None, key=plugin_name: self._plugin_settings_dialogs.pop(key, None))
        self._plugin_settings_dialogs[plugin_name] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _show_plugin_log_dialog(self, plugin_name: str, display_name: str, logs: str):
        from PySide6.QtGui import QTextCursor
        from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

        existing = self._plugin_log_dialogs.get(plugin_name)
        if existing is not None and existing.isVisible():
            text = existing.findChild(QTextEdit)
            if text is not None:
                text.setPlainText(logs or "No log output yet.")
                text.moveCursor(QTextCursor.MoveOperation.End)
            existing.raise_()
            existing.activateWindow()
            return {"shown": True, "reused": True}

        dialog = QDialog(self._plugins_dialog)
        dialog.setWindowTitle(f"{display_name} Logs")
        dialog.setModal(False)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{display_name} Logs")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        text.setPlainText(logs or "No log output yet.")
        text.moveCursor(QTextCursor.MoveOperation.End)
        root.addWidget(text, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(560, 360)
        dialog.destroyed.connect(lambda _obj=None, key=plugin_name: self._plugin_log_dialogs.pop(key, None))
        self._plugin_log_dialogs[plugin_name] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": False}

    def _plugin_settings_box(self, plugin_name: str, settings: list):
        from PySide6.QtWidgets import (
            QCheckBox, QComboBox, QFormLayout, QFrame, QLineEdit,
        )

        if not settings:
            return None
        box = QFrame()
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
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
        return box if form.rowCount() else None

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
        from ui.i18n import install as install_i18n

        apply_app_theme(app)
        install_i18n(app)
    except Exception:
        traceback.print_exc()

    host = QtProtocolHost(app, real_out)
    app._wisp_macos_py_ui_host = host
    host.emit("ui.ready", {"repo": str(root)})
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
