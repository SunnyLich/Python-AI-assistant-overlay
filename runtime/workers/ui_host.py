"""wisp-ui worker: the only process allowed to own PySide6 widgets."""

from __future__ import annotations

import html
import json
import itertools
import logging
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from runtime.bootstrap import configure_paths
from runtime.boundaries import boundary_status
from runtime import VERSION, protocol
from ui.agent.activity_i18n import (
    translate_agent_activity_text,
    translate_agent_health_badge,
    translate_agent_health_detail,
    translate_agent_log_line,
    translate_agent_name,
    translate_agent_role,
    translate_agent_status,
)
from ui.i18n import localize_widget_tree, t

log = logging.getLogger("wisp.ui_host")

_CONTEXT_SOURCE_LABELS = {
    "App",
    "Browser/Web",
    "Selection",
    "Clipboard",
    "Screenshot",
    "Git/GitHub",
    "Memory",
    "Files",
    "Context",
}

_STATUS_LABELS = {
    "pass": "PASS",
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
}

_PRIVACY_CATEGORY_LABELS = {
    "api_key": "API key",
    "bearer_token": "Bearer token",
    "card_number": "Card number",
    "credential": "Credential",
    "email": "Email",
    "private_key": "Private key",
    "ssn": "SSN",
    "url_credential": "URL credential",
}

_PRIVACY_SOURCE_LABELS = {
    "active_document": "Active document",
    "ambient": "App",
    "buffered_context": "Context",
    "clipboard": "Clipboard",
    "prompt": "Prompt",
    "selection": "Selection",
}


def _context_display_label(label: str) -> str:
    """Translate built-in context source labels while preserving user labels."""
    text = str(label or "Context")
    return t(text) if text in _CONTEXT_SOURCE_LABELS else text


def _localized_context_items(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Translate built-in context item labels while preserving custom metadata."""
    localized: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        label = next_item.get("label")
        if label is None:
            label = next_item.get("name")
        next_item["label"] = _context_display_label(str(label or "Context"))
        localized.append(next_item)
    return localized


def _privacy_category_label(category: str) -> str:
    """Translate privacy report category keys into user-facing labels."""
    text = str(category or "Sensitive data").strip()
    label = _PRIVACY_CATEGORY_LABELS.get(text.lower(), text or "Sensitive data")
    return t(label)


def _privacy_source_label(source: str) -> str:
    """Translate privacy report source keys while preserving file/user labels."""
    text = str(source or "Context").strip()
    lowered = text.lower()
    if lowered.startswith("document:"):
        return f"{t('Document')}: {text.split(':', 1)[1].strip()}"
    if lowered.startswith("dropped:"):
        return f"{t('Dropped file')}: {text.split(':', 1)[1].strip()}"
    if lowered in _PRIVACY_SOURCE_LABELS:
        return t(_PRIVACY_SOURCE_LABELS[lowered])
    return _context_display_label(text)


def _mac_status_text(status: str) -> str:
    """Handle mac status text for runtime workers UI host."""
    return translate_agent_status(status)


def _status_label(status: str) -> str:
    """Translate setup/health status labels."""
    return t(_STATUS_LABELS.get(str(status or "").strip().lower(), "WARN"))


def _translate_health_value(value: str) -> str:
    """Translate known health value atoms while preserving provider/model names."""
    text = str(value or "")
    if text in {"None", "unavailable", "authorized", "denied", "not_determined", "restricted"}:
        return t(text)
    return text


def _translate_health_text(text: str) -> str:
    """Translate health rows, including dynamic provider/model values."""
    value = str(text or "")
    if "\n" in value:
        return "\n".join(_translate_health_text(part) for part in value.splitlines())

    dynamic_patterns: tuple[tuple[str, str], ...] = (
        (r"^LLM route configured: (?P<route>.+)\.$", "LLM route configured: {route}."),
        (r"^LLM route incomplete: (?P<route>.+)\.$", "LLM route incomplete: {route}."),
        (r"^TTS provider configured: (?P<provider>.+)\.$", "TTS provider configured: {provider}."),
        (r"^STT model configured: (?P<model>.+)\.$", "STT model configured: {model}."),
        (r"^(?P<count>\d+) hotkeys configured\.$", "{count} hotkeys configured."),
        (r"^(?P<label>.+) worker responded\.$", "{label} worker responded."),
        (r"^(?P<label>.+) worker did not respond\.$", "{label} worker did not respond."),
        (r"^Accessibility permission: (?P<value>.+)\.$", "Accessibility permission: {value}."),
        (r"^Screen recording permission: (?P<value>.+)\.$", "Screen recording permission: {value}."),
        (r"^Microphone permission: (?P<value>.+)\.$", "Microphone permission: {value}."),
        (r"^TTS synthesis responded with (?P<provider>.+)\.$", "TTS synthesis responded with {provider}."),
        (r"^LLM test failed: (?P<message>.+)$", "LLM test failed: {message}"),
        (r"^LLM route uses (?P<provider>.+) but you are not logged in\.$", "LLM route uses {provider} but you are not logged in."),
    )
    for pattern, template in dynamic_patterns:
        match = re.match(pattern, value)
        if match:
            groups = match.groupdict()
            if "message" in groups:
                groups["message"] = _translate_health_text(groups["message"])
            if "value" in groups:
                groups["value"] = _translate_health_value(groups["value"])
            return t(template).format(**groups)
    return t(value)


def _translate_notice_text(text: str) -> str:
    """Translate known system notice/bubble lines without touching arbitrary output."""
    lines = str(text or "").splitlines()
    if not lines:
        return t(str(text or ""))
    out: list[str] = []
    for line in lines:
        if not line:
            out.append(line)
            continue
        for prefix in ("Installed addon: ", "Technical detail: "):
            if line.startswith(prefix):
                out.append(t(prefix) + line[len(prefix):])
                break
        else:
            out.append(t(line))
    return "\n".join(out)


def _live_file_approval_summary(params: dict[str, Any]) -> tuple[str, str, str]:
    """Return action, path, and display text for live file approvals."""
    details = params.get("details") if isinstance(params.get("details"), dict) else {}
    action = str(params.get("action") or "file edit")
    path = str(params.get("path") or details.get("path") or "").strip()
    diff = str(params.get("diff") or details.get("diff") or "").strip()
    plus = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    minus = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    lines = [
        t("Wisp wants permission to modify a local file."),
        t("Why: Files is set to ask before write, so Wisp needs approval before changing disk."),
    ]
    if action:
        lines.append(f"{t('Tool:')} {action}")
    if path:
        lines.append(f"{t('Target:')} {path}")
    if "old_chars" in details or "new_chars" in details:
        lines.append(
            t("Change: replace {old} chars with {new} chars").format(
                old=int(details.get("old_chars") or 0),
                new=int(details.get("new_chars") or 0),
            )
        )
    elif "chars" in details:
        template = "Change: overwrite file with {chars} chars" if details.get("exists") else "Change: create file with {chars} chars"
        lines.append(t(template).format(chars=int(details.get("chars") or 0)))
    if diff:
        lines.append(t("Diff: +{added} -{removed} lines").format(added=plus, removed=minus))
    lines.append(t("Approve this file change?"))
    return action, path, "\n".join(lines)


def _ui_log_dir() -> Path:
    """Handle ui log dir for runtime workers UI host."""
    configured = os.environ.get("WISP_RUN_LOG_DIR")
    if configured:
        root = Path(configured)
    else:
        root = configure_paths() / "build_logs" / "ui_runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _format_thread_stacks() -> str:
    """Format thread stacks."""
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
        """Initialize the qt freeze watchdog instance."""
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
        """Handle beat for qt freeze watchdog."""
        self._last_beat = time.monotonic()

    def stop(self) -> None:
        """Stop the freeze-watchdog timer and its background thread."""
        self._stop.set()
        self._timer.stop()

    def _run(self) -> None:
        """Background loop: detect when the Qt event loop stalls and report it."""
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
            # has stopped draining while requests are queued â€” exactly the state
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
        """Write report."""
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
        """Initialize the memory proxy instance."""
        self._emit = emit_fn
        self._facts: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def replace_facts(self, facts: list[dict[str, Any]] | None) -> None:
        """Handle replace facts for memory proxy."""
        self._facts = [dict(f) for f in (facts or [])]

    def get_all_facts(self) -> list[dict[str, Any]]:
        """Return all facts."""
        return [dict(f) for f in self._facts]

    def add_fact_manual(
        self, text: str, category: str = "general", project: str | None = None
    ) -> None:
        """Add fact manual."""
        project = (project or "").strip()
        if project:
            category = "project_context"
        fact = {
            "id": f"pending-{next(self._ids)}",
            "text": text,
            "category": category or "general",
            "source": "manual",
            "project": project,
        }
        self._facts.append(fact)
        self._emit("ui.memory.add", {"text": text, "category": category, "project": project})

    def update_fact(
        self,
        fact_id: str,
        text: str,
        category: str | None = None,
        project: str | None = None,
    ) -> None:
        """Update fact."""
        payload = {"id": fact_id, "text": text, "category": category}
        if project is not None:
            project = project.strip()
            payload["project"] = project
            category = "project_context" if project else "general"
            payload["category"] = category
        for fact in self._facts:
            if str(fact.get("id")) == str(fact_id):
                fact["text"] = text
                if category is not None:
                    fact["category"] = category
                if project is not None:
                    fact["project"] = project
                break
        self._emit("ui.memory.update", payload)

    def delete_fact(self, fact_id: str) -> None:
        """Delete fact."""
        self._facts = [f for f in self._facts if str(f.get("id")) != str(fact_id)]
        self._emit("ui.memory.delete", {"id": fact_id})


def _make_fit_graphics_view(QGraphicsView, Qt):
    """Create fit graphics view."""
    class _MacFitGraphicsView(QGraphicsView):
        """Model mac fit graphics view."""
        def __init__(self, scene):
            """Initialize the mac fit graphics view instance."""
            super().__init__(scene)
            self._zoom_factor = 1.0
            self._min_zoom = 0.45
            self._max_zoom = 3.0
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        def fit_scene(self) -> None:
            """Handle fit scene for mac fit graphics view."""
            rect = self.scene().sceneRect() if self.scene() else None
            if rect is not None and not rect.isEmpty():
                self.resetTransform()
                self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                if self._zoom_factor != 1.0:
                    self.scale(self._zoom_factor, self._zoom_factor)

        def wheelEvent(self, event):  # noqa: N802
            """Zoom the meeting room with the mouse wheel."""
            delta = event.angleDelta().y()
            if delta == 0:
                super().wheelEvent(event)
                return
            step = 1.15 if delta > 0 else 1 / 1.15
            self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor * step))
            self.fit_scene()
            event.accept()

        def resizeEvent(self, event):  # noqa: N802
            """Resize event."""
            super().resizeEvent(event)
            self.fit_scene()

        def showEvent(self, event):  # noqa: N802
            """Show event."""
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
    """Create live agent item."""
    class _MacLiveAgentItem(QGraphicsItemGroup):
        """Model mac live agent item."""
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
            """Initialize the mac live agent item instance."""
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

            status_item = QGraphicsTextItem(_mac_status_text(status or "Waiting"))
            status_item.setDefaultTextColor(QColor("#24405f" if active else "#667085"))
            status_item.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold if active else QFont.Weight.Normal))
            status_item.setTextWidth(self.TEXT_WIDTH)
            status_item.setPos(12, 54)
            status_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addToGroup(status_item)

            objective_item = QGraphicsTextItem(objective or t("No current objective"))
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
            """Handle hover enter event for mac live agent item."""
            self.setOpacity(0.88)
            super().hoverEnterEvent(event)

        def hoverLeaveEvent(self, event):  # noqa: N802
            """Handle hover leave event for mac live agent item."""
            self.setOpacity(1.0)
            super().hoverLeaveEvent(event)

        def _in_grip(self, event) -> bool:
            """Handle in grip for mac live agent item."""
            pos = event.pos()
            return pos.x() >= self.WIDTH - self.GRIP and pos.y() >= self.HEIGHT - self.GRIP

        def _emit_geometry(self) -> None:
            """Emit geometry."""
            if self._on_geometry_change is not None:
                pos = self.pos()
                self._on_geometry_change(self._index, pos.x(), pos.y(), self._scale_factor)

        def mousePressEvent(self, event):  # noqa: N802
            """Handle mouse press event for mac live agent item."""
            self._press_scene = event.scenePos()
            if self._in_grip(event):
                self._resizing = True
                event.accept()
                return
            self._resizing = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):  # noqa: N802
            """Handle mouse move event for mac live agent item."""
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
            """Handle mouse release event for mac live agent item."""
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
        """Initialize the mac agent run dialog instance."""
        from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QPainterPath, QPen, QTextCursor
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QDialogButtonBox,
            QFrame,
            QFormLayout,
            QGraphicsEllipseItem,
            QGraphicsItemGroup,
            QGraphicsRectItem,
            QGraphicsScene,
            QGraphicsTextItem,
            QGraphicsView,
            QHBoxLayout,
            QLabel,
            QMessageBox,
            QComboBox,
            QPushButton,
            QSplitter,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )

        self._host = host
        self._spec = dict(spec or {})
        self._paused = False
        self._run_dir = ""
        self._pending_approval_id = ""
        self._QApplication = QApplication
        self._QDesktopServices = QDesktopServices
        self._QTextCursor = QTextCursor
        self._QTimer = QTimer
        self._QUrl = QUrl
        self._QBrush = QBrush
        self._QComboBox = QComboBox
        self._QDialog = QDialog
        self._QDialogButtonBox = QDialogButtonBox
        self._QFormLayout = QFormLayout
        self._QVBoxLayout = QVBoxLayout
        self._QColor = QColor
        self._QFont = QFont
        self._QMessageBox = QMessageBox
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
        self.dialog.setWindowTitle(f"{t('Agent Task')} - {title}")
        self.dialog.setMinimumSize(1100, 700)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(self.dialog)

        root = QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title_label = QLabel(f"<b>{title}</b>")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title_label)

        self.completion_banner = QFrame()
        self.completion_banner.setObjectName("agentCompletionBanner")
        banner_layout = QVBoxLayout(self.completion_banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(2)
        self.completion_banner_title = QLabel(t("Agent Task Running"))
        self.completion_banner_title.setObjectName("agentCompletionBannerTitle")
        self.completion_banner_detail = QLabel(t("The run is still working."))
        self.completion_banner_detail.setWordWrap(True)
        banner_layout.addWidget(self.completion_banner_title)
        banner_layout.addWidget(self.completion_banner_detail)
        self.completion_banner.hide()
        root.addWidget(self.completion_banner)

        self.approval_panel = QFrame()
        self.approval_panel.setStyleSheet(
            "QFrame { background: #fff3d6; border: 1px solid #f59e0b; border-radius: 6px; }"
            "QLabel { color: #3a2500; background: transparent; font-weight: 600; }"
        )
        approval_row = QHBoxLayout(self.approval_panel)
        approval_row.setContentsMargins(10, 8, 10, 8)
        self.approval_label = QLabel()
        self.approval_label.setWordWrap(True)
        approve_btn = QPushButton(t("Approve"))
        decline_btn = QPushButton(t("Decline"))
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
        meeting_header.addWidget(QLabel(t("Meeting")))
        meeting_header.addStretch()
        self.reset_layout_btn = QPushButton(t("Reset Layout"))
        self.reset_layout_btn.setToolTip(t("Restore every agent card to its default position and size"))
        self.reset_layout_btn.clicked.connect(self._reset_agent_layout)
        meeting_header.addWidget(self.reset_layout_btn)
        meeting_layout.addLayout(meeting_header)
        meeting_layout.addWidget(self.meeting_view, 1)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(QLabel(t("Agent Detail")))
        self.agent_summary_view = QTextEdit()
        self.agent_summary_view.setReadOnly(True)
        self.agent_summary_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.agent_summary_view.setMaximumHeight(250)
        self.agent_summary_view.setMinimumWidth(240)
        self.agent_detail_view = self.agent_summary_view
        detail_layout.addWidget(self.agent_summary_view, 1)
        detail_layout.addWidget(QLabel(t("Recent Activity")))
        self.agent_activity_view = QTextEdit()
        self.agent_activity_view.setReadOnly(True)
        self.agent_activity_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        detail_layout.addWidget(self.agent_activity_view, 1)

        board_panel = QWidget()
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(6)
        board_layout.addWidget(QLabel(t("Shared Board")))
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
        self.tabs.addTab(meeting_splitter, t("Meeting Room"))
        self.tabs.addTab(self.log_view, t("Live Log"))
        self.tabs.addTab(self.trace_view, t("Model Trace"))
        self.tabs.addTab(self.final_view, t("Final Report"))
        root.addWidget(self.tabs, 1)
        self._refresh_meeting_room()

        row = QHBoxLayout()
        self.status_label = QLabel(t("Running..."))
        self.open_result_btn = QPushButton(t("Open Run Folder"))
        self.open_result_btn.setEnabled(False)
        self.open_result_btn.clicked.connect(self._open_result_folder)
        self.open_scope_btn = QPushButton(t("Open Scope Folder"))
        self.open_scope_btn.clicked.connect(self._open_scope_folder)
        self.retry_btn = QPushButton(t("Retry"))
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_run)
        self.continue_btn = QPushButton(t("Continue"))
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self._continue_run)
        self.nudge_btn = QPushButton(t("Nudge Agent"))
        self.nudge_btn.clicked.connect(self._send_manual_nudge)
        self.permissions_btn = QPushButton(t("Permissions"))
        self.permissions_btn.clicked.connect(self._edit_live_permissions)
        self.pause_btn = QPushButton(t("Pause After Turn"))
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = QPushButton(t("Cancel"))
        self.cancel_btn.clicked.connect(self._cancel_run)
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.dialog.close)
        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.open_result_btn)
        row.addWidget(self.open_scope_btn)
        row.addWidget(self.retry_btn)
        row.addWidget(self.continue_btn)
        row.addWidget(self.nudge_btn)
        row.addWidget(self.permissions_btn)
        row.addWidget(self.pause_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(close_btn)
        root.addLayout(row)
        localize_widget_tree(self.dialog)

    def show(self) -> None:
        """Show, raise, and focus the agent-run dialog."""
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def close(self) -> None:
        """Close the agent-run dialog."""
        self.dialog.close()

    def append_log(self, data: dict[str, Any]) -> None:
        """Append log."""
        payload = self._payload(data)
        line = str(payload.get("line") or payload.get("text") or payload.get("data") or "")
        if line:
            self._update_live_meeting(line)
            self._append_text(self.log_view, translate_agent_log_line(line))
            if not self._paused:
                self.status_label.setText(t("Running..."))
            self._refresh_meeting_room()

    def append_trace(self, data: dict[str, Any]) -> None:
        """Append trace."""
        payload = self._payload(data)
        entry = str(payload.get("entry") or payload.get("text") or payload.get("data") or "")
        if entry:
            self._append_text(self.trace_view, entry)

    def finish(self, data: dict[str, Any]) -> None:
        """Handle finish for mac agent run dialog."""
        payload = self._payload(data)
        self._run_dir = str(payload.get("run_dir") or self._run_dir or "")
        final_text = str(payload.get("final") or "")
        error_text = str(payload.get("error") or "")
        if final_text:
            self.final_view.setPlainText(final_text)
        elif error_text:
            self.final_view.setPlainText(error_text)
        elif payload.get("cancelled"):
            self.final_view.setPlainText(t("Agent task cancelled."))
        else:
            self.final_view.setPlainText(t("(no final report)"))

        if error_text:
            self.status_label.setText(t("Failed"))
            self._show_completion_banner(t("Agent Task Failed"), error_text, "failed")
            self._set_agent_status(self._active_agent, "Failed", error_text)
        elif payload.get("cancelled"):
            self.status_label.setText(t("Cancelled"))
            self._show_completion_banner(t("Agent Task Cancelled"), t("Agent task cancelled."), "cancelled")
            self._set_agent_status(self._active_agent, "Cancelled", "Agent task cancelled.")
        else:
            self.status_label.setText(t("Finished"))
            self._show_completion_banner(
                t("Agent Task Finished"),
                f"{t('Final report is ready. Log:')} {self._run_dir}" if self._run_dir else t("Final report is ready."),
                "success",
            )
            for name in self._agent_names:
                status = str(self._agent_states.get(name, {}).get("status") or "")
                if status not in {"Done", "Failed", "Cancelled"}:
                    self._set_agent_status(name, "Finished", "Agent run finished.")
        self.approval_panel.hide()
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.nudge_btn.setEnabled(False)
        self.permissions_btn.setEnabled(False)
        self.open_result_btn.setEnabled(bool(self._run_dir))
        self.retry_btn.setEnabled(True)
        self.continue_btn.setEnabled(bool(self._run_dir))
        self.tabs.setCurrentWidget(self.final_view)
        self._refresh_meeting_room()
        self.show()
        self._QApplication.alert(self.dialog, 0)
        notice_title = t("Agent Task Failed") if error_text else t("Agent Task Cancelled") if payload.get("cancelled") else t("Agent Task Finished")
        if hasattr(self._host, "_agent_notify_approval"):
            self._host._agent_notify_approval(notice_title, True, {"run_dir": self._run_dir})

    def _show_completion_banner(self, title: str, detail: str, kind: str) -> None:
        """Show a prominent completion banner at the top of the run window."""
        styles = {
            "success": ("#dcfce7", "#16a34a", "#14532d"),
            "failed": ("#fee2e2", "#dc2626", "#7f1d1d"),
            "cancelled": ("#e5e7eb", "#6b7280", "#111827"),
        }
        bg, border, text = styles.get(kind, styles["success"])
        self.completion_banner.setStyleSheet(
            f"QFrame#agentCompletionBanner {{ background: {bg}; border: 3px solid {border}; border-radius: 8px; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            "QLabel#agentCompletionBannerTitle { font-size: 22px; font-weight: 800; }"
        )
        self.completion_banner_title.setText(title)
        self.completion_banner_detail.setText(detail)
        self.completion_banner.show()

    def request_approval(self, data: dict[str, Any]) -> None:
        """Handle request approval for mac agent run dialog."""
        payload = self._payload(data)
        self._pending_approval_id = str(payload.get("approval_id") or "")
        action = str(payload.get("action") or "approval")
        details = payload.get("details")
        detail_text = ""
        if isinstance(details, dict):
            detail_text = ", ".join(f"{key}={value}" for key, value in details.items())
        if not detail_text:
            detail_text = str(payload.get("detail") or payload.get("reason") or "")
        text = f"{t('Permission needed')}: {action}"
        if detail_text:
            text += f"\n{detail_text}"
        self.approval_label.setText(text)
        self.approval_panel.show()
        self.status_label.setText(t("Permission needed"))
        self._set_agent_status(self._active_agent, "Needs approval", text)
        self._refresh_meeting_room()
        self._host._agent_notify_approval(
            text,
            resolved=False,
            data=payload,
        )
        self.dialog.raise_()
        self._QApplication.alert(self.dialog, 0)

    @staticmethod
    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        """Handle payload for mac agent run dialog."""
        if isinstance(data, dict) and isinstance(data.get("data"), dict) and len(data) == 1:
            return data["data"]
        return data if isinstance(data, dict) else {"data": data}

    def _append_text(self, view, text: str) -> None:
        """Append text."""
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
    def _scroll_snapshot(view) -> tuple[int, bool]:
        """Capture scroll position and whether the view was following the bottom."""
        bar = view.verticalScrollBar()
        value = bar.value()
        return value, value >= bar.maximum() - 4

    @classmethod
    def _set_plain_text_preserving_scroll(cls, view, text: str) -> None:
        """Set text without snapping a user-scrolled view back to the top."""
        old_value, was_at_bottom = cls._scroll_snapshot(view)
        view.setPlainText(text)
        bar = view.verticalScrollBar()
        if was_at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(old_value, bar.maximum()))

    @staticmethod
    def _agent_roles_from_spec(spec: dict[str, Any]) -> dict[str, str]:
        """Handle agent roles from spec for mac agent run dialog."""
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
        """Update live meeting."""
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
            self._paused = True
            self.pause_btn.setText(t("Resume"))
            self.status_label.setText(t("Paused after current turn"))
            return
        if body.startswith("agent run resumed"):
            self._paused = False
            self.pause_btn.setText(t("Pause After Turn"))
            self.status_label.setText(t("Running..."))
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
        """Record meeting message."""
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
        """Ensure agent state."""
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
        """Set agent status."""
        self._ensure_agent_state(name)
        self._agent_states[name]["status"] = status
        self._append_agent_history(name, event)

    def _agent_health(self, name: str) -> dict:
        """Handle agent health for mac agent run dialog."""
        self._ensure_agent_state(name)
        return self._agent_states[name].setdefault(
            "health",
            {"calls": 0, "total_latency": 0.0, "invalid_json": 0, "repairs": 0, "fallbacks": 0},
        )

    def _record_model_latency(self, name: str, body: str) -> None:
        """Record model latency."""
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
        """Handle objective from thought for mac agent run dialog."""
        clean = " ".join(thought.split())
        return MacAgentRunDialog._shorten(clean, 120)

    def _append_agent_history(self, name: str, event: str) -> None:
        """Append agent history."""
        self._ensure_agent_state(name)
        history = self._agent_states[name]["history"]
        history.append(event)
        del history[:-40]

    def _refresh_meeting_room(self) -> None:
        """Refresh meeting room."""
        self._refresh_shared_board()
        self._draw_live_meeting()

    def _draw_live_meeting(self) -> None:
        """Handle draw live meeting for mac agent run dialog."""
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

        title = self.meeting_scene.addText(t("Agent Meeting"), self._QFont("Segoe UI", 11, self._QFont.Weight.DemiBold))
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
                translate_agent_name(name),
                translate_agent_role(str(state.get("role") or "Agent")),
                translate_agent_status(str(state.get("status") or "Waiting")),
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
        """Handle draw last message arrow for mac agent run dialog."""
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
        """Handle draw live arrow for mac agent run dialog."""
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
        """Handle live edge points for mac agent run dialog."""
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
        """Handle live agent positions for mac agent run dialog."""
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
        """Handle select live agent for mac agent run dialog."""
        if 0 <= index < len(self._agent_names):
            self._selected_agent = self._agent_names[index]
            self._draw_live_meeting()

    def _on_agent_geometry_change(self, index: int, x: float, y: float, scale: float) -> None:
        """Handle agent geometry change events."""
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
        """Reset agent layout."""
        if not self._agent_layout:
            return
        self._agent_layout.clear()
        self._draw_live_meeting()

    def _refresh_agent_detail(self) -> None:
        """Refresh agent detail."""
        state = self._agent_states.get(self._selected_agent)
        if not state:
            self.agent_detail_view.clear()
            self.agent_activity_view.clear()
            return
        health = self._health_detail(self._selected_agent)
        detail = (
            f"<h3>{html.escape(translate_agent_name(self._selected_agent))}</h3>"
            f"<p><b>{html.escape(t('Role:'))}</b> {html.escape(translate_agent_role(str(state.get('role') or 'Agent')))}<br>"
            f"<b>{html.escape(t('Status:'))}</b> {html.escape(_mac_status_text(str(state.get('status') or 'Waiting')))}<br>"
            f"<b>{html.escape(t('Last tool:'))}</b> {html.escape(str(state.get('tool') or t('None')))}</p>"
            f"<p><b>{html.escape(t('Current objective'))}</b><br>"
            f"{html.escape(str(state.get('objective') or t('No current objective.')))}</p>"
            f"<p><b>{html.escape(t('Model health'))}</b><br>{html.escape(health)}</p>"
            f"<p><b>{html.escape(t('Latest thought'))}</b><br>"
            f"{html.escape(str(state.get('thought') or t('No thought yet.')))}</p>"
        )
        self.agent_detail_view.setHtml(detail)
        history = state.get("history") or []
        self._set_plain_text_preserving_scroll(
            self.agent_activity_view,
            "\n".join(f"- {translate_agent_activity_text(item)}" for item in history[-18:])
            or f"- {t('No activity yet.')}",
        )

    def _refresh_shared_board(self) -> None:
        """Refresh shared board."""
        if not self._meeting_messages:
            self._set_plain_text_preserving_scroll(self.shared_board_view, t("No messages yet."))
            return
        lines: list[str] = []
        for item in self._meeting_messages:
            lines.append(f"{translate_agent_name(item['from'])} -> {translate_agent_name(item['to'])}")
            lines.append(translate_agent_activity_text(item["message"]))
            lines.append("")
        self._set_plain_text_preserving_scroll(self.shared_board_view, "\n".join(lines).strip())

    def _health_badge(self, name: str) -> str:
        """Handle health badge for mac agent run dialog."""
        return translate_agent_health_badge(self._agent_health(name))

    def _health_detail(self, name: str) -> str:
        """Handle health detail for mac agent run dialog."""
        return translate_agent_health_detail(self._agent_health(name))

    @staticmethod
    def _shorten(text: str, max_chars: int) -> str:
        """Handle shorten for mac agent run dialog."""
        clean = " ".join(text.split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max(0, max_chars - 1)].rstrip() + "..."

    def _resolve_approval(self, approved: bool) -> None:
        """Handle resolve approval for mac agent run dialog."""
        if not self._pending_approval_id:
            return
        approval_id = self._pending_approval_id
        self._pending_approval_id = ""
        self.approval_panel.hide()
        self._host.emit(
            "ui.agent.approval.respond",
            {"approval_id": approval_id, "approved": bool(approved)},
        )

    def _toggle_pause(self) -> None:
        """Pause after the current turn, or resume a paused run."""
        if self._paused:
            self._host.emit("ui.agent.resume_requested", {})
            self._paused = False
            self.pause_btn.setText(t("Pause After Turn"))
            self.status_label.setText(t("Running..."))
        else:
            self._host.emit("ui.agent.pause_requested", {})
            self._paused = True
            self.pause_btn.setText(t("Resume"))
            self.status_label.setText(t("Will pause after current turn"))

    def _send_manual_nudge(self) -> None:
        """Queue a user message for the running agent task."""
        from ui.agent.task_window import AgentNudgeDialog

        dialog = AgentNudgeDialog(self._agent_names + ["ALL"], parent=self.dialog)
        if dialog.exec() != self._QDialog.DialogCode.Accepted or not dialog.nudge:
            return
        target = str(dialog.nudge.get("to") or "ALL")
        message = str(dialog.nudge.get("message") or "").strip()
        if not message:
            return
        self._host.emit("ui.agent.nudge", {"target_agent": target, "message": message})
        self.status_label.setText(f"{t('Nudge queued for')} {target}")
        self._record_meeting_message(f"message: User -> {target}: {message.replace(chr(10), ' ')}")
        self._refresh_meeting_room()

    def _edit_live_permissions(self) -> None:
        """Edit permission modes for the active run."""
        dialog = self._QDialog(self.dialog)
        dialog.setWindowTitle(t("Permission Modes"))
        layout = self._QVBoxLayout(dialog)
        form = self._QFormLayout()
        mode_options = ("auto", "ask permission", "never permit")
        fields = {
            "shell": ("shell_permission_mode", t("Shell")),
            "network": ("network_permission_mode", t("Network")),
            "git": ("git_permission_mode", t("Git")),
            "file_create": ("file_create_permission_mode", t("Create files")),
            "file_edit": ("file_edit_permission_mode", t("Edit files")),
            "file_delete": ("file_delete_permission_mode", t("Delete files")),
        }
        combos: dict[str, Any] = {}
        for category, (spec_key, label) in fields.items():
            combo = self._QComboBox()
            for option in mode_options:
                combo.addItem(t(option), option)
            current = str(self._spec.get(spec_key) or "never permit").strip().lower()
            idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
            combos[category] = combo
            form.addRow(label, combo)
        layout.addLayout(form)
        buttons = self._QDialogButtonBox(
            self._QDialogButtonBox.StandardButton.Cancel | self._QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        localize_widget_tree(dialog)
        if dialog.exec() != self._QDialog.DialogCode.Accepted:
            return
        modes = {category: str(combo.currentData() or "") for category, combo in combos.items()}
        for category, mode in modes.items():
            spec_key = fields[category][0]
            self._spec[spec_key] = mode
            allow_key = {
                "shell": "allow_shell",
                "network": "allow_network",
                "git": "allow_git",
                "file_create": "allow_file_create",
                "file_edit": "allow_file_edit",
                "file_delete": "allow_file_delete",
            }[category]
            self._spec[allow_key] = mode not in {"never", "never permit", "deny"}
        self._host.emit("ui.agent.permissions", {"permission_modes": modes})
        self.status_label.setText(t("Permission changes queued"))

    def _cancel_run(self) -> None:
        """Cancel run."""
        self.status_label.setText(t("Cancelling..."))
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.nudge_btn.setEnabled(False)
        self.permissions_btn.setEnabled(False)
        self._host.emit("ui.agent.cancel_requested", {})

    def _retry_run(self) -> None:
        """Handle retry run for mac agent run dialog."""
        self._host._start_agent_run(self._spec)

    def _continue_run(self) -> None:
        """Handle continue run for mac agent run dialog."""
        if self._run_dir:
            self._host.emit("ui.agent.history.continue", {"run_dir": self._run_dir})

    def _open_result_folder(self) -> None:
        """Open result folder."""
        if self._run_dir:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._run_dir))

    def _open_scope_folder(self) -> None:
        """Open scope folder."""
        scope = str(self._spec.get("scope_folder") or "")
        if scope:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(scope))


class MacAgentHistoryDialog:
    """Protocol-backed agent history browser for the pure-Python target."""

    def __init__(self, host: "QtProtocolHost") -> None:
        """Initialize the mac agent history dialog instance."""
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
        self.dialog.setWindowTitle(t("Agent Task History"))
        self.dialog.setMinimumSize(960, 620)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(self.dialog)

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
        self.tabs.addTab(self.summary_view, t("Summary"))
        self.tabs.addTab(self.log_view, t("Run Log"))
        self.tabs.addTab(self.trace_view, t("Model Trace"))
        self.tabs.addTab(self.diff_view, t("Diff"))
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        row = QHBoxLayout()
        refresh_btn = QPushButton(t("Refresh"))
        refresh_btn.clicked.connect(lambda: self._host.emit("ui.agent.history.refresh", {}))
        open_btn = QPushButton(t("Open Run Folder"))
        open_btn.clicked.connect(self._open_current_run)
        retry_btn = QPushButton(t("Retry"))
        retry_btn.clicked.connect(lambda: self._emit_current("ui.agent.history.retry"))
        continue_btn = QPushButton(t("Continue"))
        continue_btn.clicked.connect(lambda: self._emit_current("ui.agent.history.continue"))
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(self.dialog.close)
        row.addStretch()
        row.addWidget(refresh_btn)
        row.addWidget(open_btn)
        row.addWidget(retry_btn)
        row.addWidget(continue_btn)
        row.addWidget(close_btn)
        root.addLayout(row)
        localize_widget_tree(self.dialog)

    def show(self) -> None:
        """Show, raise, and focus the agent-history dialog."""
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def replace_runs(self, runs: list[dict[str, Any]] | None, runs_root: str = "") -> None:
        """Handle replace runs for mac agent history dialog."""
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
            self._clear_views(t("No agent task runs yet."))

    def update_detail(self, data: dict[str, Any]) -> None:
        """Update detail."""
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
            summary = f"{t('Run folder:')}\n{run_dir}\n\n{t('Error:')}\n{error}"
        else:
            summary = (
                f"{t('Run folder:')}\n{run_dir}\n\n"
                f"{t('Final report:')}\n{final or t('(no final report)')}\n\n"
                f"{t('Task spec:')}\n{task_json or t('(missing task.json)')}"
            )
            if error:
                summary += f"\n\n{t('Error:')}\n{error}"
        self.summary_view.setPlainText(summary)
        self.log_view.setPlainText(str(payload.get("run_log") or ""))
        self.trace_view.setPlainText(str(payload.get("verbose_log") or ""))
        self.diff_view.setPlainText(str(payload.get("diff_patch") or t("(no diff artifact)")))

    def _selected_run_changed(self, current, _previous) -> None:
        """Handle selected run changed for mac agent history dialog."""
        if self._loading or current is None:
            return
        run_dir = str(current.data(self._Qt.ItemDataRole.UserRole) or "")
        self._current_run_dir = run_dir
        if run_dir:
            self._host.emit("ui.agent.history.read", {"run_dir": run_dir})

    def _emit_current(self, event: str) -> None:
        """Emit current."""
        if self._current_run_dir:
            self._host.emit(event, {"run_dir": self._current_run_dir})

    def _open_current_run(self) -> None:
        """Open current run."""
        if self._current_run_dir:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._current_run_dir))
        elif self._runs_root:
            self._QDesktopServices.openUrl(self._QUrl.fromLocalFile(self._runs_root))

    def _clear_views(self, text: str) -> None:
        """Clear views."""
        for view in (self.summary_view, self.log_view, self.trace_view, self.diff_view):
            view.setPlainText(text)

    @staticmethod
    def _display_name(run: dict[str, Any]) -> str:
        """Handle display name for mac agent history dialog."""
        status = str(run.get("status") or "unknown")
        modified = str(run.get("modified_display") or "")
        title = str(run.get("title") or run.get("id") or "Agent run")
        suffix = f" - {modified}" if modified else ""
        return f"{title} [{status}]{suffix}"


def _protect_stdout():
    """Handle protect stdout for runtime workers UI host."""
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


class QtProtocolHost:
    """Model qt protocol host."""
    def __init__(self, app, out) -> None:
        """Initialize the qt protocol host instance."""
        from PySide6.QtCore import QTimer

        self._app = app
        self._out = out
        self._write_lock = threading.Lock()
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._closing = False
        self._stdin_stream = sys.stdin.buffer

        self._overlay_signals = None
        self._overlay = None
        self._intent = None
        self._snip = None
        self._bubble = None
        self._chat = None
        self._memory = None
        self._memory_viewer = None
        self._addons_dialog = None
        self._addon_settings_dialogs: dict[str, Any] = {}
        self._addon_log_dialogs: dict[str, Any] = {}
        self._status_dialogs: list[Any] = []
        self._agent_run_dialog: MacAgentRunDialog | None = None
        self._agent_history_dialog: MacAgentHistoryDialog | None = None
        from core.conversation_store import store as conversation_store
        self._active_project_id = conversation_store.GENERAL_PROJECT_ID
        # Conversation hotkey/voice prompts continue when the intent overlay or
        # chat window selects a target. None means the next prompt starts fresh.
        self._active_conversation_idx: int | None = None
        try:
            self._all_conversations: list[dict] = conversation_store.load_conversations()
        except Exception:
            self._all_conversations = []
        self._apply_memory_project()
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
        """Write a message to the supervisor over the protocol pipe (thread-safe)."""
        with self._write_lock:
            protocol.write_message(self._out, obj)

    def emit(self, event: str, data: Any = None, req_id: Any = None) -> None:
        """Send an event message to the supervisor."""
        self._send(protocol.make_event(event, data=data, req_id=req_id))

    def _respond(self, req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> None:
        """Handle respond for qt protocol host."""
        self._send(protocol.make_response(req_id, ok, result=result, error=error))

    def _read_loop(self) -> None:
        """Read loop."""
        stream = self._stdin_stream
        while True:
            try:
                line = stream.readline()
            except (OSError, ValueError):
                line = b""
            if not line:
                self._lines.put(None)
                return
            self._lines.put(line)

    def _close_stdin_reader(self) -> None:
        """Unblock the stdin reader before Python starts interpreter teardown."""
        try:
            self._stdin_stream.close()
        except Exception:
            pass

    def _quit_after_shutdown(self) -> None:
        """Stop UI IPC and quit the Qt event loop."""
        self._closing = True
        self._pump.stop()
        self._close_stdin_reader()
        self._app.quit()

    def _drain(self) -> None:
        """Handle drain for qt protocol host."""
        self._drain_ticks += 1  # proof the IPC pump fired this tick
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                self._quit_after_shutdown()
                return
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        """Handle line."""
        req_id = None
        try:
            msg = json.loads(raw.decode("utf-8"))
            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "__shutdown__":
                self._respond(req_id, True, result=None)
                self._quit_after_shutdown()
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
        """Handle watchdog status for qt protocol host."""
        started = self._active_dispatch_started
        return {
            "method": self._active_dispatch_method,
            "active_for_seconds": max(0.0, time.monotonic() - started) if started else 0.0,
            "drain_ticks": self._drain_ticks,
            "queue_depth": self._lines.qsize(),
        }

    def _write_slow_dispatch_log(self, method: str, elapsed: float) -> None:
        """Write slow dispatch log."""
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
        """Route an incoming UI request method to its handler and return the result."""
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
        if method == "ui.prewarm_intent":
            return self._prewarm_intent()
        if method == "ui.show_intent":
            return self._show_intent(**params)
        if method == "ui.intent.context_items":
            return self._update_intent_context_items(**params)
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
        if method == "ui.voice.candidates":
            return self._voice_candidates(**params)
        if method == "ui.health.show":
            return self._health_show(**params)
        if method == "ui.privacy.report":
            return self._privacy_report(**params)
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
        if method == "ui.chat.context_preview":
            return self._chat_context_preview(**params)
        if method == "ui.chat.add_conversation":
            return self._chat_add_conversation(**params)
        if method == "ui.chat.begin_conversation":
            return self._chat_begin_conversation(**params)
        if method == "ui.chat.active_history":
            return self._chat_active_history()
        if method == "ui.chat.ingest":
            return self._chat_ingest()
        if method == "ui.live_file.approval.request":
            return self._live_file_approval_request(**params)
        if method == "ui.show_chat":
            return self._show_chat(force_new=bool(params.get("new", False)))
        if method == "ui.show_settings":
            return self._show_settings()
        if method == "ui.show_memory":
            return self._show_memory(**params)
        if method == "ui.show_addons":
            return self._show_addons(**params)
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
        """Handle reload config for qt protocol host."""
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
        """Ensure overlay."""
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
            self._overlay_signals.bubble_stop_requested.connect(
                lambda: self.emit("ui.bubble.stop", {})
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
            self._overlay_signals.show_addon_manager.connect(
                lambda: self.emit("ui.addons.open_requested", {})
            )
            self._overlay_signals.show_agent_task.connect(
                lambda: self.emit("ui.agent.task_requested", {})
            )
            self._overlay_signals.show_agent_history.connect(
                lambda: self.emit("ui.agent.history_requested", {})
            )
            self._overlay_signals.request_setup_check.connect(
                lambda: self.emit("ui.health.requested", {"source": "settings"})
            )
            self._overlay_signals.context_items_dropped.connect(self._context_items_dropped)
            self._overlay_signals.remove_dropped_item.connect(
                lambda idx: self.emit("ui.context.remove", {"index": int(idx)})
            )
            self._overlay_signals.bubble_highlight.connect(self._bubble_highlight)
            self._overlay_signals.settings_applied.connect(self._settings_applied)
        return self._overlay

    def _settings_applied(self) -> None:
        """Handle settings applied for qt protocol host."""
        self._reload_config()
        self.emit("ui.settings.applied", {})

    def _ensure_bubble(self):
        """Ensure bubble."""
        if self._bubble is None:
            overlay = self._ensure_overlay()
            self._bubble = getattr(overlay, "_bubble", None)
            if self._bubble is None:
                from ui.bubble import SpeechBubble

                self._bubble = SpeechBubble()
                self._bubble.set_stop_callback(lambda: self.emit("ui.bubble.stop", {}))
        return self._bubble

    def _show_overlay(self) -> dict[str, Any]:
        """Show overlay."""
        overlay = self._ensure_overlay()
        overlay.show()
        overlay.raise_()
        return {"shown": True}

    def _prewarm_intent(self) -> dict[str, Any]:
        """Warm intent overlay imports and first widget construction."""
        if getattr(self, "_intent_prewarmed", False):
            return {"prewarmed": True, "cached": True}
        try:
            from ui.intent_overlay import IntentOverlay

            if sys.platform == "win32":
                try:
                    import keyboard  # type: ignore  # noqa: F401
                except Exception:
                    log.debug("keyboard prewarm failed", exc_info=True)
            warm = IntentOverlay(caller_idx=0, context_items=[])
            warm.close()
            warm.deleteLater()
            self._intent_prewarmed = True
            return {"prewarmed": True}
        except Exception as exc:
            log.debug("intent prewarm failed", exc_info=True)
            return {"prewarmed": False, "error": str(exc)}

    def _show_intent(
        self,
        caller_idx: int = 0,
        target_hwnd: int = 0,
        context_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Show intent."""
        from ui.intent_overlay import IntentOverlay

        if self._intent is not None:
            self._intent.close()
            self._intent = None
        self._intent = IntentOverlay(
            caller_idx=caller_idx,
            target_hwnd=target_hwnd,
            context_items=_localized_context_items(context_items),
            conversation_options=self._intent_conversation_options(),
            project_options=self._intent_project_options(),
            active_project_id=self._intent_active_project_id(),
        )
        def _chosen(intent: str, custom: str) -> None:
            overlay = self._intent
            project_choice = overlay.project_choice() if overlay else {"mode": "existing", "project_id": self._active_project_id}
            applied_project = self._apply_intent_project_choice(project_choice)
            conversation_choice = overlay.conversation_choice() if overlay else {"mode": "new"}
            applied_choice = self._apply_intent_conversation_choice(conversation_choice)
            self.emit(
                "ui.intent.chosen",
                {
                    "caller_idx": caller_idx,
                    "intent": intent,
                    "custom": custom,
                    "context_choices": overlay.context_choices() if overlay else [],
                    "project_choice": applied_project,
                    "conversation_choice": applied_choice,
                },
            )

        self._intent.intent_chosen.connect(_chosen)
        def _cancelled() -> None:
            self._apply_cancelled_intent_conversation_choice(self._intent)
            self.emit("ui.intent.cancelled", {"caller_idx": caller_idx})

        self._intent.cancelled.connect(_cancelled)
        self._intent.destroyed.connect(lambda: setattr(self, "_intent", None))
        self._intent.show()
        self._intent.raise_()
        if sys.platform != "win32":
            self._intent.activateWindow()
            self._intent.setFocus()
        return {"shown": True, "caller_idx": caller_idx}

    def _apply_cancelled_intent_conversation_choice(self, overlay) -> None:
        """Preserve only explicit chat-target changes from a canceled picker."""
        if overlay is not None and overlay.conversation_choice_touched():
            self._apply_intent_conversation_choice(overlay.conversation_choice())

    def _update_intent_context_items(
        self,
        context_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Refresh context chips on the currently open intent overlay."""
        if self._intent is None:
            return {"updated": False, "reason": "no_intent"}
        try:
            self._intent.update_context_items(_localized_context_items(context_items))
        except RuntimeError:
            self._intent = None
            return {"updated": False, "reason": "closed"}
        return {"updated": True}

    def _show_snip(self) -> dict[str, Any]:
        """Show snip."""
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
        """Handle overlay state for qt protocol host."""
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.set_state.emit(state)
        if state != "idle":
            overlay.show()
            overlay.raise_()
        return {"state": state}

    def _reply_reset(self) -> dict[str, Any]:
        """Handle reply reset for qt protocol host."""
        bubble = self._ensure_bubble()
        bubble.clear()
        return {"reset": True}

    def _reply_thinking(self) -> dict[str, Any]:
        """Handle reply thinking for qt protocol host."""
        self._ensure_bubble().start_thinking()
        return {"thinking": True}

    def _reply_listening(self, text: str = "") -> dict[str, Any]:
        """Handle reply listening for qt protocol host."""
        self._ensure_bubble().show_listening(text or None)
        return {"listening": True}

    def _reply_start_reveal(self) -> dict[str, Any]:
        """Handle reply start reveal for qt protocol host."""
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
        """Handle reply notice for qt protocol host."""
        translated = _translate_notice_text(text)
        self._ensure_bubble().show_notice(translated, timeout_ms=timeout_ms)
        return {"shown": True, "text": translated}

    def _reply_transcript(self, text: str = "") -> dict[str, Any]:
        """Handle reply transcript for qt protocol host."""
        self._ensure_bubble().show_transcript(text)
        return {"shown": bool((text or "").strip()), "text": text}

    def _voice_candidates(
        self,
        text: str = "",
        candidates: list | None = None,
        purpose: str = "voice",
    ) -> dict[str, Any]:
        """Ask the user to accept/edit a voice transcript candidate."""
        from PySide6.QtWidgets import QInputDialog

        choices = [str(item).strip() for item in (candidates or []) if str(item).strip()]
        if not choices and str(text or "").strip():
            choices = [str(text).strip()]
        if not choices:
            return {"accepted": False, "text": ""}
        parent = self._ensure_overlay()
        title = "Dictation transcript" if purpose == "dictation" else "Voice transcript"
        chosen, accepted = QInputDialog.getItem(
            parent,
            title,
            "Choose or edit the transcript:",
            choices,
            0,
            True,
        )
        return {"accepted": bool(accepted), "text": str(chosen or "").strip()}

    def _reply_chunk(
        self,
        text: str = "",
        is_thought: bool = False,
        is_progress: bool = False,
    ) -> dict[str, Any]:
        """Handle reply chunk for qt protocol host."""
        bubble = self._ensure_bubble()
        bubble.append_chunk(text, is_thought=is_thought)
        return {"appended": len(text or ""), "is_progress": bool(is_progress)}

    def _reply_done(self, flush: bool = True) -> dict[str, Any]:
        """Finish the reply bubble.

        flush=True reveals everything immediately (TTS playback ended, errors).
        flush=False lets a running WPM reveal drain at the configured speed â€”
        used when TTS is off, so BUBBLE_REVEAL_WPM is honored instead of the
        whole reply slamming in the moment the LLM finishes streaming.
        """
        bubble = self._ensure_bubble()
        bubble.finish(flush_remaining=bool(flush))
        return {"done": True}

    @staticmethod
    def _context_item_payload(item: Any) -> dict[str, Any]:
        """Handle context item payload for qt protocol host."""
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
        """Handle context items dropped for qt protocol host."""
        payload = [self._context_item_payload(item) for item in (items or [])]
        self.emit("ui.context.dropped", {"items": payload})

    def _context_clear(self) -> dict[str, Any]:
        """Handle context clear for qt protocol host."""
        overlay = self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.drop_context_cleared.emit()
        return {"cleared": True, "overlay": bool(overlay)}

    def _context_add_item(self, name: str = "", item_type: str = "text") -> dict[str, Any]:
        """Handle context add item for qt protocol host."""
        self._ensure_overlay()
        if self._overlay_signals is not None:
            self._overlay_signals.add_context_item.emit(
                _context_display_label(str(name or "Context")), str(item_type or "text")
            )
        return {"added": True}

    def _context_summary(self, items: list | None = None) -> dict[str, Any]:
        """Handle context summary for qt protocol host."""
        self._ensure_overlay()
        if self._overlay_signals is not None:
            pairs = [
                (
                    _context_display_label(str(item.get("label") or item.get("name") or "Context")),
                    str(item.get("type") or "text"),
                )
                for item in (items or [])
                if isinstance(item, dict)
            ]
            self._overlay_signals.show_context_summary.emit(pairs)
        return {"shown": len(items or [])}

    def _bubble_highlight(self, text: str, revealed_count: int, finished: bool) -> None:
        """Handle bubble highlight for qt protocol host."""
        self.emit(
            "ui.bubble.highlight",
            {"text": text, "revealed_count": int(revealed_count), "finished": bool(finished)},
        )

    def _memory_manager(self):
        """Handle memory manager for qt protocol host."""
        if self._memory is None:
            self._memory = MemoryProxy(self.emit)
        return self._memory

    # ------------------------------------------------------------------
    # Projects & conversation persistence
    # ------------------------------------------------------------------

    def _persist_conversations(self) -> None:
        """Handle persist conversations for qt protocol host."""
        try:
            from core.conversation_store import store as conversation_store
            conversation_store.save_conversations(self._all_conversations)
        except Exception:
            log.exception("failed to persist conversations")

    def _apply_memory_project(self) -> None:
        """Apply memory project."""
        try:
            from core.conversation_store import store as conversation_store
            from core.memory_store import store as memory_store
            pid = self._active_project_id
            memory_store.set_active_project(
                None if pid == conversation_store.GENERAL_PROJECT_ID else pid
            )
        except Exception:
            log.exception("failed to apply memory project scope")

    def _set_active_project(self, project_id: str | None) -> None:
        """Set active project."""
        from core.conversation_store import store as conversation_store
        self._active_project_id = project_id or conversation_store.GENERAL_PROJECT_ID
        self._apply_memory_project()

    def _create_project(self, name: str):
        """Create project."""
        try:
            from core.conversation_store import store as conversation_store
            return conversation_store.add_project(name)
        except Exception:
            log.exception("failed to create project %r", name)
            return None

    def _set_active_conversation(self, idx) -> None:
        """Chat window selected/started a conversation -> retarget hotkey prompts."""
        previous_idx = self._active_conversation_idx
        if idx is None or (isinstance(idx, int) and 0 <= idx < len(self._all_conversations)):
            self._active_conversation_idx = idx
        if (
            isinstance(idx, int)
            and idx != previous_idx
            and self._chat is not None
            and not bool(getattr(self._chat, "_streaming", False))
        ):
            title = self._chat_notice_title(idx)
            if title:
                try:
                    self._ensure_bubble().show_notice(f"{t('Continuing')}: {title}", timeout_ms=2500)
                except Exception:
                    log.debug("failed to show chat selection notice", exc_info=True)

    def _chat_notice_title(self, idx: int, limit: int = 48) -> str:
        """Return a compact conversation title for overlay selection notices."""
        if not (0 <= idx < len(self._all_conversations)):
            return ""
        conv = self._all_conversations[idx]
        override = str(conv.get("title_override") or "").strip()
        if override:
            return override[:limit] + ("..." if len(override) > limit else "")
        for msg in conv.get("messages", []):
            if msg.get("role") == "user" and msg.get("content"):
                text = " ".join(str(msg.get("content") or "").split())
                return text[:limit] + ("..." if len(text) > limit else "")
        return t("New chat")

    def _chat_conversation_subtitle(self, idx: int) -> str:
        """Return a short display timestamp for an intent overlay chat option."""
        if not (0 <= idx < len(self._all_conversations)):
            return ""
        conv = self._all_conversations[idx]
        raw = str(conv.get("updated_at") or conv.get("created_at") or "").strip()
        if not raw:
            return ""
        return raw.replace("T", " ").split("+", 1)[0].split(".", 1)[0]

    def _intent_project_options(self) -> list[dict[str, Any]]:
        """Return project options for the intent overlay selector."""
        from core.conversation_store import store as conversation_store

        return conversation_store.load_projects()

    def _intent_active_project_id(self) -> str:
        """Return the project that should be selected when the intent overlay opens."""
        from core.conversation_store import store as conversation_store

        idx = self._active_conversation_idx
        if isinstance(idx, int) and 0 <= idx < len(self._all_conversations):
            return self._all_conversations[idx].get("project_id") or conversation_store.GENERAL_PROJECT_ID
        return self._active_project_id or conversation_store.GENERAL_PROJECT_ID

    def _intent_conversation_options(self, limit: int = 12) -> list[dict[str, Any]]:
        """Return latest-first chat history options for the intent overlay.

        Keep ``limit`` per project so switching projects in the picker does not
        hide older project conversations behind newer chats from other projects.
        """
        if not self._all_conversations:
            return []
        from core.conversation_store import store as conversation_store

        selected_idx = (
            self._active_conversation_idx
            if isinstance(self._active_conversation_idx, int)
            and 0 <= self._active_conversation_idx < len(self._all_conversations)
            else None
        )
        options: list[dict[str, Any]] = []
        seen: set[int] = set()
        counts_by_project: dict[str, int] = {}
        for idx in range(len(self._all_conversations) - 1, -1, -1):
            project_id = self._all_conversations[idx].get("project_id") or conversation_store.GENERAL_PROJECT_ID
            if counts_by_project.get(project_id, 0) >= limit:
                continue
            options.append(
                {
                    "index": idx,
                    "title": self._chat_notice_title(idx, limit=72) or t("Conversation"),
                    "subtitle": self._chat_conversation_subtitle(idx),
                    "project_id": project_id,
                    "selected": selected_idx is not None and idx == selected_idx,
                }
            )
            seen.add(idx)
            counts_by_project[project_id] = counts_by_project.get(project_id, 0) + 1
        if selected_idx is not None and selected_idx not in seen:
            options.append(
                {
                    "index": selected_idx,
                    "title": self._chat_notice_title(selected_idx, limit=72) or t("Conversation"),
                    "subtitle": self._chat_conversation_subtitle(selected_idx),
                    "project_id": self._all_conversations[selected_idx].get("project_id") or conversation_store.GENERAL_PROJECT_ID,
                    "selected": True,
                }
            )
        return options

    def _apply_intent_project_choice(self, choice: dict[str, Any] | None) -> dict[str, Any]:
        """Apply the intent overlay's project selection before prompting."""
        from core.conversation_store import store as conversation_store

        choice = choice or {}
        mode = str(choice.get("mode") or "").strip().lower()
        if mode == "new_project":
            name = str(choice.get("name") or "").strip()
            project = self._create_project(name) if name else None
            project_id = (
                str(project.get("id") or "")
                if isinstance(project, dict)
                else conversation_store.GENERAL_PROJECT_ID
            )
            self._set_active_project(project_id)
            return {"mode": "existing", "project_id": project_id}
        project_id = str(choice.get("project_id") or "").strip() or conversation_store.GENERAL_PROJECT_ID
        valid = {str(project.get("id") or "") for project in conversation_store.load_projects()}
        if project_id not in valid:
            project_id = conversation_store.GENERAL_PROJECT_ID
        self._set_active_project(project_id)
        return {"mode": "existing", "project_id": project_id}

    def _apply_intent_conversation_choice(self, choice: dict[str, Any] | None) -> dict[str, Any]:
        """Apply the intent overlay's new/continue selection before prompting."""
        choice = choice or {}
        if str(choice.get("mode") or "").strip().lower() == "new":
            self._active_conversation_idx = None
            return {"mode": "new"}
        try:
            idx = int(choice.get("index"))
        except (TypeError, ValueError):
            idx = len(self._all_conversations) - 1 if self._all_conversations else -1
        if 0 <= idx < len(self._all_conversations):
            self._active_conversation_idx = idx
            return {"mode": "continue", "index": idx}
        self._active_conversation_idx = None
        return {"mode": "new"}

    def _chat_active_history(self) -> dict[str, Any]:
        """Return prior turns + memory project for the active conversation.

        The supervisor replays ``history`` to the model (full continuation) and
        uses ``project_id`` (None = global) to scope memory in the brain. When
        starting fresh, history is empty and the project is the dropdown's.
        """
        from core.conversation_store import store as conversation_store
        idx = self._active_conversation_idx
        history: list[dict] = []
        context = ""
        if idx is not None and 0 <= idx < len(self._all_conversations):
            conv = self._all_conversations[idx]
            project = conv.get("project_id") or conversation_store.GENERAL_PROJECT_ID
            file_context = list(conv.get("file_context") or [])
            tool_context = self._normalized_tool_context(conv.get("tool_context") or {})
            context_policy = self._normalized_context_policy(conv.get("context_policy") or {})
            context = str(conv.get("context") or "")
            for m in conv.get("messages", []):
                if (
                    m.get("role") not in ("user", "assistant")
                    or not isinstance(m.get("content"), str)
                    or not m.get("content").strip()
                ):
                    continue
                item = {"role": m.get("role"), "content": m.get("content")}
                attachments = conversation_store.normalize_attachments(m.get("attachments"))
                if attachments:
                    item["attachments"] = attachments
                history.append(item)
        else:
            project = self._active_project_id
            file_context = []
            tool_context = {}
            context_policy = {}
        memory_project = None if project == conversation_store.GENERAL_PROJECT_ID else project
        return {
            "history": history,
            "project_id": memory_project,
            "context": context,
            "file_context": file_context,
            "tool_context": tool_context,
            "context_policy": context_policy,
        }

    def _make_chat_send_fn(self):
        """Create chat send fn."""
        def send_with_memory(messages: list, context_policy: dict | None = None):
            """Send with memory."""
            request_id = f"chat-{next(self._chat_request_ids)}"
            stream: "queue.Queue[tuple[str, Any]]" = queue.Queue()
            streamed_text = ""
            with self._chat_streams_lock:
                self._chat_streams[request_id] = stream
            payload = {"request_id": request_id, "messages": messages}
            normalized_policy = self._normalized_context_policy(context_policy or {})
            if normalized_policy:
                payload["context_policy"] = normalized_policy
            idx = self._active_conversation_idx
            if idx is not None and 0 <= idx < len(self._all_conversations):
                if not normalized_policy:
                    stored_policy = self._normalized_context_policy(
                        self._all_conversations[idx].get("context_policy") or {}
                    )
                    if stored_policy:
                        payload["context_policy"] = stored_policy
                tool_context = self._normalized_tool_context(
                    self._all_conversations[idx].get("tool_context") or {}
                )
                if tool_context:
                    payload["tool_context"] = tool_context
            self.emit("ui.chat.request", payload)
            try:
                while True:
                    kind, payload = stream.get()
                    if kind == "chunk":
                        is_thought = False
                        is_progress = False
                        if isinstance(payload, dict):
                            chunk = str(payload.get("text") or "")
                            is_thought = bool(payload.get("is_thought"))
                            is_progress = bool(payload.get("is_progress"))
                        else:
                            chunk = str(payload or "")
                        if not is_thought and not is_progress:
                            streamed_text += chunk
                            yield chunk
                        else:
                            item = {"type": "chunk", "text": chunk}
                            if is_thought:
                                item["is_thought"] = True
                            if is_progress:
                                item["is_progress"] = True
                            yield item
                    elif kind == "done":
                        final_text = ""
                        file_context = []
                        tool_context = {}
                        if isinstance(payload, dict):
                            final_text = str(payload.get("text") or "")
                            file_context = list(payload.get("file_context") or [])
                            tool_context = self._normalized_tool_context(payload.get("tool_context") or {})
                        elif payload is not None:
                            final_text = str(payload)
                        if file_context or tool_context:
                            yield {
                                "type": "metadata",
                                "file_context": file_context,
                                "tool_context": tool_context,
                            }
                        if final_text and final_text != streamed_text:
                            yield {"type": "final", "text": final_text}
                        return
                    elif kind == "error":
                        raise RuntimeError(str(payload or "chat failed"))
            finally:
                with self._chat_streams_lock:
                    self._chat_streams.pop(request_id, None)

        return send_with_memory

    def _chat_stream(self, request_id: str):
        """Handle chat stream for qt protocol host."""
        with self._chat_streams_lock:
            return self._chat_streams.get(str(request_id))

    def _chat_chunk(
        self,
        request_id: str = "",
        text: str = "",
        is_progress: bool = False,
        is_thought: bool = False,
    ) -> dict[str, Any]:
        """Handle chat chunk for qt protocol host."""
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(
                (
                    "chunk",
                    {
                        "text": text,
                        "is_progress": bool(is_progress),
                        "is_thought": bool(is_thought),
                    },
                )
            )
        return {"queued": stream is not None}

    def _chat_done(
        self,
        request_id: str = "",
        text: str = "",
        file_context: list | None = None,
        tool_context: dict | None = None,
    ) -> dict[str, Any]:
        """Handle chat done for qt protocol host."""
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(
                (
                    "done",
                    {
                        "text": text,
                        "file_context": list(file_context or []),
                        "tool_context": self._normalized_tool_context(tool_context or {}),
                    },
                )
            )
        return {"queued": stream is not None}

    def _chat_error(self, request_id: str = "", error: str = "") -> dict[str, Any]:
        """Handle chat error for qt protocol host."""
        stream = self._chat_stream(request_id)
        if stream is not None:
            stream.put(("error", error))
        return {"queued": stream is not None}

    def _live_file_approval_request(self, **params: Any) -> dict[str, Any]:
        """Ask the user to approve a live model file write/edit."""
        action, path, text = _live_file_approval_summary(params)
        state: dict[str, Any] = {"done": False, "approved": False, "feedback": "", "surface": ""}
        loop = None
        chat_resolver = {"fn": None}
        chat_panel = {"shown": False}

        def finish(surface: str, value: bool, feedback: str = "") -> None:
            if state["done"]:
                return
            state["done"] = True
            state["approved"] = bool(value)
            state["feedback"] = str(feedback or "").strip()
            state["surface"] = surface
            if surface != "chat" and callable(chat_resolver["fn"]):
                try:
                    chat_resolver["fn"](bool(value), str(feedback or ""))
                except Exception:
                    traceback.print_exc()
            if surface == "chat":
                try:
                    bubble = self._ensure_bubble()
                    thinking = getattr(bubble, "start_thinking", None)
                    if callable(thinking):
                        thinking()
                except Exception:
                    pass
            if loop is not None:
                loop.quit()

        def show_chat_panel() -> bool:
            try:
                self._show_chat(force_new=False)
            except Exception:
                traceback.print_exc()
            if chat_panel["shown"]:
                return True
            chat = self._chat
            if chat is None or not chat.isVisible():
                return False
            request = getattr(chat, "request_live_file_approval", None)
            if not callable(request):
                return False
            request_params = dict(params)
            request_params["_on_decision"] = lambda result: finish(
                "chat",
                bool((result or {}).get("approved")) if isinstance(result, dict) else False,
                str((result or {}).get("feedback") or "") if isinstance(result, dict) else "",
            )
            request_params["_register_resolver"] = lambda resolver: chat_resolver.update({"fn": resolver})
            result = request(request_params)
            if isinstance(result, dict) and result.get("shown"):
                chat_panel["shown"] = True
                if "approved" in result and result.get("approved"):
                    finish("chat", bool(result.get("approved")), str(result.get("feedback") or ""))
                return True
            return False

        chat_shown = show_chat_panel()
        bubble_result = self._live_file_approval_bubble(
            dict(params),
            text,
            on_decision=lambda value, feedback="": finish("bubble", bool(value), str(feedback or "")),
            on_feedback=show_chat_panel,
        )
        bubble_shown = bool(bubble_result.get("shown"))
        if chat_shown or bubble_shown:
            if not state["done"]:
                from PySide6.QtCore import QEventLoop

                loop = QEventLoop()
                loop.exec()
            return {
                "approved": bool(state["approved"]),
                "feedback": str(state.get("feedback") or "").strip(),
                "surface": str(state.get("surface") or ("chat" if chat_shown else "bubble")),
            }

        if bubble_result.get("shown"):
            return {
                "approved": bool(bubble_result.get("approved")),
                "feedback": str(bubble_result.get("feedback") or "").strip(),
                "surface": "bubble",
            }

        from PySide6.QtWidgets import QMessageBox

        diff = str(params.get("diff") or details.get("diff") or "").strip()

        box = QMessageBox(self._chat or self._overlay)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("Approve this file change?"))
        box.setText(text)
        box.setInformativeText(t("Approve this file change?"))
        if diff:
            box.setDetailedText(diff[:20000])
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        approved = box.exec() == QMessageBox.StandardButton.Yes
        return {"approved": bool(approved), "feedback": "", "surface": "modal"}

    def _live_file_approval_bubble(
        self,
        request: dict[str, Any],
        text: str,
        *,
        on_decision=None,
        on_feedback=None,
    ) -> dict[str, Any]:
        """Ask for live file approval through the overlay bubble actions."""
        state = {"done": False, "approved": False, "feedback": ""}
        loop = None

        def finish(value: bool, feedback: str = "") -> None:
            if state["done"]:
                return
            state["done"] = True
            state["approved"] = bool(value)
            state["feedback"] = str(feedback or "").strip()
            if callable(on_decision):
                on_decision(bool(value), str(feedback or ""))
            if loop is not None:
                loop.quit()

        def request_changes() -> None:
            if callable(on_feedback):
                on_feedback()
                return
            try:
                self._show_chat(force_new=False)
                chat = self._chat
                request_fn = getattr(chat, "request_live_file_approval", None) if chat is not None else None
                if callable(request_fn):
                    result = request_fn(dict(request))
                    if isinstance(result, dict) and result.get("shown"):
                        finish(bool(result.get("approved")), str(result.get("feedback") or ""))
                        return
            except Exception:
                traceback.print_exc()
            finish(False)

        overlay = self._ensure_overlay()
        notify = getattr(overlay, "notify_agent_approval", None)
        if not callable(notify):
            return {"shown": False, "approved": False}
        result = notify(
            text,
            resolved=False,
            on_approve=lambda: finish(True),
            on_feedback=request_changes,
            on_decline=lambda: finish(False),
        )
        if not isinstance(result, dict) or not result.get("actionable"):
            return {"shown": False, "approved": False}
        if not state["done"] and not callable(on_decision):
            from PySide6.QtCore import QEventLoop

            loop = QEventLoop()
            loop.exec()
        return {
            "shown": True,
            "approved": bool(state["approved"]),
            "feedback": str(state.get("feedback") or "").strip(),
        }

    def _chat_add_conversation(
        self,
        user: str = "",
        assistant: str = "",
        context: str = "",
        image_base64: str | None = None,
        attachments: list | None = None,
        file_context: list | None = None,
        tool_context: dict | None = None,
        context_policy: dict | None = None,
        append_user: bool = True,
        conversation_index: int | None = None,
    ) -> dict[str, Any]:
        """Handle chat add conversation for qt protocol host."""
        import uuid as _uuid
        from datetime import datetime, timezone
        from core.conversation_store import store as conversation_store

        now = datetime.now(timezone.utc).isoformat()
        idx = conversation_index if conversation_index is not None else self._active_conversation_idx
        if idx is not None and 0 <= idx < len(self._all_conversations):
            conv_id = str(self._all_conversations[idx].get("id") or _uuid.uuid4())
            self._all_conversations[idx]["id"] = conv_id
        else:
            conv_id = str(_uuid.uuid4())
        user_msg: dict[str, Any] = {
            "id": str(_uuid.uuid4()),
            "role": "user",
            "content": user,
            "created_at": now,
        }
        normalized_attachments = conversation_store.normalize_attachments(attachments or [])
        if image_base64:
            try:
                normalized_attachments.append(
                    conversation_store.save_image_attachment(
                        image_base64,
                        conversation_id=conv_id,
                        message_id=str(user_msg["id"]),
                        source="screenshot",
                        name="screenshot.png",
                    )
                )
            except Exception:
                log.exception("failed to persist chat image attachment")
        normalized_attachments = conversation_store.normalize_attachments(normalized_attachments)
        if normalized_attachments:
            user_msg["attachments"] = normalized_attachments
        context_text = str(context or "").strip()
        if context_text:
            user_msg["context"] = context_text
        assistant_msg = {
            "id": str(_uuid.uuid4()),
            "role": "assistant",
            "content": assistant,
            "created_at": now,
        }
        normalized_file_context = self._normalized_file_context(file_context or [])
        normalized_tool_context = self._normalized_tool_context(tool_context or {})
        if normalized_file_context:
            assistant_msg["file_context"] = normalized_file_context
        if normalized_tool_context:
            assistant_msg["tool_context"] = normalized_tool_context

        if idx is not None and 0 <= idx < len(self._all_conversations):
            # Continue the active conversation (the one selected in the chat window).
            conv = self._all_conversations[idx]
            conv.setdefault("created_at", now)
            conv["updated_at"] = now
            if context_text:
                current_context = str(conv.get("context") or "").strip()
                conv["context"] = f"{current_context}\n\n---\n{context_text}" if current_context else context_text
            messages = conv.setdefault("messages", [])
            if append_user:
                messages.append(user_msg)
            if assistant:
                messages.append(assistant_msg)
            self._merge_file_context(conv, normalized_file_context)
            self._merge_tool_context(conv, normalized_tool_context)
            normalized_policy = self._normalized_context_policy(context_policy or {})
            if normalized_policy:
                conv["context_policy"] = normalized_policy
            self._persist_conversations()
            if self._chat is not None:
                self._chat.sync_conversation(idx)
            return {"count": len(self._all_conversations), "continued": True}

        # No active conversation (fresh start) -> open a new one and make it active.
        self._all_conversations.append(
            {
                "id": conv_id,
                "project_id": self._active_project_id,
                "messages": [msg for msg in (user_msg if append_user else None, assistant_msg if assistant else None) if msg],
                "context": context_text,
                "created_at": now,
                "updated_at": now,
                "file_context": normalized_file_context,
                "tool_context": normalized_tool_context,
                "context_policy": self._normalized_context_policy(context_policy or {}),
            }
        )
        self._active_conversation_idx = len(self._all_conversations) - 1
        self._persist_conversations()
        if self._chat is not None:
            self._chat.ingest_new_conversations(select_new=True)
        return {"count": len(self._all_conversations), "continued": False}

    def _chat_begin_conversation(
        self,
        user: str = "",
        context: str = "",
        image_base64: str | None = None,
        attachments: list | None = None,
        context_policy: dict | None = None,
    ) -> dict[str, Any]:
        """Persist a user chat turn as soon as a prompt is submitted."""
        result = self._chat_add_conversation(
            user=user,
            assistant="",
            context=context,
            image_base64=image_base64,
            attachments=attachments,
            context_policy=context_policy,
            append_user=True,
        )
        idx = self._active_conversation_idx
        return {
            "started": True,
            "conversation_index": idx,
            "continued": bool(result.get("continued")),
            "count": result.get("count"),
        }

    @staticmethod
    def _normalized_file_context(items: list) -> list[dict[str, Any]]:
        """Return compact persisted local-file metadata."""
        out: list[dict[str, Any]] = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            item = {
                "tool": str(raw.get("tool") or ""),
                "path": str(raw.get("path") or ""),
                "relative_path": str(raw.get("relative_path") or ""),
                "root": str(raw.get("root") or ""),
                "ok": bool(raw.get("ok")),
                "message": str(raw.get("message") or ""),
            }
            if item["tool"] and item["path"] and item not in out:
                out.append(item)
        return out[-20:]

    def _merge_file_context(self, conv: dict, items: list) -> None:
        """Merge local-file metadata into a conversation."""
        merged = self._normalized_file_context(list(conv.get("file_context") or []) + list(items or []))
        if merged:
            conv["file_context"] = merged

    @staticmethod
    def _normalized_tool_context(raw: dict) -> dict[str, Any]:
        """Return compact persisted tool policy metadata."""
        if not isinstance(raw, dict):
            return {}

        def _str_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            out: list[str] = []
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
            return out

        mode = str(raw.get("file_access_mode") or "").strip().lower()
        if mode not in {"off", "read", "ask", "auto"}:
            mode = ""
        ctx = {
            "allowed_tools": _str_list(raw.get("allowed_tools")),
            "pinned_tools": _str_list(raw.get("pinned_tools")),
            "file_access_mode": mode,
        }
        if not ctx["allowed_tools"] and not ctx["pinned_tools"] and not ctx["file_access_mode"]:
            return {}
        return ctx

    def _merge_tool_context(self, conv: dict, raw: dict) -> None:
        """Persist the latest tool policy for a conversation."""
        ctx = self._normalized_tool_context(raw)
        if ctx:
            conv["tool_context"] = ctx

    @staticmethod
    def _normalized_context_policy(raw: dict) -> dict[str, Any]:
        """Return compact persisted chat context/tool policy metadata."""
        if not isinstance(raw, dict):
            return {}
        from core.system.env_utils import normalize_file_access_mode
        from runtime.supervisor import tool_modes

        def _mode(value: Any, default: str = "off") -> str:
            mode = str(value or default or "off").strip().lower()
            if mode == "on":
                return "auto"
            return mode if mode in {"off", "auto", "model"} else default

        tools = raw.get("tools")
        policy = {
            "context_ambient": bool(raw.get("context_ambient", False)),
            "context_documents": tool_modes.context_mode(raw, "documents") == "auto",
            "context_tools": False,
            "context_documents_mode": tool_modes.context_mode(raw, "documents"),
            "context_browser_mode": tool_modes.context_mode(raw, "browser"),
            "context_github_mode": tool_modes.context_mode(raw, "github"),
            "context_memory_mode": tool_modes.context_mode(raw, "memory"),
            "context_screenshot": _mode(raw.get("context_screenshot"), "off"),
            "context_clipboard": bool(raw.get("context_clipboard", False)),
            "_context_selection_enabled": bool(raw.get("_context_selection_enabled", False)),
            "file_access": normalize_file_access_mode(raw.get("file_access", "off")),
            "tools": dict(tools) if isinstance(tools, dict) else {},
        }
        policy["context_tools"] = any(
            policy[key] == "model"
            for key in (
                "context_documents_mode",
                "context_browser_mode",
                "context_github_mode",
                "context_memory_mode",
            )
        )
        return policy

    def _chat_ingest(self) -> dict[str, Any]:
        """Handle chat ingest for qt protocol host."""
        if self._chat is not None:
            self._chat.ingest_new_conversations()
            return {"ingested": True}
        return {"ingested": False}

    def _show_chat(self, force_new: bool = False) -> dict[str, Any]:
        """Show chat."""
        from ui.chat_window import ChatWindow

        if self._chat is not None:
            if force_new:
                self._chat.start_new_conversation()
            self._chat.raise_()
            self._chat.activateWindow()
            self._chat.request_context_preview()
            return {"shown": True, "reused": True}
        start_new = force_new or not self._all_conversations
        from core.conversation_store import store as conversation_store
        self._chat = ChatWindow(
            conversations=self._all_conversations,
            send_fn=self._make_chat_send_fn(),
            start_new=start_new,
            projects=conversation_store.load_projects(),
            active_project_id=self._active_project_id,
            on_project_change=self._set_active_project,
            on_new_project=self._create_project,
            persist_fn=self._persist_conversations,
            active_idx=self._active_conversation_idx,
            on_select=self._set_active_conversation,
            on_context_preview=lambda payload: self.emit("ui.chat.context_preview", payload),
        )
        self._chat.destroyed.connect(lambda: setattr(self, "_chat", None))
        self._chat.show()
        self._chat.raise_()
        self._chat.activateWindow()
        return {"shown": True, "reused": False}

    def _chat_context_preview(
        self,
        preview_id: str = "",
        context_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Refresh chat context chip token estimates."""
        if self._chat is None:
            return {"updated": False, "reason": "no_chat"}
        self._chat.update_context_preview(str(preview_id or ""), context_items or [])
        return {"updated": True}

    def _show_settings(self) -> dict[str, Any]:
        """Show settings."""
        from PySide6.QtCore import QTimer
        from ui.settings_panel.dialog import open_settings

        def _open() -> None:
            """Open the Settings dialog on the Qt thread."""
            try:
                open_settings(
                    parent=None,
                    on_apply=self._settings_applied,
                    on_setup_check=lambda: self.emit("ui.health.requested", {"source": "settings"}),
                )
            except Exception:
                traceback.print_exc()

        QTimer.singleShot(0, _open)
        return {"queued": True}

    def _format_status_rows(self, rows: list[dict[str, Any]] | None) -> str:
        """Format health/privacy rows for a compact QMessageBox."""
        lines: list[str] = []
        for row in rows or []:
            status = _status_label(str(row.get("status") or "warn"))
            name = _translate_health_text(str(row.get("name") or "Check"))
            message = _translate_health_text(str(row.get("message") or ""))
            recommendation = _translate_health_text(str(row.get("recommendation") or ""))
            block = f"{status} - {name}\n{message}"
            if recommendation:
                block += f"\n{recommendation}"
            lines.append(block)
        return "\n\n".join(lines) or t("No status details available.")

    def _show_status_dialog(self, title: str, body: str) -> None:
        """Show a non-blocking status dialog and keep it alive until closed."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.setWindowTitle(t(title))
        box.setText(body)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._status_dialogs.append(box)

        def _forget(_result: int, b=box) -> None:
            if b in self._status_dialogs:
                self._status_dialogs.remove(b)

        box.finished.connect(_forget)
        box.open()

    def _health_show(self, rows: list[dict[str, Any]] | None = None, title: str = "") -> dict[str, Any]:
        """Show live setup/health rows in a dismissible window."""
        from PySide6.QtCore import QTimer

        body = self._format_status_rows(rows)

        def _open() -> None:
            self._show_status_dialog(title or "Setup check", body)

        QTimer.singleShot(0, _open)
        return {"queued": True, "count": len(rows or [])}

    def _privacy_report(self, report: dict[str, Any] | None = None, title: str = "") -> dict[str, Any]:
        """Show privacy redaction details using only safe previews."""
        from PySide6.QtCore import QTimer

        report = report or {}
        count = int(report.get("count") or 0)
        items = [item for item in (report.get("items") or []) if isinstance(item, dict)]
        lines = [t("Privacy redaction report"), f"{count} {t('item(s) detected and censored.')}"]
        for item in items[:8]:
            category = _privacy_category_label(str(item.get("category") or "Sensitive data"))
            source = _privacy_source_label(str(item.get("source") or "Context"))
            preview = str(item.get("preview") or item.get("replacement") or "[redacted]")
            lines.append(f"{category} - {source}: {preview}")
        if count > len(items[:8]):
            lines.append(t("Additional redactions were hidden from this compact report."))
        body = "\n".join(lines)

        def _open() -> None:
            self._show_status_dialog(title or "Privacy Report", body)

        QTimer.singleShot(0, _open)
        return {"queued": True, "count": count}

    def _show_memory(self, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Show memory."""
        from PySide6.QtCore import QTimer

        manager = self._memory_manager()
        if hasattr(manager, "replace_facts"):
            manager.replace_facts(facts or [])

        def _open() -> None:
            """Open (or raise) the Memory viewer on the Qt thread."""
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

    def _show_addons(
        self,
        addons: list[dict[str, Any]] | None = None,
        addons_dir: str = "",
    ) -> dict[str, Any]:
        """Show addons."""
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
        reused = self._addons_dialog is not None and self._addons_dialog.isVisible()
        if self._addons_dialog is not None:
            self._addons_dialog.close()
            self._addons_dialog = None

        dialog = QDialog()
        dialog.setWindowTitle(t("Addon Manager"))
        dialog.setModal(False)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(t("Addons"))
        title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel(
            t("Addons are Python packages in the addons/ folder. Each addon runs in its own host process.")
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

        addon_rows = addons or []
        if not addon_rows:
            empty = QLabel(t("No addons found."))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("opacity: 0.55; font-size: 10pt;")
            inner_layout.addWidget(empty)
        else:
            for addon in addon_rows:
                inner_layout.addWidget(self._addon_card(addon))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        if addons_dir:
            open_btn = QPushButton(t("Open addons folder"))
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(addons_dir)))
            footer.addWidget(open_btn)
            install_btn = QPushButton(t("Install archive"))
            install_btn.clicked.connect(lambda: self._install_addon_archive_dialog())
            footer.addWidget(install_btn)
            install_folder_btn = QPushButton(t("Install folder"))
            install_folder_btn.clicked.connect(lambda: self._install_addon_folder_dialog())
            footer.addWidget(install_folder_btn)
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(620, 500)
        self._addons_dialog = dialog
        self._addons_dialog.destroyed.connect(lambda: setattr(self, "_addons_dialog", None))
        localize_widget_tree(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": reused}

    def _install_addon_archive_dialog(self) -> None:
        """Install addon archive dialog."""
        from PySide6.QtWidgets import QFileDialog

        archive, _selected_filter = QFileDialog.getOpenFileName(
            self._addons_dialog,
            t("Install Addon Archive"),
            "",
            t("Wisp Addons (*.wisp *.zip)"),
        )
        if archive:
            self.emit("ui.addons.install_archive", {"path": archive})

    def _install_addon_folder_dialog(self) -> None:
        """Install addon folder dialog."""
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self._addons_dialog,
            t("Install Addon Folder"),
            "",
        )
        if folder:
            self.emit("ui.addons.install_folder", {"path": folder})

    def _addon_card(self, addon: dict[str, Any]):
        """Build an addon card for the Qt protocol host."""
        from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

        card = QFrame()
        card.setObjectName("addonCard")
        card.setStyleSheet(
            "QFrame#addonCard { border: 1px solid #55555f; "
            "border-radius: 8px; padding: 2px; }"
        )
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

        settings_btn = QPushButton(t("Settings"))
        settings_btn.setToolTip(t("Open this addon's settings"))
        settings_btn.clicked.connect(
            lambda _checked=False, a=addon, aid=addon_id, n=name: self._show_addon_settings_dialog(
                aid,
                n,
                a.get("settings") or [],
            )
        )
        name_row.addWidget(settings_btn)

        logs_btn = QPushButton(t("Logs"))
        logs_btn.setToolTip(t("Open this addon's diagnostic log"))
        logs_btn.clicked.connect(
            lambda _checked=False, a=addon, aid=addon_id, n=name: self._show_addon_log_dialog(
                aid,
                n,
                str(a.get("logs") or ""),
            )
        )
        name_row.addWidget(logs_btn)

        runtime = addon.get("runtime") if isinstance(addon.get("runtime"), dict) else {}
        packages = [str(p) for p in (runtime.get("packages") or [])]
        has_dependencies = str(runtime.get("tier") or "1") == "2"
        if has_dependencies:
            repair_btn = QPushButton(self._addon_runtime_action_label(runtime))
            repair_btn.setToolTip(t("Install or rebuild this addon's dependency environment"))
            repair_btn.clicked.connect(
                lambda _checked=False, aid=addon_id, display_name=name, rt=runtime: self._confirm_addon_environment(
                    aid,
                    display_name,
                    rt,
                )
            )
            name_row.addWidget(repair_btn)

        enabled = bool(addon.get("enabled", True))
        enable = QCheckBox(t("Enabled"))
        enable.setChecked(enabled)
        # "discovered" (not yet loaded into a manager) addons can't be toggled live.
        enable.setEnabled(str(addon.get("status") or "") in {"loaded", "disabled", "needs_dependencies", "needs_approval"})
        enable.toggled.connect(
            lambda checked, aid=addon_id: self.emit(
                "ui.addons.set_enabled",
                {"addon_id": aid, "enabled": bool(checked)},
            )
        )
        name_row.addWidget(enable)
        layout.addLayout(name_row)

        path = str(addon.get("path") or "")
        if path:
            path_lbl = QLabel(path)
            path_lbl.setStyleSheet("font-size: 8pt; opacity: 0.45;")
            layout.addWidget(path_lbl)

        hooks = addon.get("hooks") or []
        tools = addon.get("tools") or []
        details = []
        if hooks:
            details.append(t("Hooks: ") + ", ".join(str(h) for h in hooks))
        if tools:
            details.append(t("Tools: ") + ", ".join(str(tool) for tool in tools))
        if details:
            detail_lbl = QLabel("\n".join(details))
            detail_lbl.setWordWrap(True)
            detail_lbl.setStyleSheet("font-size: 8pt; opacity: 0.65;")
            layout.addWidget(detail_lbl)

        if has_dependencies:
            dep_parts = [self._addon_runtime_summary(runtime)]
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
            error_lbl = QLabel(error)
            error_lbl.setWordWrap(True)
            error_lbl.setStyleSheet("font-size: 8pt; color: #b42318;")
            layout.addWidget(error_lbl)
        return card

    def _confirm_addon_environment(self, addon_id: str, display_name: str, runtime: dict[str, Any]) -> None:
        """Confirm installation of an addon's dependency environment."""
        from PySide6.QtWidgets import QMessageBox

        packages = [str(p) for p in (runtime.get("packages") or [])]
        lines = [
            f"{display_name} {t('declares Python/package dependencies.')}",
            "",
            f"{t('Python: ')}{runtime.get('python_requirement') or t('current runtime')}",
            t("Packages:"),
        ]
        lines.extend(f"  {package}" for package in packages)
        if not packages:
            lines.append("  " + t("No packages declared"))
        env_path = str(runtime.get("env_path") or "")
        if env_path:
            lines.extend(["", f"{t('Environment: ')}{env_path}"])
        lines.extend(["", t("Install or rebuild this environment now?")])
        choice = QMessageBox.question(
            self._addons_dialog,
            t("Approve Addon Dependencies"),
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.emit("ui.addons.repair_environment", {"addon_id": addon_id})

    @staticmethod
    def _addon_runtime_action_label(runtime: dict[str, Any]) -> str:
        """Return the dependency action label for an addon."""
        if runtime.get("needs_approval"):
            return t("Approve env")
        return t("Repair env") if runtime.get("ready") else t("Install env")

    @staticmethod
    def _addon_runtime_summary(runtime: dict[str, Any]) -> str:
        """Return a short dependency status summary for an addon."""
        if runtime.get("needs_approval"):
            return t("Dependency env: needs approval")
        return t("Dependency env: ready") if runtime.get("ready") else t("Dependency env: needs install")

    def _show_addon_settings_dialog(self, addon_id: str, display_name: str, settings: list):
        """Show addon settings dialog."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

        existing = self._addon_settings_dialogs.get(addon_id)
        if existing is not None and existing.isVisible():
            existing.close()

        dialog = QDialog(self._addons_dialog)
        dialog.setWindowTitle(f"{display_name} {t('Settings')}")
        dialog.setModal(False)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{display_name} {t('Settings')}")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(8)

        settings_box = self._addon_settings_box(addon_id, settings)
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
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(500, 420)
        dialog.destroyed.connect(lambda _obj=None, key=addon_id: self._addon_settings_dialogs.pop(key, None))
        self._addon_settings_dialogs[addon_id] = dialog
        localize_widget_tree(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _show_addon_log_dialog(self, addon_id: str, display_name: str, logs: str):
        """Show addon log dialog."""
        from PySide6.QtGui import QTextCursor
        from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

        existing = self._addon_log_dialogs.get(addon_id)
        if existing is not None and existing.isVisible():
            text = existing.findChild(QTextEdit)
            if text is not None:
                text.setPlainText(logs or t("No log output yet."))
                text.moveCursor(QTextCursor.MoveOperation.End)
            existing.raise_()
            existing.activateWindow()
            return {"shown": True, "reused": True}

        dialog = QDialog(self._addons_dialog)
        dialog.setWindowTitle(f"{display_name} {t('Logs')}")
        dialog.setModal(False)
        from ui.shared.window_utils import enable_standard_window_controls
        enable_standard_window_controls(dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"{display_name} {t('Logs')}")
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        root.addWidget(title)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        text.setPlainText(logs or t("No log output yet."))
        text.moveCursor(QTextCursor.MoveOperation.End)
        root.addWidget(text, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton(t("Close"))
        close_btn.clicked.connect(dialog.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        dialog.resize(680, 460)
        dialog.destroyed.connect(lambda _obj=None, key=addon_id: self._addon_log_dialogs.pop(key, None))
        self._addon_log_dialogs[addon_id] = dialog
        localize_widget_tree(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return {"shown": True, "reused": False}

    def _addon_settings_box(self, addon_id: str, settings: list):
        """Build the addon settings form."""
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
            """Emit a setting-change event for this addon."""
            self.emit(
                "ui.addons.set_setting",
                {"addon_id": addon_id, "key": key, "value": value},
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
                    w.setPlaceholderText(t("number"))
                w.editingFinished.connect(lambda k=key, e=w: save(k, e.text()))

            help_text = str(s.get("help") or "")
            if help_text:
                w.setToolTip(t(help_text))
            form.addRow(t(label), w)
        return box if form.rowCount() else None

    def _show_agent_task(self, spec: dict[str, Any] | None = None) -> dict[str, Any]:
        """Show agent task."""
        from core.agent.task_spec import agent_task_spec_from_dict
        from ui.agent.task_window import open_agent_task_dialog

        initial_spec = None
        if isinstance(spec, dict) and spec:
            initial_spec = agent_task_spec_from_dict(spec)

        def on_submit(task_spec) -> None:
            """Handle submit events."""
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
        """Start agent run."""
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
        """Show agent history."""
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
        """Handle agent log for qt protocol host."""
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.append_log(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_trace(self, **params: Any) -> dict[str, Any]:
        """Handle agent trace for qt protocol host."""
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.append_trace(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_done(self, **params: Any) -> dict[str, Any]:
        """Handle agent done for qt protocol host."""
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.finish(params)
            return {"accepted": True}
        return {"accepted": False}

    def _agent_approval_request(self, **params: Any) -> dict[str, Any]:
        """Handle agent approval request for qt protocol host."""
        if self._agent_run_dialog is not None:
            self._agent_run_dialog.request_approval(params)
            return {"accepted": True}
        result = self._agent_notify_approval("Agent approval requested.", resolved=False, data=params)
        return {"accepted": bool(result.get("actionable"))}

    def _agent_history_detail(self, **params: Any) -> dict[str, Any]:
        """Handle agent history detail for qt protocol host."""
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
        """Handle agent notify approval for qt protocol host."""
        overlay = self._ensure_overlay()
        notify = getattr(overlay, "notify_agent_approval", None)
        approval_id = str((data or {}).get("approval_id") or "").strip()

        def respond(approved: bool) -> None:
            self.emit(
                "ui.agent.approval.respond",
                {"approval_id": approval_id, "approved": bool(approved)},
            )

        kwargs: dict[str, Any] = {"resolved": bool(resolved)}
        if approval_id and not resolved:
            kwargs["on_approve"] = lambda: respond(True)
            kwargs["on_decline"] = lambda: respond(False)
        if callable(notify):
            result = notify(text or "Agent approval requested.", **kwargs)
            if isinstance(result, dict):
                return {**result, "data": data or {}}
        return {"shown": bool(callable(notify)), "actionable": False, "data": data or {}}


def main() -> int:
    """Handle main for runtime workers UI host."""
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
    app._wisp_runtime_ui_host = host
    host.emit("ui.ready", {"repo": str(root)})
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
