"""Product flow controller for the pure-Python worker target."""

from __future__ import annotations

import base64
import itertools
import logging
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from core.system.env_utils import mcp_server_id_from_tool, mcp_server_override_key
from runtime.supervisor import flow_context, flow_estimates, flow_utils, tool_modes
from ui.i18n import t

log = logging.getLogger("wisp.runtime.flows")
_INTERACTIVE_LLM_TIMEOUT_SECONDS = 120.0
_INTERACTIVE_LLM_TOOL_TIMEOUT_SECONDS = 300.0
_TTS_SEGMENT_MIN_CHARS = 60
_TTS_SEGMENT_MAX_CHARS = 520
_READ_ALOUD_MIN_WORDS = 50
_READ_ALOUD_MAX_WORDS = 110
_READ_ALOUD_PAUSE_RE = re.compile(r"[.!?;:][\"')\]}]*$")
_BROWSER_APP_NAMES = {
    "browser",
    "chrome",
    "chrome.exe",
    "google chrome",
    "firefox",
    "firefox.exe",
    "safari",
    "brave",
    "brave browser",
    "brave.exe",
    "msedge.exe",
    "microsoft edge",
    "opera",
    "vivaldi",
}
_SELECTED_PATH_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml",
    ".yml", ".csv", ".html", ".htm", ".css", ".xml", ".sh", ".bat", ".ps1",
    ".c", ".cpp", ".h", ".java", ".rs", ".go", ".rb", ".php", ".sql",
    ".toml", ".ini", ".cfg", ".conf", ".log",
}
_SELECTED_PATH_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
_SELECTED_PATH_DOCUMENT_EXTS = {".docx", ".pdf", ".xlsx", ".xls", ".pptx", ".odt", ".ods", ".odp"}
_SELECTED_PATH_TEXT_BYTES = 51_200
_AUDIO_CONFIG_KEYS = {
    "TTS_PROVIDER",
    "TTS_SPEAK_REPLIES",
    "CARTESIA_VOICE_ID",
    "ELEVENLABS_VOICE_ID",
    "ELEVENLABS_MODEL",
    "OPENAI_TTS_VOICE",
    "OPENAI_TTS_MODEL",
    "TTS_CUSTOM_BASE_URL",
    "TTS_CUSTOM_VOICE",
    "TTS_CUSTOM_MODEL",
    "TTS_CUSTOM_SAMPLE_RATE",
    "GPT_SOVITS_URL",
    "GPT_SOVITS_REF_AUDIO_PATH",
    "GPT_SOVITS_PROMPT_TEXT",
    "GPT_SOVITS_PROMPT_LANG",
    "GPT_SOVITS_TEXT_LANG",
    "GPT_SOVITS_SAMPLE_RATE",
    "GPT_SOVITS_TEXT_SPLIT_METHOD",
    "GPT_SOVITS_BATCH_SIZE",
    "GPT_SOVITS_SPEED_FACTOR",
    "GPT_SOVITS_SEED",
    "GPT_SOVITS_TIMEOUT_SECONDS",
    "KOKORO_VOICE",
    "KOKORO_LANG_CODE",
    "KOKORO_DEVICE",
    "KOKORO_SPEED",
    "KOKORO_SAMPLE_RATE",
    "KOKORO_SPLIT_PATTERN",
    "TTS_VOLUME",
    "TTS_READ_ALOUD_MIN_WORDS",
    "TTS_READ_ALOUD_MAX_WORDS",
    "STT_MODEL",
    "STT_COMPUTE_TYPE",
    "STT_LANGUAGE",
    "STT_BEAM_SIZE",
    "STT_DEVICE",
    "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS",
    "STT_BACKGROUND_CHUNK_STEP_SECONDS",
    "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS",
    "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
}


_file_context_text = flow_context.file_context_text
_normalized_tool_context = flow_context.normalized_tool_context
_all_context_off_policy = flow_context.all_context_off_policy
_normalized_context_policy = flow_context.normalized_context_policy
json_safe_dumps = flow_utils.json_safe_dumps


class WorkerLike(Protocol):
    """Model worker like."""
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        """Call a method on the worker and return its result."""
        ...

    def on_event(self, event: str, handler) -> None:
        """Handle event events."""
        ...

    def call_with_events(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        on_event: Callable[[str, Any, Any], None],
        on_started: Callable[[Any], None] | None = None,
    ) -> Any:
        """Call with events."""
        ...


@dataclass
class PendingInvocation:
    """Model pending invocation."""
    caller_idx: int = 0
    caller: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    screenshot_b64: str | None = None
    screenshot_tool_b64: str | None = None
    intent_target_pid: int = 0
    paste_target_pid: int = 0
    is_snip: bool = False
    context_ready: threading.Event = field(default_factory=threading.Event)


class _TtsSegmentBuffer:
    """Collect streamed reply text into stable TTS-sized segments."""

    def __init__(
        self,
        *,
        min_chars: int = _TTS_SEGMENT_MIN_CHARS,
        max_chars: int = _TTS_SEGMENT_MAX_CHARS,
    ) -> None:
        self._buffer = ""
        self._min_chars = min_chars
        self._max_chars = max_chars

    def feed(self, text: str) -> list[str]:
        """Add text and return completed speakable segments."""
        if not text:
            return []
        self._buffer += text
        segments: list[str] = []
        while True:
            boundary = self._boundary()
            if boundary is None:
                break
            segment = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if segment:
                segments.append(segment)
        return segments

    def finish(self) -> list[str]:
        """Return any remaining text as the final segment."""
        segment = self._buffer.strip()
        self._buffer = ""
        return [segment] if segment else []

    def _boundary(self) -> int | None:
        """Find the next stable sentence/paragraph/length boundary."""
        text = self._buffer
        paragraph_at = text.find("\n\n")
        if paragraph_at >= self._min_chars // 2:
            return paragraph_at + 2
        for idx, char in enumerate(text):
            if char not in ".!?":
                continue
            boundary = idx + 1
            if boundary < self._min_chars:
                continue
            if boundary == len(text) or text[boundary].isspace():
                return boundary
        if len(text) >= self._max_chars:
            split_at = max(
                text.rfind(" ", self._min_chars, self._max_chars),
                text.rfind("\n", self._min_chars, self._max_chars),
            )
            return split_at if split_at > 0 else self._max_chars
        return None


class FlowController:
    """Wire native/UI events into brain/audio/native product workflows."""

    def __init__(
        self,
        *,
        native: WorkerLike,
        ui: WorkerLike,
        brain: WorkerLike,
        audio: WorkerLike,
        run_async: bool = True,
    ) -> None:
        """Initialize the flow controller instance."""
        self.native = native
        self.ui = ui
        self.brain = brain
        self.audio = audio
        self.run_async = run_async
        self._lock = threading.RLock()
        self._pending: PendingInvocation | None = None
        self._voice_context: dict[str, Any] = {}
        self._voice_screenshot_b64: str | None = None
        self._voice_active = False
        self._voice_state = "idle"
        # Dictation push-to-talk (paste transcript into the focused field).
        self._dictate_state = "idle"
        self._dictate_target_pid = 0
        self._dictate_focus_token = 0
        self._generation = itertools.count(1)
        self._current_generation = 0
        self._context_buffer: list[str] = []
        self._drop_context_items: list[dict[str, Any]] = []
        self._pending_context_capture: dict[str, Any] | None = None
        self._last_reply = ""
        self._last_privacy_report: dict[str, Any] = {}
        self._active_agent_stream_id: Any = None
        self._reply_thought_parser = None
        self._tts_lock = threading.RLock()
        self._tts_generation = 0
        self._tts_queue: "queue.Queue[str | None] | None" = None
        self._tts_sequence_active = False
        self._reply_bubble_cancelled_generation = 0
        self._config_mtime = self._current_config_mtime()

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Subscribe to native and UI worker events to wire up the app flows."""
        self.native.on_event("native.hotkey", self._on_native_hotkey)
        self.ui.on_event("ui.summon_caller", self._on_summon_caller)
        self.ui.on_event("ui.request_snip", self._on_request_snip)
        self.ui.on_event("ui.intent.chosen", self._on_intent_chosen)
        self.ui.on_event("ui.intent.cancelled", self._on_intent_cancelled)
        self.ui.on_event("ui.intent.snip.requested", self._on_intent_snip_requested)
        self.ui.on_event("ui.intent.snip.region", self._on_intent_snip_region)
        self.ui.on_event("ui.intent.snip.cancelled", self._on_intent_snip_cancelled)
        self.ui.on_event("ui.intent.selection.requested", self._on_intent_selection_requested)
        self.ui.on_event("ui.chat.snip.region", self._on_chat_snip_region)
        self.ui.on_event("ui.chat.snip.cancelled", self._on_chat_snip_cancelled)
        self.ui.on_event("ui.chat.selection.requested", self._on_chat_selection_requested)
        self.ui.on_event("ui.snip.region", self._on_snip_region)
        self.ui.on_event("ui.snip.cancelled", self._on_snip_cancelled)
        self.ui.on_event("ui.context.dropped", self._on_context_dropped)
        self.ui.on_event("ui.context.remove", self._on_context_remove)
        self.ui.on_event("ui.chat.request", self._on_chat_request)
        self.ui.on_event("ui.chat.context_preview", self._on_chat_context_preview)
        self.ui.on_event("ui.memory.open_requested", self._on_memory_open_requested)
        self.ui.on_event("ui.memory.add", self._on_memory_add)
        self.ui.on_event("ui.memory.update", self._on_memory_update)
        self.ui.on_event("ui.memory.delete", self._on_memory_delete)
        self.ui.on_event("ui.settings.open_requested", self._on_settings_open_requested)
        self.ui.on_event("ui.addons.open_requested", self._on_addons_open_requested)
        self.ui.on_event("ui.runtime_status.open_requested", self._on_runtime_status_open_requested)
        self.ui.on_event("ui.addons.run_action", self._on_addons_run_action)
        self.ui.on_event("ui.addons.set_enabled", self._on_addons_set_enabled)
        self.ui.on_event("ui.addons.set_setting", self._on_addons_set_setting)
        self.ui.on_event("ui.addons.repair_environment", self._on_addons_repair_environment)
        self.ui.on_event("ui.addons.install_archive", self._on_addons_install_archive)
        self.ui.on_event("ui.addons.install_folder", self._on_addons_install_folder)
        self.ui.on_event("ui.agent.task_requested", self._on_agent_task_requested)
        self.ui.on_event("ui.agent.history_requested", self._on_agent_history_requested)
        self.ui.on_event("ui.agent.run_requested", self._on_agent_run_requested)
        self.ui.on_event("ui.agent.cancel_requested", self._on_agent_cancel_requested)
        self.ui.on_event("ui.agent.pause_requested", self._on_agent_pause_requested)
        self.ui.on_event("ui.agent.resume_requested", self._on_agent_resume_requested)
        self.ui.on_event("ui.agent.nudge", self._on_agent_nudge)
        self.ui.on_event("ui.agent.permissions", self._on_agent_permissions)
        self.ui.on_event("ui.agent.approval.respond", self._on_agent_approval_respond)
        self.ui.on_event("ui.agent.history.refresh", self._on_agent_history_refresh)
        self.ui.on_event("ui.agent.history.read", self._on_agent_history_read)
        self.ui.on_event("ui.agent.history.retry", self._on_agent_history_retry)
        self.ui.on_event("ui.agent.history.continue", self._on_agent_history_continue)
        self.ui.on_event("ui.settings.applied", self._on_settings_applied)
        self.ui.on_event("ui.health.requested", self._on_health_requested)
        self.ui.on_event("ui.bubble.speed", self._on_bubble_speed)
        self.ui.on_event("ui.bubble.stop", self._on_bubble_stop)
        self.brain.on_event("reply.chunk", self._on_reply_chunk)
        self.brain.on_event("reply.done", self._on_reply_done)
        self.brain.on_event("agent.log", self._forward_agent_event("ui.agent.log"))
        self.brain.on_event("agent.trace", self._forward_agent_event("ui.agent.trace"))
        self.brain.on_event("agent.done", self._forward_agent_event("ui.agent.done"))
        self.brain.on_event("agent.approval.request", self._on_agent_approval_request)
        self.audio.on_event("audio.warmup.started", self._on_audio_warmup_started)
        self.audio.on_event("audio.warmup.progress", self._on_audio_warmup_progress)
        self.audio.on_event("audio.warmup.done", self._on_audio_warmup_done)
        self.audio.on_event("audio.playback.started", self._on_audio_playback_started)
        self.audio.on_event("audio.playback.done", self._on_audio_playback_done)
        self.ui.call("ui.show_overlay", timeout=30.0)
        try:
            self.ui.call("ui.prewarm_intent", timeout=30.0, wait=False)
        except Exception:
            log.exception("intent prewarm did not start")
        try:
            self.audio.call("audio.prewarm", timeout=30.0, wait=False)
        except Exception:
            log.exception("audio prewarm did not start")

    def start_hotkeys(self) -> dict[str, Any]:
        """Start hotkeys."""
        addon_hotkeys = self._addon_hotkeys()
        result = self.native.call("native.hotkeys.start", {"addon_hotkeys": addon_hotkeys}, timeout=10.0) or {}
        if not isinstance(result, dict):
            result = {"started": False, "reason": "unexpected native response"}
        if not result.get("started"):
            reason = str(result.get("reason") or result.get("error") or "unknown error")
            log.warning("native hotkeys did not start: %s", reason)
            self._notice("Global hotkeys did not start. Click the Wisp icon to summon it.")
        self._show_addon_notifications()
        return result

    # -- event handlers ------------------------------------------------

    def _on_native_hotkey(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle native hotkey events."""
        kind = (data or {}).get("kind")
        if kind == "caller":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.begin_caller, int((data or {}).get("index") or 0))
        elif kind == "snip":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.begin_snip)
        elif kind == "add_context":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.add_context)
        elif kind == "clear_context":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.clear_context)
        elif kind == "read_selection_aloud":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.read_selection_aloud)
        elif kind == "voice_start":
            if self._claim_voice_start():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.voice_start)
        elif kind == "voice_stop":
            if self._claim_voice_stop():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.voice_stop)
        elif kind == "dictate_start":
            if self._claim_dictate_start():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.dictate_start)
        elif kind == "dictate_stop":
            if self._claim_dictate_stop():
                log.info("hotkey received: kind=%s", kind)
                self._schedule(self.dictate_stop)
        elif kind == "addon":
            log.info("hotkey received: kind=%s", kind)
            self._schedule(self.addon_run_hotkey, data or {})

    def _on_summon_caller(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle summon caller events."""
        self._schedule(self.begin_caller, int((data or {}).get("caller_idx") or 0))

    def _on_request_snip(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle request snip events."""
        self._schedule(self.begin_snip)

    def _on_intent_chosen(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle intent chosen events."""
        prompt = str((data or {}).get("custom") or (data or {}).get("prompt") or "").strip()
        choices = list((data or {}).get("context_choices") or [])
        self._schedule(self.intent_chosen, prompt, choices)

    def _on_intent_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle intent cancelled events."""
        with self._lock:
            pending_capture = dict(self._pending_context_capture or {})
        if pending_capture.get("surface") == "intent":
            return
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            pending.context_ready.set()
        self._new_generation()
        self._set_idle()

    def _on_intent_snip_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle screenshot-chip snip requests from an open intent picker."""
        choices = list((data or {}).get("context_choices") or [])
        custom_text = str((data or {}).get("custom_text") or "")
        self._schedule(self.intent_snip_requested, choices, custom_text)

    def _on_intent_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle a selected screenshot-chip snip region."""
        self._schedule(self.intent_snip_region_selected, data or {})

    def _on_intent_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle cancellation of a screenshot-chip snip."""
        self._schedule(self.intent_snip_cancelled)

    def _on_intent_selection_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle selection capture requests from an open intent picker."""
        choices = list((data or {}).get("context_choices") or [])
        custom_text = str((data or {}).get("custom_text") or "")
        self._schedule(self.intent_selection_capture_requested, choices, custom_text)

    def _on_chat_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle a selected chat screenshot snip region."""
        self._schedule(self.chat_snip_region_selected, data or {})

    def _on_chat_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle cancellation of a chat screenshot snip."""
        self._schedule(self.chat_snip_cancelled)

    def _on_chat_selection_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle selection capture requests from the chat window."""
        self._schedule(self.chat_selection_capture_requested)

    def _on_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle snip region events."""
        self._schedule(self.snip_region_selected, data or {})

    def _on_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle snip cancelled events."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            pending.context_ready.set()
        self._new_generation()
        self._set_idle()

    def _on_context_dropped(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle context dropped events."""
        self._schedule(self.context_items_dropped, list((data or {}).get("items") or []))

    def _on_context_remove(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle context remove events."""
        self._schedule(self.remove_context_item, int((data or {}).get("index") or 0))

    def _on_chat_request(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle chat request events."""
        self._schedule(self.chat_request, data or {})

    def _on_chat_context_preview(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle chat context preview events."""
        self._schedule(self.chat_context_preview, data or {})

    def _on_memory_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory open requested events."""
        self._schedule(self.open_memory)

    def _on_memory_add(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory add events."""
        self._schedule(self.memory_add, data or {})

    def _on_memory_update(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory update events."""
        self._schedule(self.memory_update, data or {})

    def _on_memory_delete(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle memory delete events."""
        self._schedule(self.memory_delete, data or {})

    def _on_settings_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle settings open requested events."""
        self._schedule(self.open_settings)

    def _on_addons_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons open requested events."""
        self._schedule(self.open_addons)

    def _on_runtime_status_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle runtime status open requested events."""
        self._schedule(self.open_runtime_status)

    def _on_addons_run_action(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons run action events."""
        self._schedule(self.addon_run_action, data or {})

    def _on_addons_set_enabled(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons set enabled events."""
        self._schedule(self.addon_set_enabled, data or {})

    def _on_addons_set_setting(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons set setting events."""
        self._schedule(self.addon_set_setting, data or {})

    def _on_addons_repair_environment(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons repair environment events."""
        self._schedule(self.addon_repair_environment, data or {})

    def _on_addons_install_archive(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons install archive events."""
        self._schedule(self.addon_install_archive, data or {})

    def _on_addons_install_folder(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle addons install folder events."""
        self._schedule(self.addon_install_folder, data or {})

    def _on_agent_task_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent task requested events."""
        self._schedule(self.open_agent_task)

    def _on_agent_history_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history requested events."""
        self._schedule(self.open_agent_history)

    def _on_agent_run_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent run requested events."""
        self._schedule(self.run_agent_task, dict((data or {}).get("spec") or {}))

    def _on_agent_cancel_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent cancel requested events."""
        self._schedule(self.cancel_agent_task)

    def _on_agent_pause_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent pause requested events."""
        self._schedule(self.control_agent_task, {"action": "pause"})

    def _on_agent_resume_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent resume requested events."""
        self._schedule(self.control_agent_task, {"action": "resume"})

    def _on_agent_nudge(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent nudge events."""
        payload = data or {}
        self._schedule(
            self.control_agent_task,
            {
                "action": "nudge",
                "target_agent": str(payload.get("target_agent") or payload.get("to") or "ALL"),
                "message": str(payload.get("message") or ""),
            },
        )

    def _on_agent_permissions(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle live agent permission updates."""
        self._schedule(
            self.control_agent_task,
            {"action": "permissions", "permission_modes": dict((data or {}).get("permission_modes") or {})},
        )

    def _on_agent_approval_respond(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent approval respond events."""
        self._schedule(self.respond_agent_approval, data or {})

    def _on_agent_history_refresh(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history refresh events."""
        self._schedule(self.open_agent_history)

    def _on_agent_history_read(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history read events."""
        self._schedule(self.read_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_agent_history_retry(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history retry events."""
        self._schedule(self.retry_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_agent_history_continue(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle agent history continue events."""
        self._schedule(self.continue_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_settings_applied(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle settings applied events."""
        changed_keys = None
        if isinstance(data, dict) and "changed_keys" in data:
            changed_keys = [str(key) for key in (data.get("changed_keys") or [])]
        self._schedule(self.reload_settings, changed_keys)

    def _on_audio_warmup_started(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Tell the user local speech models are warming."""
        items = set((data or {}).get("items") or [])
        if not items:
            return
        if "tts" in items and "stt" in items:
            text = "Warming up local voice and speech recognition..."
        elif "tts" in items:
            text = "Warming up local voice..."
        else:
            text = "Warming up local speech recognition..."
        self._safe_call(self.ui, "ui.reply.notice", {"text": text, "timeout_ms": 0}, timeout=30.0)

    def _on_audio_warmup_progress(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Show which speech component is currently warming or ready."""
        item = str((data or {}).get("item") or "")
        status = str((data or {}).get("status") or "")
        items = set((data or {}).get("items") or [])
        if item not in {"stt", "tts"} or not status:
            return
        label = "speech recognition" if item == "stt" else "local voice"
        if status == "started":
            text = f"Warming up {label}..."
            self._safe_call(self.ui, "ui.reply.notice", {"text": text, "timeout_ms": 0}, timeout=30.0)
            return
        if status.startswith("preparing ") and item == "tts":
            self._safe_call(
                self.ui,
                "ui.reply.notice",
                {
                    "text": t("Preparing local voice... {detail}").format(
                        detail=status.removeprefix("preparing ")
                    ),
                    "timeout_ms": 0,
                },
                timeout=30.0,
            )
            return
        if status.startswith("error:"):
            self._safe_call(
                self.ui,
                "ui.reply.notice",
                {"text": f"Local speech warmup failed: {item}: {status}", "timeout_ms": 6000},
                timeout=30.0,
            )
            return
        if status == "ok" and item == "stt" and "tts" in items:
            text = "Speech recognition is ready. Warming up local voice..."
            self._safe_call(self.ui, "ui.reply.notice", {"text": text, "timeout_ms": 0}, timeout=30.0)

    def _on_audio_warmup_done(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Tell the user local speech warmup finished."""
        items = set((data or {}).get("items") or [])
        if not items:
            return
        result = (data or {}).get("result") if isinstance((data or {}).get("result"), dict) else {}
        failures = [
            f"{name}: {status}"
            for name, status in result.items()
            if str(status).startswith("error:")
        ]
        if failures:
            self._safe_call(
                self.ui,
                "ui.reply.notice",
                {"text": "Local speech warmup failed: " + "; ".join(failures), "timeout_ms": 6000},
                timeout=30.0,
            )
            return
        if "tts" in items and "stt" in items:
            text = "Local voice and speech recognition are ready."
        elif "tts" in items:
            text = "Local voice is ready."
        else:
            text = "Local speech recognition is ready."
        self._safe_call(self.ui, "ui.reply.notice", {"text": text, "timeout_ms": 6000}, timeout=30.0)

    def _on_bubble_speed(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle bubble speed events."""
        self._safe_call(
            self.audio,
            "audio.speed_boost",
            {"enabled": bool((data or {}).get("enabled"))},
            timeout=5.0,
        )

    def _on_bubble_stop(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Stop the visible reply bubble and any speech for the current answer."""
        self._schedule(self.stop_reply_bubble)

    def _on_audio_playback_started(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle audio playback started events."""
        self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.start_reveal", timeout=30.0)

    def _on_audio_playback_done(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle audio playback done events."""
        if self._tts_sequence_is_active():
            return
        self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
        self._set_idle()

    def _on_reply_chunk(self, data: dict[str, Any], _req_id: Any = None) -> list[tuple[str, bool, bool]]:
        """Handle reply chunk events."""
        text = str((data or {}).get("text") or "")
        if not text:
            return []
        is_progress = bool((data or {}).get("is_progress"))
        payload_is_thought = bool((data or {}).get("is_thought"))
        if payload_is_thought:
            self._safe_call(
                self.ui,
                "ui.reply.chunk",
                {
                    "text": text,
                    "is_thought": True,
                    "is_progress": is_progress,
                },
                timeout=30.0,
            )
            return [(text, True, is_progress)]
        parser = self._reply_thought_parser
        if parser is None:
            self._safe_call(
                self.ui,
                "ui.reply.chunk",
                {"text": text, "is_progress": is_progress},
                timeout=30.0,
            )
            return [(text, False, is_progress)]
        segments = list(parser.feed(text))
        for segment, is_thought in segments:
            if segment:
                self._safe_call(
                    self.ui,
                    "ui.reply.chunk",
                    {
                        "text": segment,
                        "is_thought": bool(is_thought),
                        "is_progress": is_progress,
                    },
                    timeout=30.0,
                )
        return [(segment, bool(is_thought), is_progress) for segment, is_thought in segments if segment]

    def _replace_reply_text(self, text: str) -> None:
        """Replace streamed reply chunks with the final assistant text."""
        if not text:
            return
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        try:
            from core.assistant_text import ThoughtStreamParser

            parser = ThoughtStreamParser()
        except Exception:
            self._safe_call(self.ui, "ui.reply.chunk", {"text": text}, timeout=30.0)
            return
        for segment, is_thought in list(parser.feed(text)) + list(parser.finish()):
            if segment:
                self._safe_call(
                    self.ui,
                    "ui.reply.chunk",
                    {"text": segment, "is_thought": bool(is_thought)},
                    timeout=30.0,
                )

    def _on_reply_done(self, data: dict[str, Any], _req_id: Any = None) -> None:
        """Handle reply done events."""
        parser = self._reply_thought_parser
        if parser is not None:
            for segment, is_thought in parser.finish():
                if segment:
                    self._safe_call(
                        self.ui,
                        "ui.reply.chunk",
                        {"text": segment, "is_thought": bool(is_thought)},
                        timeout=30.0,
                    )
            self._reply_thought_parser = None
        text = str((data or {}).get("text") or "")
        if text:
            self._last_reply = text
        # flush=False: the LLM finished streaming but no audio will pace the
        # bubble (this path only runs with TTS off) â€” let the WPM reveal drain
        # at BUBBLE_REVEAL_WPM instead of slamming the full reply in at once.
        self._safe_call(self.ui, "ui.reply.done", {"flush": False}, timeout=30.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "idle"}, timeout=30.0)

    def _forward_agent_event(self, event_name: str):
        """Handle forward agent event for flow controller."""
        def forward(data: Any, _req_id: Any = None) -> None:
            """Handle forward for flow controller."""
            log.debug("%s: %s", event_name, data)
            self._safe_call(self.ui, event_name, {"data": data}, timeout=30.0)

        return forward

    def _on_agent_approval_request(self, data: Any, _req_id: Any = None) -> None:
        """Handle agent approval request events."""
        action = ""
        detail = ""
        if isinstance(data, dict):
            action = str(data.get("action") or "approval")
            detail = str(data.get("detail") or data.get("reason") or "")
        text = f"Agent needs permission: {action}"
        if detail:
            text += f"\n{detail}"
        self._safe_call(
            self.ui,
            "ui.agent.notify_approval",
            {"text": text, "resolved": False, "data": data},
            timeout=30.0,
        )

    # -- public product actions ---------------------------------------

    def begin_caller(self, caller_idx: int = 0) -> None:
        """Handle begin caller for flow controller."""
        import time

        t0 = time.monotonic()
        self._reload_supervisor_config_if_changed()
        caller = self._caller(caller_idx)
        self._log_caller_runtime(caller_idx, caller)
        generation = self._new_generation()
        # Silence any in-progress speech, but don't block the picker waiting for
        # it â€” audio.stop just flips a flag in the audio worker.
        self._fire(self.audio, "audio.stop")
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        initial_context: dict[str, Any] = {}
        try:
            initial_context = self._context_snapshot(
                caller,
                include_browser=False,
                include_selected_paths=True,
                preview_context_sources=True,
            )
        except Exception:
            log.exception("pre-picker context snapshot failed")
            initial_context = {}
        if not self._is_current(generation):
            return
        screenshot_b64 = None
        screenshot_tool_b64 = None
        t_shot0 = time.monotonic()
        if caller.get("context_screenshot") == "auto":
            screenshot_b64 = self._capture_fullscreen_b64()
        elif self._screenshot_tool_allowed(caller):
            screenshot_tool_b64 = self._capture_model_tool_b64()
        t_shot = time.monotonic()
        if not self._is_current(generation):
            return
        pending = PendingInvocation(
            caller_idx=caller_idx,
            caller=caller,
            context=initial_context,
            screenshot_b64=screenshot_b64,
            screenshot_tool_b64=screenshot_tool_b64,
        )
        with self._lock:
            self._pending = pending
        # Capture before showing the picker so Selection still belongs to the
        # user's app. The native clipboard fallback saves and restores clipboard
        # contents, preserving the next paste.
        self._fire(
            self.ui,
            "ui.show_intent",
            {
                "caller_idx": caller_idx,
                "target_hwnd": 0,
                "context_items": self._intent_context_items(pending),
            },
        )
        t_show = time.monotonic()
        self._schedule(self._collect_initial_intent_context, pending, generation, t0, t_show)
        log.info(
            "caller %d picker shown after pre-capture screenshot=%.2fs total=%.2fs",
            caller_idx, t_shot - t_shot0, t_show - t0,
        )

    def begin_snip(self) -> None:
        """Handle begin snip for flow controller."""
        self._new_generation()
        # Show the selector FIRST; it must never wait on audio teardown or
        # cosmetic UI state. Stopping audio and the "listening" animation are
        # fired afterwards without blocking (mirrors begin_caller). Previously the
        # blocking audio.stop call delayed the overlay once the audio worker was
        # busy â€” fast on the first snip, slow on later ones.
        t0 = time.monotonic()
        self.ui.call("ui.show_snip", timeout=30.0)
        log.info("snip: ui.show_snip round-trip %.2fs", time.monotonic() - t0)
        self._fire(self.audio, "audio.stop")
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})

    def stop_reply_bubble(self) -> None:
        """Hide the reply bubble and stop TTS for the current answer."""
        with self._lock:
            generation = self._current_generation
            self._reply_bubble_cancelled_generation = generation
        self._cancel_tts_sequence(generation)
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "idle"}, timeout=30.0)

    def snip_region_selected(self, region: dict[str, Any]) -> None:
        """Handle snip region selected for flow controller."""
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        caller = self._snip_caller()
        with self._lock:
            pending = PendingInvocation(
                caller_idx=0,
                caller=caller,
                context=self._context_snapshot(caller),
                screenshot_b64=screenshot_b64,
                is_snip=True,
            )
            self._pending = pending
        self.ui.call(
            "ui.show_intent",
            {
                "caller_idx": 0,
                "context_items": self._intent_context_items(pending) if pending else [],
            },
            timeout=30.0,
        )

    def intent_snip_requested(
        self,
        context_choices: list[dict[str, Any]] | None = None,
        custom_text: str = "",
    ) -> None:
        """Mark the current intent as waiting for a user-selected screenshot."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            pending.caller = self._apply_intent_context_choices(
                pending.caller,
                context_choices or [],
            )
            pending.caller["context_screenshot"] = "auto"
            pending.caller["_context_screenshot_enabled"] = True
            has_screenshot = bool(pending.screenshot_b64)
            pending.caller["_context_screenshot_requires_snip"] = not has_screenshot
            if not has_screenshot:
                pending.screenshot_b64 = None
            pending.screenshot_tool_b64 = None
            self._pending = pending
            self._pending_context_capture = {
                "surface": "intent",
                "source": "screenshot",
                "custom_text": str(custom_text or ""),
            }

    def intent_snip_region_selected(self, region: dict[str, Any]) -> None:
        """Attach a selected snip to the current pending intent."""
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            capture = dict(self._pending_context_capture or {})
            custom_text = str(capture.get("custom_text") or "")
            if capture.get("source") == "screenshot":
                self._pending_context_capture = None
            pending.screenshot_b64 = screenshot_b64
            pending.screenshot_tool_b64 = None
            pending.caller["context_screenshot"] = "auto" if screenshot_b64 else "off"
            pending.caller["_context_screenshot_enabled"] = bool(screenshot_b64)
            pending.caller["_context_screenshot_requires_snip"] = False
            self._pending = pending
        context_items = self._intent_context_items(pending)
        if not screenshot_b64:
            for item in context_items:
                if item.get("id") == "screenshot":
                    item["force_state"] = True
        self._restore_intent_after_context_capture(pending, custom_text, context_items)

    def intent_snip_cancelled(self) -> None:
        """Return the screenshot chip to Off after a cancelled intent snip."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            capture = dict(self._pending_context_capture or {})
            custom_text = str(capture.get("custom_text") or "")
            if capture.get("source") == "screenshot":
                self._pending_context_capture = None
            pending.screenshot_b64 = None
            pending.screenshot_tool_b64 = None
            pending.caller["context_screenshot"] = "off"
            pending.caller["_context_screenshot_enabled"] = False
            pending.caller["_context_screenshot_requires_snip"] = False
            self._pending = pending
        context_items = self._intent_context_items(pending)
        for item in context_items:
            if item.get("id") == "screenshot":
                item["force_state"] = True
        self._restore_intent_after_context_capture(pending, custom_text, context_items)

    def intent_selection_capture_requested(
        self,
        context_choices: list[dict[str, Any]] | None = None,
        custom_text: str = "",
    ) -> None:
        """Capture selected text or paths for intent after the next user selection."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            pending.caller = self._apply_intent_context_choices(
                pending.caller,
                context_choices or [],
            )
            pending.caller["_context_selection_enabled"] = False
            self._pending = pending
            self._pending_context_capture = {
                "surface": "intent",
                "source": "selection",
                "custom_text": str(custom_text or ""),
            }
            capture = dict(self._pending_context_capture)
        self._notice("Select text or files/folders.")
        self._complete_selection_after_user_selects(capture)

    def chat_selection_capture_requested(self) -> None:
        """Capture selected text or paths for chat after the next user selection."""
        with self._lock:
            self._pending_context_capture = {"surface": "chat", "source": "selection"}
            capture = dict(self._pending_context_capture)
        self._notice("Select text or files/folders.")
        self._complete_selection_after_user_selects(capture)

    def _complete_selection_after_user_selects(self, capture: dict[str, Any]) -> None:
        """Capture Selection automatically after the user finishes selecting."""
        try:
            context = self.native.call(
                "native.context.await_selection",
                {
                    "timeout": 30.0,
                    "settle_ms": 100,
                    "include_clipboard": True,
                    "include_selected_paths": True,
                },
                timeout=35.0,
            ) or {}
        except Exception:
            log.exception("interactive selection capture failed")
            context = {}
        if not context:
            context = self._context_snapshot({"context_clipboard": True}, include_selected_paths=True)
        with self._lock:
            if self._pending_context_capture != capture:
                return
        paths = self._selected_paths_from_context(context)
        selected_text = str(context.get("selected_text") or "").strip()
        clipboard_text = str(context.get("clipboard_text") or "").strip()
        text = selected_text or ("" if paths else clipboard_text)
        self._complete_selection_capture(text, capture, paths)

    def chat_snip_region_selected(self, region: dict[str, Any]) -> None:
        """Attach a selected snip image to the chat composer."""
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        if not screenshot_b64:
            self.chat_snip_cancelled()
            return
        self._safe_call(
            self.ui,
            "ui.chat.capture_context",
            {
                "name": "Screenshot",
                "content": screenshot_b64,
                "item_type": "image",
                "source": "screenshot",
            },
            timeout=30.0,
        )
        self._notice("Screenshot captured.")

    def chat_snip_cancelled(self) -> None:
        """Return the chat Screenshot chip to Off after a cancelled snip."""
        self._safe_call(
            self.ui,
            "ui.chat.capture_cancelled",
            {"source": "screenshot"},
            timeout=30.0,
        )

    def intent_chosen(self, prompt: str, context_choices: list[dict[str, Any]] | None = None) -> None:
        """Handle intent chosen for flow controller."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            pending = PendingInvocation(caller_idx=0, caller=self._caller(0), context=self._context_snapshot({}))
            pending.context_ready.set()
        elif not pending.context_ready.is_set():
            pending.context_ready.wait(timeout=3.0)
        pending.caller = self._apply_intent_context_choices(pending.caller, context_choices or [])
        if not prompt:
            prompt = "What is this?"
        if pending.caller.get("paste_back") and self._is_local_file_request(prompt):
            caller = dict(pending.caller)
            caller["paste_back"] = False
            if tool_modes.local_file_access_mode(caller) in {"off", "read"}:
                caller["file_access"] = "ask"
            pending.caller = caller
            self._query(prompt, pending)
        elif pending.caller.get("paste_back"):
            self._rewrite_and_paste(prompt, pending)
        else:
            self._query(prompt, pending)

    def add_context(self) -> None:
        """Add context."""
        with self._lock:
            pending_capture = dict(self._pending_context_capture or {})
        context = self._context_snapshot(
            {"context_clipboard": True},
            include_selected_paths=pending_capture.get("source") == "selection",
        )
        paths = self._selected_paths_from_context(context)
        selected_text = str(context.get("selected_text") or "").strip()
        clipboard_text = str(context.get("clipboard_text") or "").strip()
        text = selected_text or ("" if paths else clipboard_text)
        if pending_capture.get("source") == "selection":
            self._complete_selection_capture(text, pending_capture, paths)
            return
        if not text:
            self._notice("No selected text or clipboard text to add.")
            return
        # Show the added context as a removable badge to the right of the icon,
        # exactly like a dropped file -- not as a speech-bubble notice. Routing
        # it through _drop_context_items keeps the badge's X-to-remove indexing
        # consistent with remove_context_item.
        name = "Selection"
        self._drop_context_items.append({"name": name, "content": text, "type": "text"})
        self._fire(self.ui, "ui.context.add_item", {"name": name, "item_type": "text"})

    def _complete_selection_capture(
        self,
        text: str,
        capture: dict[str, Any],
        paths: list[str] | None = None,
    ) -> None:
        """Complete a pending interactive selection capture."""
        surface = str(capture.get("surface") or "")
        selected_paths = self._selected_paths_from_context({"selected_paths": paths or []})
        if not text and not selected_paths:
            with self._lock:
                if self._pending_context_capture == capture:
                    self._pending_context_capture = None
            if surface == "intent":
                self._restore_intent_after_selection_capture("", str(capture.get("custom_text") or ""))
            elif surface == "chat":
                self._safe_call(
                    self.ui,
                    "ui.chat.capture_cancelled",
                    {"source": "selection"},
                    timeout=30.0,
                )
            self._notice("No selected text, clipboard text, or selected files found.")
            return

        with self._lock:
            self._pending_context_capture = None
        if surface == "intent":
            self._restore_intent_after_selection_capture(
                text,
                str(capture.get("custom_text") or ""),
                selected_paths,
            )
        elif surface == "chat":
            payload = {
                "name": "Selection",
                "content": text,
                "item_type": "text",
                "source": "selection",
            }
            if selected_paths:
                payload["paths"] = selected_paths
            self._safe_call(self.ui, "ui.chat.capture_context", payload, timeout=30.0)
        else:
            if text:
                self._drop_context_items.append({"name": "Selection", "content": text, "type": "text"})
                self._fire(self.ui, "ui.context.add_item", {"name": "Selection", "item_type": "text"})
            else:
                for item in self._path_context_items(selected_paths):
                    self._drop_context_items.append(item)
                    self._fire(
                        self.ui,
                        "ui.context.add_item",
                        {
                            "name": str(item.get("name") or "Selection"),
                            "item_type": str(item.get("type") or "file"),
                        },
                    )
        self._notice("Selection captured.")

    def _restore_intent_after_selection_capture(
        self,
        text: str,
        custom_text: str = "",
        paths: list[str] | None = None,
    ) -> None:
        """Restore the intent picker after an out-of-band selection capture."""
        selected_paths = self._selected_paths_from_context({"selected_paths": paths or []})
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            if text:
                pending.context["selected_text"] = text
            else:
                pending.context.pop("selected_text", None)
            if selected_paths:
                pending.context["selected_paths"] = selected_paths
            else:
                pending.context.pop("selected_paths", None)
            if text or selected_paths:
                pending.caller["_context_selection_enabled"] = True
            else:
                pending.caller["_context_selection_enabled"] = False
            self._pending = pending
        self._safe_call(
            self.ui,
            "ui.show_intent",
            {
                "caller_idx": pending.caller_idx,
                "target_hwnd": pending.intent_target_pid,
                "context_items": self._intent_context_items(pending),
                "initial_custom_text": custom_text,
                "focus_overlay": True,
            },
            timeout=30.0,
        )

    def _restore_intent_after_context_capture(
        self,
        pending: PendingInvocation,
        custom_text: str = "",
        context_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """Reopen the intent picker after an interactive context capture."""
        self._safe_call(
            self.ui,
            "ui.show_intent",
            {
                "caller_idx": pending.caller_idx,
                "target_hwnd": pending.intent_target_pid,
                "context_items": context_items or self._intent_context_items(pending),
                "initial_custom_text": str(custom_text or ""),
                "focus_overlay": True,
            },
            timeout=30.0,
        )

    def read_selection_aloud(self) -> None:
        """Speak the currently selected text without sending it to a model."""
        if not self._tts_enabled():
            self._notice(t("TTS is off. Choose a voice provider in Settings first."))
            return
        try:
            context = self._context_snapshot({"context_clipboard": False})
        except Exception as exc:  # noqa: BLE001 - keep tray action user-facing
            log.exception("read selection aloud failed to capture context")
            self._notice(f"{t('Could not read selected text')}: {self._friendly_error(exc)}")
            return
        text = str(context.get("selected_text") or "").strip()
        if not text:
            self._notice(t("No selected text to read aloud."))
            return

        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reading", {"text": text}, timeout=30.0)
        if not self._read_aloud_text(text, generation=gen) and not self._reply_bubble_cancelled(gen):
            self._notice(t("Could not read selected text aloud."))

    def clear_context(self) -> None:
        """Clear context."""
        self._context_buffer.clear()
        self._drop_context_items.clear()
        with self._lock:
            self._pending_context_capture = None
        # The panel visibly empties (ui.context.clear), so no bubble notice.
        self._safe_call(self.ui, "ui.context.clear", timeout=30.0)

    def context_items_dropped(self, items: list[dict[str, Any]]) -> None:
        """Handle context items dropped for flow controller."""
        cleaned = [self._normalize_context_item(item) for item in items]
        self._drop_context_items.extend(cleaned)

    def remove_context_item(self, index: int) -> None:
        """Remove context item."""
        if 0 <= index < len(self._drop_context_items):
            self._drop_context_items.pop(index)

    def voice_start(self) -> None:
        """Handle voice start for flow controller."""
        # Acknowledge the keypress instantly with the listening icon, before any
        # config reload or setup work, so holding the hotkey gives immediate
        # visual feedback rather than waiting on the steps below.
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        self._reload_supervisor_config_if_changed()
        self._ensure_voice_start_claimed()
        self._new_generation()
        caller = self._voice_caller()
        self._voice_context = {}
        self._voice_screenshot_b64 = None
        self._fire(self.audio, "audio.stop")
        try:
            record_result = self.audio.call("audio.record.start", timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("voice record start failed")
            self._notice(f"Couldn't start recording: {self._friendly_error(exc)}")
            self._mark_voice_idle()
            self._set_idle()
            return
        if isinstance(record_result, dict) and record_result.get("recording") is False:
            self._mark_voice_idle()
            self._set_idle()
            return
        if not self._mark_voice_recording():
            return
        self._fire(self.ui, "ui.reply.listening")
        # include_browser=False keeps a slow page fetch off the record-start
        # path; _brain_query_params fetches it lazily at query time instead.
        self._voice_context = self._context_snapshot(caller, include_browser=False)
        # Capture AFTER recording starts so the screenshot overlaps the speech
        # instead of delaying the record start.
        if caller.get("context_screenshot") == "auto":
            self._voice_screenshot_b64 = self._capture_fullscreen_b64()

    def voice_stop(self) -> None:
        """Handle voice stop for flow controller."""
        if not self._ensure_voice_stop_claimed():
            return
        self._fire(self.ui, "ui.overlay.state", {"state": "thinking"})
        # The first transcription after launch blocks on the (slow) model load /
        # warmup. Tell the user that's what's happening instead of leaving them
        # staring at the generic "thinking" dots wondering why it's slow.
        if self._stt_warming():
            self._fire(self.ui, "ui.reply.notice",
                       {"text": "Warming up speech model â€” the first transcription is slowerâ€¦"})
        else:
            self._fire(self.ui, "ui.reply.thinking")
        try:
            result = self.audio.call("audio.record.stop_transcribe", timeout=180.0)
            text = str((result or {}).get("text") or "").strip()
            if not text:
                # Empty transcript = the clip was too short/quiet or had no speech
                # (a too-brief F8 tap is the usual cause). Tell the user how to
                # hold the key instead of silently resetting and leaving them
                # wondering why nothing happened.
                self._notice("Didn't catch any speech. Hold the key down while you speak, then release.")
                self._set_idle()
                return
            text = self._confirm_voice_transcript(text, purpose="voice")
            if not text:
                self._fire(self.ui, "ui.reply.reset")
                self._set_idle()
                return
            pending = PendingInvocation(
                caller_idx=0,
                caller=self._voice_caller(),
                context=self._voice_context,
                screenshot_b64=self._voice_screenshot_b64,
            )
            self._voice_screenshot_b64 = None
            if self._voice_review_transcript_enabled():
                pending.context_ready.set()
                self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
                with self._lock:
                    self._pending = pending
                self._safe_call(
                    self.ui,
                    "ui.show_intent",
                    {
                        "caller_idx": 0,
                        "target_hwnd": 0,
                        "context_items": self._intent_context_items(pending),
                        "initial_custom_text": text,
                        "focus_overlay": True,
                    },
                    timeout=30.0,
                )
                self._set_idle()
                return
            self._safe_call(self.ui, "ui.reply.transcript", {"text": text}, timeout=30.0)
            self._mark_voice_idle()
            self._query(text, pending, preserve_reply_bubble=True)
        finally:
            self._mark_voice_idle()

    def dictate_start(self) -> None:
        """Push-to-talk dictation: capture the focused text field (so the result
        can be pasted back in place), then start recording."""
        self._reload_supervisor_config_if_changed()
        # Capture focus now, while the user's app is still frontmost. paste_back=True
        # makes the snapshot grab the focused text element / window handle.
        context = self._context_snapshot(
            {"paste_back": True, "context_clipboard": False}, include_browser=False
        )
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if str(context.get("platform") or "") == "darwin":
            self._dictate_target_pid = int(active_app.get("pid") or 0)
        else:
            self._dictate_target_pid = int(active_app.get("window_id") or active_app.get("pid") or 0)
        self._dictate_focus_token = int(context.get("focus_token") or 0)
        self._fire(self.audio, "audio.stop")
        try:
            record_result = self.audio.call("audio.record.start", timeout=20.0)
        except Exception as exc:  # noqa: BLE001 â€” surface mic/worker failure in the UI
            log.exception("dictation record start failed")
            self._notice(f"Couldn't start dictation: {self._friendly_error(exc)}")
            self._mark_dictate_idle()
            self._set_idle()
            return
        if isinstance(record_result, dict) and record_result.get("recording") is False:
            self._mark_dictate_idle()
            self._set_idle()
            return
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        self._fire(self.ui, "ui.reply.listening")

    def dictate_stop(self) -> None:
        """Stop dictation, transcribe, optionally LLM-clean, and paste into the
        text field that was focused when recording started."""
        try:
            try:
                result = self.audio.call("audio.record.stop_transcribe", timeout=180.0)
            except Exception as exc:  # noqa: BLE001 â€” surface transcribe failure in the UI
                log.exception("dictation transcribe failed")
                self._notice(f"Dictation failed: {self._friendly_error(exc)}")
                self._set_idle()
                return
            text = str((result or {}).get("text") or "").strip()
            if not text:
                self._notice("Didn't catch any speech. Hold the key down while you speak, then release.")
                self._set_idle()
                return
            text = self._confirm_voice_transcript(text, purpose="dictation")
            if not text:
                self._set_idle()
                return
            import config
            if str(getattr(config, "DICTATE_MODE", "raw")).lower() == "llm":
                text = self._dictation_cleanup(text)
            self._paste_dictation(text)
        finally:
            self._mark_dictate_idle()

    def _dictation_cleanup(self, text: str) -> str:
        """Run the raw transcript through the LLM for punctuation/cleanup. Any
        failure falls back to the raw text so dictation always pastes something."""
        try:
            result = self._brain_call_with_events(
                "brain.rewrite",
                {
                    "selected_text": text,
                    "intent_prompt": (
                        "This is a raw speech-to-text dictation. Fix punctuation, "
                        "capitalization, and obvious transcription slips, and remove "
                        "filler words. Output ONLY the cleaned text, nothing else."
                    ),
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=lambda *_a, **_k: None,
            )
            return str((result or {}).get("text") or "").strip() or text
        except Exception:  # noqa: BLE001 â€” never block a paste on cleanup
            log.exception("dictation LLM cleanup failed; pasting raw transcript")
            return text

    @staticmethod
    def _voice_transcript_candidates(text: str) -> list[str]:
        """Return cheap transcript candidates without rerunning STT."""
        raw = " ".join(str(text or "").split())
        if not raw:
            return []
        candidates = [raw]
        polished = raw[:1].upper() + raw[1:]
        if polished and polished[-1] not in ".!?":
            polished += "."
        if polished not in candidates:
            candidates.append(polished)
        command_like = raw.rstrip(".!?")
        if command_like.lower().startswith(("can you ", "please ")):
            command_like = command_like[:1].upper() + command_like[1:]
            if command_like not in candidates:
                candidates.append(command_like)
        return candidates[:3]

    def _confirm_voice_transcript(self, text: str, *, purpose: str) -> str:
        """Optionally ask the user to choose/edit the transcript before use."""
        import config

        if not bool(getattr(config, "VOICE_TRANSCRIPT_CONFIRM", False)):
            return text
        result = self._safe_call(
            self.ui,
            "ui.voice.candidates",
            {
                "text": text,
                "candidates": self._voice_transcript_candidates(text),
                "purpose": purpose,
            },
            timeout=300.0,
        ) or {}
        if not isinstance(result, dict) or not result.get("accepted"):
            return ""
        return str(result.get("text") or "").strip()

    def _paste_dictation(self, text: str) -> None:
        """Paste dictation."""
        paste = self.native.call(
            "native.paste_text",
            {
                "text": text,
                "target_pid": self._dictate_target_pid,
                "focus_token": self._dictate_focus_token,
            },
            timeout=30.0,
        )
        paste = paste if isinstance(paste, dict) else {}
        log.info("dictation paste: target_pid=%s result=%s", self._dictate_target_pid, paste)
        self._set_idle()
        if paste.get("ok"):
            return  # silent success â€” the pasted text is the confirmation
        if paste.get("clipboard_ok"):
            self._native_notify(
                "Wisp â€” dictation on clipboard",
                f"Couldn't focus the field. Press {self._paste_shortcut()} to paste.",
            )
        else:
            log.error("dictation paste failed: %s", paste.get("error") or paste)
            self._native_notify("Wisp â€” dictation failed", "Couldn't paste the text. See native.stderr.log.")

    def reload_settings(self, changed_keys: list[str] | None = None) -> None:
        """Handle reload settings for flow controller."""
        import config

        config.reload()
        self._config_mtime = self._current_config_mtime()
        log.info("supervisor config reloaded")
        self._safe_call(self.brain, "brain.config.reload", timeout=30.0)
        # The audio worker owns the live TTS path and is long-lived, so it must
        # reload config + drop cached TTS connections here â€” prewarm alone leaves
        # the old provider/voice in effect until restart.
        audio_changed = changed_keys is None or any(key in _AUDIO_CONFIG_KEYS for key in changed_keys)
        if audio_changed:
            self._safe_call(self.audio, "audio.config.reload", timeout=30.0)
        else:
            log.info("audio config reload skipped; changed settings did not affect audio")
        # The native worker is a separate long-lived process and owns global
        # registrations. Replace hotkeys in one native call so Apply cannot
        # leave an old listener referenced between stop/start requests.
        result = self._safe_call(
            self.native,
            "native.hotkeys.reload",
            {"addon_hotkeys": self._addon_hotkeys()},
            timeout=10.0,
        ) or {}
        if isinstance(result, dict) and not result.get("started"):
            self._notice("Global hotkeys did not start. Click the Wisp icon to summon it.")

    def _on_health_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        from core.setup_check import run_setup_check

        rows = list(run_setup_check())
        self._safe_call(self.ui, "ui.health.show", {"rows": rows, "title": "Setup check"}, timeout=5.0)
        warnings = [row for row in rows if row.get("status") in {"warn", "fail"}]
        if warnings:
            first = warnings[0]
            self._safe_call(
                self.ui,
                "ui.reply.notice",
                {
                    "text": f"Health issue: {first.get('name')}: {first.get('message')}",
                    "timeout_ms": 8000,
                },
                timeout=30.0,
            )

    def chat_request(self, data: dict[str, Any]) -> None:
        """Handle chat request for flow controller."""
        self._reload_supervisor_config_if_changed()
        request_id = str(data.get("request_id") or "")
        messages = data.get("messages") or []
        if not request_id:
            return

        done_seen = False

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            nonlocal done_seen
            if event == "reply.chunk":
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {
                        "request_id": request_id,
                        "text": str((payload or {}).get("text") or ""),
                        "is_progress": bool((payload or {}).get("is_progress")),
                        "is_thought": bool((payload or {}).get("is_thought")),
                    },
                    timeout=30.0,
                )
            elif event == "reply.done":
                done_seen = True
                self._emit_file_context_progress(
                    list((payload or {}).get("file_context") or []),
                    chat_request_id=request_id,
                    include_bubble=False,
                )
                self._safe_call(
                    self.ui,
                    "ui.chat.done",
                    {
                        "request_id": request_id,
                        "text": str((payload or {}).get("text") or ""),
                        "file_context": list((payload or {}).get("file_context") or []),
                        "tool_context": tool_context,
                        "context_snippets": context_snippets,
                    },
                    timeout=30.0,
                )
            elif event == "live_file.approval.request":
                self._handle_live_file_approval_request(payload)

        try:
            caller_idx = int(data.get("caller_idx", 0) or 0)
        except (TypeError, ValueError):
            caller_idx = 0
        supplied_policy = _normalized_context_policy(data.get("context_policy"))
        caller = supplied_policy or self._caller(caller_idx) or _all_context_off_policy()
        allowed_tools, pinned_tools, file_access_mode = self._chat_tool_policy(caller)
        stored_tool_context = _normalized_tool_context(data.get("tool_context"))
        if stored_tool_context and not supplied_policy:
            allowed_tools = list(stored_tool_context.get("allowed_tools") or allowed_tools)
            pinned_tools = list(stored_tool_context.get("pinned_tools") or pinned_tools)
            file_access_mode = str(stored_tool_context.get("file_access_mode") or file_access_mode)
        if self._screenshot_tool_allowed(caller) and "capture_screen" not in allowed_tools:
            allowed_tools.append("capture_screen")
            if "capture_screen" not in pinned_tools:
                pinned_tools.append("capture_screen")
        tool_context = {
            "allowed_tools": list(allowed_tools),
            "pinned_tools": list(pinned_tools),
            "file_access_mode": file_access_mode,
        }
        context_parts = self._chat_context_parts(caller)
        messages = self._messages_with_chat_context(messages, caller, context_parts)
        context_snippets: list[dict[str, str]] = []
        for label, _block, preview_source in context_parts:
            preview = self._context_preview_text(preview_source)
            if preview:
                context_snippets.append({"label": label, "preview": preview})
        chat_params: dict[str, Any] = {
            "messages": messages,
            "memory_enabled": self._context_mode(caller, "memory") == "on",
            "use_tools": bool(allowed_tools),
            "allowed_tools": allowed_tools,
            "pinned_tools": pinned_tools,
            "file_access_mode": file_access_mode,
        }
        try:
            hist = self._safe_call(self.ui, "ui.chat.active_history", {}, timeout=10.0)
            if isinstance(hist, dict):
                chat_params["memory_project"] = hist.get("project_id")
        except Exception:
            log.exception("failed to fetch active project for chat")

        try:
            result = self._brain_call_with_events(
                "brain.chat",
                chat_params,
                timeout=self._interactive_llm_timeout_seconds(chat_params),
                on_event=on_event,
            )
            if not done_seen:
                text = str((result or {}).get("text") or "")
                self._emit_file_context_progress(
                    list((result or {}).get("file_context") or []),
                    chat_request_id=request_id,
                    include_bubble=False,
                )
                if text:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {"request_id": request_id, "text": text},
                        timeout=30.0,
                    )
                self._safe_call(
                    self.ui,
                    "ui.chat.done",
                    {
                        "request_id": request_id,
                        "text": text,
                        "file_context": list((result or {}).get("file_context") or []),
                        "tool_context": tool_context,
                        "context_snippets": context_snippets,
                    },
                    timeout=30.0,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("chat request failed")
            self._safe_call(
                self.ui,
                "ui.chat.error",
                {"request_id": request_id, "error": f"{type(exc).__name__}: {exc}"},
                timeout=30.0,
            )

    def chat_context_preview(self, data: dict[str, Any]) -> None:
        """Refresh chat-window context chip token estimates before send."""
        self._reload_supervisor_config_if_changed()
        preview_id = str(data.get("preview_id") or "")
        if not preview_id:
            return
        try:
            caller_idx = int(data.get("caller_idx", 0) or 0)
        except (TypeError, ValueError):
            caller_idx = 0
        caller = _normalized_context_policy(data.get("context_policy")) or self._caller(caller_idx) or _all_context_off_policy()
        try:
            context = self._context_snapshot(
                caller,
                include_browser=False,
                preview_context_sources=True,
            )
        except Exception:
            log.exception("chat context preview snapshot failed")
            context = {}
        pending = PendingInvocation(caller_idx=caller_idx, caller=caller, context=context)
        self._safe_call(
            self.ui,
            "ui.chat.context_preview",
            {"preview_id": preview_id, "context_items": self._intent_context_items(pending)},
            timeout=30.0,
        )
        changed = False
        if self._effective_document_mode(caller) in {"auto", "model"} and not context.get("active_document_text"):
            text = self._fetch_active_document_text(context)
            if text:
                context["active_document_text"] = text
                changed = True
        if self._context_mode(caller, "browser") == "auto" and not context.get("browser_content"):
            browser = self._fetch_browser_content_for_context(context)
            if browser.get("browser_url") and not context.get("browser_url"):
                context["browser_url"] = browser["browser_url"]
                changed = True
            if browser.get("browser_content"):
                context["browser_content"] = browser["browser_content"]
                changed = True
        if changed:
            pending.context = context
            self._safe_call(
                self.ui,
                "ui.chat.context_preview",
                {"preview_id": preview_id, "context_items": self._intent_context_items(pending)},
                timeout=30.0,
            )

    def open_memory(self) -> None:
        """Open memory."""
        result = self._safe_call(self.brain, "brain.memory.list", timeout=30.0) or {}
        facts = result.get("facts") if isinstance(result, dict) else []
        self._safe_call(self.ui, "ui.show_memory", {"facts": facts or []}, timeout=30.0)

    def memory_add(self, data: dict[str, Any]) -> None:
        """Handle memory add for flow controller."""
        self._safe_call(
            self.brain,
            "brain.memory.add",
            {
                "text": str(data.get("text") or ""),
                "category": data.get("category"),
                "project": data.get("project"),
            },
            timeout=30.0,
        )

    def memory_update(self, data: dict[str, Any]) -> None:
        """Handle memory update for flow controller."""
        self._safe_call(
            self.brain,
            "brain.memory.update",
            {
                "fact_id": str(data.get("id") or data.get("fact_id") or ""),
                "text": str(data.get("text") or ""),
                "category": data.get("category"),
                "project": data.get("project"),
            },
            timeout=30.0,
        )

    def memory_delete(self, data: dict[str, Any]) -> None:
        """Handle memory delete for flow controller."""
        self._safe_call(
            self.brain,
            "brain.memory.delete",
            {"fact_id": str(data.get("id") or data.get("fact_id") or "")},
            timeout=30.0,
        )

    def open_addons(self) -> None:
        """Open addons."""
        result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            result = {}
        self._safe_call(
            self.ui,
            "ui.show_addons",
            {
                "addons": result.get("addons") or [],
                "addons_dir": str(result.get("addons_dir") or ""),
            },
            timeout=30.0,
        )

    def open_settings(self) -> None:
        """Open settings with live addon model tools from the brain process."""
        self._safe_call(
            self.ui,
            "ui.show_settings",
            {"extra_tools": self._addon_model_tool_payloads()},
            timeout=30.0,
        )

    def _worker_status_row(self, name: str, worker: WorkerLike) -> dict[str, Any]:
        """Build one worker status row without making an IPC round trip."""
        spec = getattr(worker, "spec", None)
        stderr_tail = ""
        tail_fn = getattr(worker, "stderr_tail", None)
        if callable(tail_fn):
            try:
                stderr_tail = str(tail_fn(30) or "")
            except Exception:
                stderr_tail = ""
        alive_fn = getattr(worker, "alive", None)
        try:
            alive = bool(alive_fn()) if callable(alive_fn) else False
        except Exception:
            alive = False
        return {
            "name": name,
            "pid": getattr(worker, "pid", None),
            "alive": alive,
            "module": str(getattr(spec, "module", "") or ""),
            "stderr_tail": stderr_tail,
        }

    def open_runtime_status(self) -> None:
        """Open a terminal-like diagnostics view for packaged/no-console runs."""
        workers = [
            self._worker_status_row("native", self.native),
            self._worker_status_row("ui", self.ui),
            self._worker_status_row("brain", self.brain),
            self._worker_status_row("audio", self.audio),
        ]
        self._safe_call(
            self.ui,
            "ui.runtime_status.show",
            {"workers": workers, "log_dir": os.environ.get("WISP_RUN_LOG_DIR", "")},
            timeout=30.0,
        )

    def addon_run_action(self, data: dict[str, Any]) -> None:
        """Handle addon run action for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        result = self._safe_call(
            self.brain,
            "brain.addons.run_action",
            {
                "addon_id": addon_id,
                "label": str(data.get("label") or ""),
            },
            timeout=60.0,
        )
        message = "Addon action finished."
        if isinstance(result, dict) and result.get("message"):
            message = str(result["message"])
        self._notice(message)

    def addon_set_enabled(self, data: dict[str, Any]) -> None:
        """Handle addon set enabled for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        if not addon_id:
            return
        self._safe_call(
            self.brain,
            "brain.addons.set_enabled",
            {"addon_id": addon_id, "enabled": bool(data.get("enabled"))},
            timeout=30.0,
        )
        self.open_addons()  # refresh the dialog so it reflects the new state

    def addon_set_setting(self, data: dict[str, Any]) -> None:
        """Handle addon set setting for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        key = str(data.get("key") or "")
        if not addon_id or not key:
            return
        self._safe_call(
            self.brain,
            "brain.addons.set_setting",
            {"addon_id": addon_id, "key": key, "value": data.get("value")},
            timeout=30.0,
        )

    def addon_repair_environment(self, data: dict[str, Any]) -> None:
        """Handle addon repair environment for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        if not addon_id:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.repair_environment",
            {"addon_id": addon_id},
            timeout=600.0,
        )
        message = "Addon dependency environment repaired."
        if isinstance(result, dict) and not result.get("ready", True):
            message = str(result.get("error") or "Addon dependency environment is not ready.")
        self._notice(message)
        self.open_addons()

    def addon_install_archive(self, data: dict[str, Any]) -> None:
        """Handle addon install archive for flow controller."""
        path = str(data.get("path") or "")
        if not path:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.install_archive",
            {"path": path},
            timeout=120.0,
        )
        message = "Addon archive installed."
        if isinstance(result, dict) and result.get("id"):
            message = f"Installed addon: {result['id']}"
        self._notice(message)
        self.open_addons()

    def addon_install_folder(self, data: dict[str, Any]) -> None:
        """Handle addon install folder for flow controller."""
        path = str(data.get("path") or "")
        if not path:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.install_folder",
            {"path": path},
            timeout=120.0,
        )
        message = "Addon folder installed."
        if isinstance(result, dict) and result.get("id"):
            message = f"Installed addon: {result['id']}"
        self._notice(message)
        self.open_addons()

    def addon_run_hotkey(self, data: dict[str, Any]) -> None:
        """Handle addon run hotkey for flow controller."""
        addon_id = str(data.get("addon_id") or "")
        hotkey_id = str(data.get("hotkey_id") or "")
        if not addon_id or not hotkey_id:
            return
        result = self._safe_call(
            self.brain,
            "brain.addons.run_hotkey",
            {"addon_id": addon_id, "hotkey_id": hotkey_id},
            timeout=60.0,
        )
        if isinstance(result, dict):
            prompt = str(result.get("prompt") or "").strip()
            if prompt:
                self.intent_chosen(prompt)
                return
            notify = result.get("notify")
            if isinstance(notify, dict):
                notify_result = self._safe_call(
                    self.native,
                    "native.notify",
                    {
                        "title": str(notify.get("title") or "Wisp"),
                        "message": str(notify.get("message") or ""),
                    },
                    timeout=10.0,
                )
                if not (isinstance(notify_result, dict) and notify_result.get("ok")):
                    self._notice(str(notify.get("message") or "Addon notification."))
                return
            llm = result.get("llm")
            if isinstance(llm, dict):
                llm_result = self._safe_call(
                    self.brain,
                    "brain.addons.llm_call",
                    {
                        "addon_id": addon_id,
                        "prompt": str(llm.get("prompt") or ""),
                        "max_tokens": int(llm.get("max_tokens") or 512),
                    },
                    timeout=120.0,
                )
                if isinstance(llm_result, dict) and llm_result.get("text"):
                    self._notice(str(llm_result["text"]))
                return
            message = str(result.get("message") or "").strip()
            if message:
                self._notice(message)

    def _addon_hotkeys(self) -> list[dict[str, Any]]:
        """Handle addon hotkeys for flow controller."""
        result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            return []
        out: list[dict[str, Any]] = []
        for addon in result.get("addons") or []:
            if not isinstance(addon, dict):
                continue
            addon_id = str(addon.get("id") or addon.get("name") or "")
            for item in addon.get("hotkeys") or []:
                if not isinstance(item, dict):
                    continue
                combo = str(item.get("hotkey") or "")
                hotkey_id = str(item.get("id") or "")
                if addon_id and combo and hotkey_id:
                    out.append({"addon_id": addon_id, "id": hotkey_id, "hotkey": combo})
        return out

    def _show_addon_notifications(self) -> None:
        """Show addon notifications."""
        result = self._safe_call(self.brain, "brain.addons.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            return
        for addon in result.get("addons") or []:
            if not isinstance(addon, dict):
                continue
            for item in addon.get("notifications") or []:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("message") or "")
                if not message:
                    continue
                notify_result = self._safe_call(
                    self.native,
                    "native.notify",
                    {
                        "title": str(item.get("title") or addon.get("name") or "Wisp"),
                        "message": message,
                    },
                    timeout=10.0,
                )
                if not (isinstance(notify_result, dict) and notify_result.get("ok")):
                    self._notice(message)

    def open_agent_task(self, spec: dict[str, Any] | None = None) -> None:
        """Open agent task."""
        params = {"spec": spec} if isinstance(spec, dict) and spec else {}
        self._safe_call(self.ui, "ui.show_agent_task", params, timeout=30.0)

    def open_agent_history(self) -> None:
        """Open agent history."""
        result = self._safe_call(
            self.brain,
            "brain.agent.history.list",
            {"limit": 100},
            timeout=30.0,
        ) or {}
        if not isinstance(result, dict):
            result = {}
        self._safe_call(
            self.ui,
            "ui.show_agent_history",
            {
                "runs_root": str(result.get("runs_root") or ""),
                "runs": list(result.get("runs") or []),
            },
            timeout=30.0,
        )

    def run_agent_task(self, spec: dict[str, Any]) -> None:
        """Run agent task."""
        if not isinstance(spec, dict) or not spec:
            self._notice("Agent task spec was empty.")
            return

        timeout = max(600.0, float(spec.get("max_runtime_minutes") or 60) * 60.0 + 120.0)
        done_seen = False
        stream_id: Any = None

        def on_started(req_id: Any) -> None:
            """Handle started events."""
            nonlocal stream_id
            stream_id = req_id
            with self._lock:
                self._active_agent_stream_id = req_id

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            nonlocal done_seen
            params = payload if isinstance(payload, dict) else {"data": payload}
            if event == "agent.log":
                self._safe_call(self.ui, "ui.agent.log", params, timeout=30.0)
            elif event == "agent.trace":
                self._safe_call(self.ui, "ui.agent.trace", params, timeout=30.0)
            elif event == "agent.approval.request":
                result = self._safe_call(self.ui, "ui.agent.approval.request", params, timeout=30.0) or {}
                accepted = bool(result.get("accepted")) if isinstance(result, dict) else False
                approval_id = str(params.get("approval_id") or "").strip()
                if approval_id and not accepted:
                    self._notice("Agent approval could not be shown; declining the request.")
                    self._safe_call(
                        self.brain,
                        "brain.agent.approval.respond",
                        {"approval_id": approval_id, "approved": False},
                        timeout=30.0,
                    )
            elif event == "agent.done":
                done_seen = True
                self._safe_call(self.ui, "ui.agent.done", params, timeout=30.0)

        try:
            result = self._brain_call_with_events(
                "brain.agent.run",
                {"spec": spec},
                timeout=timeout,
                on_event=on_event,
                on_started=on_started,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("agent task failed")
            self._safe_call(
                self.ui,
                "ui.agent.done",
                {"error": f"{type(exc).__name__}: {exc}"},
                timeout=30.0,
            )
            return
        finally:
            with self._lock:
                if self._active_agent_stream_id == stream_id:
                    self._active_agent_stream_id = None

        if not done_seen and isinstance(result, dict):
            self._safe_call(self.ui, "ui.agent.done", result, timeout=30.0)

    def cancel_agent_task(self) -> None:
        """Cancel agent task."""
        with self._lock:
            target = self._active_agent_stream_id
        if target is None:
            self._notice("No agent task is running.")
            return
        result = self._safe_call(self.brain, "brain.cancel", {"target": target}, timeout=10.0) or {}
        if isinstance(result, dict) and result.get("cancelled"):
            self._notice("Agent task cancellation requested.")
        else:
            self._notice("Agent task was not running.")

    def control_agent_task(self, data: dict[str, Any]) -> None:
        """Send a cooperative control command to the active agent task."""
        with self._lock:
            target = self._active_agent_stream_id
        if target is None:
            self._notice("No agent task is running.")
            return
        payload = dict(data or {})
        payload["target"] = target
        result = self._safe_call(self.brain, "brain.agent.control", payload, timeout=30.0) or {}
        if isinstance(result, dict) and result.get("message"):
            self._notice(str(result["message"]))

    def respond_agent_approval(self, data: dict[str, Any]) -> None:
        """Handle respond agent approval for flow controller."""
        approval_id = str(data.get("approval_id") or "").strip()
        if not approval_id:
            self._notice("Agent approval response was missing an id.")
            return
        result = self._safe_call(
            self.brain,
            "brain.agent.approval.respond",
            {"approval_id": approval_id, "approved": bool(data.get("approved", False))},
            timeout=30.0,
        ) or {}
        if isinstance(result, dict) and result.get("message"):
            self._notice(str(result["message"]))

    def read_agent_history(self, run_dir: str) -> None:
        """Read agent history."""
        if not run_dir:
            self._safe_call(
                self.ui,
                "ui.agent.history.detail",
                {"error": "run_dir is required"},
                timeout=30.0,
            )
            return
        try:
            result = self.brain.call(
                "brain.agent.history.read",
                {"run_dir": run_dir},
                timeout=30.0,
            ) or {}
        except Exception as exc:  # noqa: BLE001
            log.exception("agent history read failed")
            result = {"run_dir": run_dir, "error": f"{type(exc).__name__}: {exc}"}
        self._safe_call(self.ui, "ui.agent.history.detail", result, timeout=30.0)

    def retry_agent_history(self, run_dir: str) -> None:
        """Handle retry agent history for flow controller."""
        self._open_agent_spec_from_history("brain.agent.history.retry_spec", run_dir)

    def continue_agent_history(self, run_dir: str) -> None:
        """Handle continue agent history for flow controller."""
        self._open_agent_spec_from_history("brain.agent.history.continue_spec", run_dir)

    def _open_agent_spec_from_history(self, method: str, run_dir: str) -> None:
        """Open agent spec from history."""
        if not run_dir:
            self._notice("Choose an agent run first.")
            return
        result = self._safe_call(self.brain, method, {"run_dir": run_dir}, timeout=30.0) or {}
        spec = result.get("spec") if isinstance(result, dict) else None
        if isinstance(spec, dict) and spec:
            self.open_agent_task(spec)
        else:
            self._notice("Could not load that agent task spec.")

    # -- core flows -----------------------------------------------------

    def _query(
        self,
        prompt: str,
        pending: PendingInvocation,
        *,
        preserve_reply_bubble: bool = False,
    ) -> None:
        """Run a prompt through the pipeline: stop audio, show 'thinking', stream the reply."""
        query_started = time.monotonic()
        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        if not preserve_reply_bubble:
            self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
            self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        params = self._brain_query_params(prompt, pending)
        self._discard_unused_pending_context(pending, params)
        log.info(
            "query context ready in %.2fs prompt_chars=%d ambient_chars=%d "
            "selected_chars=%d screenshot=%s screenshot_tool=%s tools=%s",
            time.monotonic() - query_started,
            len(prompt or ""),
            len(str(params.get("ambient_text") or "")),
            len(str(params.get("selected") or "")),
            bool(params.get("screenshot_b64")),
            params.get("screenshot_tool_b64") is not None,
            bool(params.get("use_tools")),
        )
        summary = params.pop("_ui_context_summary", [])
        chat_context = str(params.get("ambient_text") or "")
        if summary:
            self._safe_call(self.ui, "ui.context.summary", {"items": summary}, timeout=30.0)

        done_seen = False
        first_chunk_seen = False
        streamed_reply_parts: list[str] = []
        tts_segmenter = _TtsSegmentBuffer() if self._tts_replies_enabled() else None
        early_chat_index: int | None = None
        try:
            from core.assistant_text import ThoughtStreamParser

            self._reply_thought_parser = ThoughtStreamParser()
        except Exception:
            self._reply_thought_parser = None

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            nonlocal done_seen, first_chunk_seen
            if event == "reply.chunk":
                if not first_chunk_seen:
                    first_chunk_seen = True
                    log.info("query first reply chunk after %.2fs", time.monotonic() - query_started)
                if not bool((payload or {}).get("is_progress")) and not bool((payload or {}).get("is_thought")):
                    streamed_reply_parts.append(str((payload or {}).get("text") or ""))
                if early_chat_index is not None:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {
                            "conversation_index": early_chat_index,
                            "text": str((payload or {}).get("text") or ""),
                            "is_progress": bool((payload or {}).get("is_progress")),
                            "is_thought": bool((payload or {}).get("is_thought")),
                        },
                        timeout=30.0,
                    )
                if self._reply_bubble_cancelled(gen):
                    return
                for segment, is_thought, is_progress in self._on_reply_chunk(payload):
                    if tts_segmenter is not None and is_progress and not is_thought:
                        self._queue_tts_segment(gen, segment)
                    elif tts_segmenter is not None and not is_thought:
                        for tts_segment in tts_segmenter.feed(segment):
                            self._queue_tts_segment(gen, tts_segment)
            elif event == "reply.done":
                done_seen = True
                self._emit_file_context_progress(
                    list((payload or {}).get("file_context") or []),
                    conversation_index=early_chat_index,
                )
                text_done = str((payload or {}).get("text") or "")
                if text_done:
                    self._last_reply = text_done
                if not (self._tts_replies_enabled() and text_done):
                    self._on_reply_done(payload)
            elif event == "live_file.approval.request":
                self._handle_live_file_approval_request(payload)

        # Continue the conversation selected in the chat window: replay its prior
        # turns so the model has full context. ui_host is the source of truth for
        # the active conversation (empty on a fresh start -> new conversation).
        try:
            hist = self._safe_call(self.ui, "ui.chat.active_history", {}, timeout=10.0)
            if isinstance(hist, dict):
                if hist.get("history"):
                    params["history"] = hist["history"]
                prior_context = str(hist.get("context") or "").strip()
                if prior_context:
                    base = str(params.get("ambient_text") or "")
                    block = f"[Conversation Context]\n{prior_context}"
                    params["ambient_text"] = (base + "\n\n" + block).strip() if base else block
                file_ctx = _file_context_text(list(hist.get("file_context") or []))
                if file_ctx:
                    base = str(params.get("ambient_text") or "")
                    params["ambient_text"] = (base + "\n\n" + file_ctx).strip() if base else file_ctx
                # Scope memory (retrieval + saves) to the conversation's project.
                params["memory_project"] = hist.get("project_id")
        except Exception:
            log.exception("failed to fetch active conversation history")

        context_policy = params.pop("context_policy", {})
        try:
            begin_result = self._safe_call(
                self.ui,
                "ui.chat.begin_conversation",
                {
                    "user": prompt,
                    "context": chat_context,
                    "image_base64": params.get("screenshot_b64"),
                    "context_policy": context_policy,
                },
                timeout=30.0,
            )
            if isinstance(begin_result, dict) and begin_result.get("started"):
                raw_idx = begin_result.get("conversation_index")
                if isinstance(raw_idx, int):
                    early_chat_index = raw_idx
        except Exception:
            log.exception("failed to begin chat conversation before query")
        try:
            log.info("query brain call started")
            result = self._brain_call_with_events(
                "brain.query",
                params,
                timeout=self._interactive_llm_timeout_seconds(params),
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001 - surface route/config failures in the UI
            log.exception("brain query failed after %.2fs", time.monotonic() - query_started)
            self._reply_thought_parser = None
            if early_chat_index is not None:
                self._safe_call(
                    self.ui,
                    "ui.chat.done",
                    {"conversation_index": early_chat_index, "text": ""},
                    timeout=30.0,
                )
            self._notice(f"LLM request failed: {self._friendly_error(exc)}")
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()
            return
        log.info("query brain call finished after %.2fs", time.monotonic() - query_started)
        parser = self._reply_thought_parser
        if parser is not None:
            for segment, is_thought in parser.finish():
                if segment:
                    self._safe_call(
                        self.ui,
                        "ui.reply.chunk",
                        {"text": segment, "is_thought": bool(is_thought)},
                        timeout=30.0,
                    )
                    if tts_segmenter is not None and not is_thought:
                        for tts_segment in tts_segmenter.feed(segment):
                            self._queue_tts_segment(gen, tts_segment)
            self._reply_thought_parser = None
        text = str((result or {}).get("text") or "")
        file_context = list((result or {}).get("file_context") or [])
        if not done_seen:
            self._emit_file_context_progress(file_context, conversation_index=early_chat_index)
        privacy_report = (result or {}).get("privacy_report") if isinstance(result, dict) else None
        self._last_privacy_report = privacy_report if isinstance(privacy_report, dict) else {}
        self._last_reply = text
        bubble_cancelled = self._reply_bubble_cancelled(gen)
        if early_chat_index is not None:
            self._safe_call(
                self.ui,
                "ui.chat.done",
                {
                    "conversation_index": early_chat_index,
                    "text": text,
                    "file_context": file_context,
                    "tool_context": _normalized_tool_context(
                        {
                            "allowed_tools": params.get("allowed_tools") or [],
                            "pinned_tools": params.get("pinned_tools") or [],
                            "file_access_mode": params.get("file_access_mode") or "",
                        }
                    ),
                },
                timeout=30.0,
            )
        if text and not bubble_cancelled and "".join(streamed_reply_parts) != text:
            self._replace_reply_text(text)
        if tts_segmenter is not None and not bubble_cancelled:
            for tts_segment in tts_segmenter.finish():
                self._queue_tts_segment(gen, tts_segment)
            self._finish_tts_sequence(gen)
        if text:
            tool_context = _normalized_tool_context(
                {
                    "allowed_tools": params.get("allowed_tools") or [],
                    "pinned_tools": params.get("pinned_tools") or [],
                    "file_access_mode": params.get("file_access_mode") or "",
                }
            )
            self._safe_call(
                self.ui,
                "ui.chat.add_conversation",
                {
                    "user": prompt,
                    "assistant": text,
                    "context": chat_context,
                    "image_base64": params.get("screenshot_b64"),
                    "file_context": file_context,
                    "tool_context": tool_context,
                    "context_policy": context_policy,
                    "append_user": early_chat_index is None,
                    "conversation_index": early_chat_index,
                },
                timeout=30.0,
            )
        privacy_count = int((privacy_report or {}).get("count") or 0) if isinstance(privacy_report, dict) else 0
        if privacy_count:
            self._safe_call(
                self.ui,
                "ui.context.summary",
                {
                    "items": [
                        {
                            "label": t("Privacy: {count} redacted").format(count=privacy_count),
                            "type": "privacy",
                        }
                    ]
                },
                timeout=30.0,
            )
            self._safe_call(
                self.ui,
                "ui.privacy.report",
                {"report": privacy_report, "title": "Privacy Report"},
                timeout=30.0,
            )
        if bubble_cancelled:
            self._set_idle()
        elif self._is_current(gen) and text and self._tts_replies_enabled():
            if not self._tts_sequence_is_active():
                self._speak_text(text, generation=gen)
        elif text and "".join(streamed_reply_parts) != text:
            self._safe_call(self.ui, "ui.reply.done", {"flush": False}, timeout=30.0)
            self._set_idle()
        elif not done_seen:
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()

    def _rewrite_and_paste(self, prompt: str, pending: PendingInvocation) -> None:
        """Handle rewrite and paste for flow controller."""
        gen = self._new_generation()
        selected = str(pending.context.get("selected_text") or "").strip()
        if not selected:
            self._notice("No selected text to rewrite.")
            self._set_idle()
            return
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
        self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            """Handle event events."""
            if event == "reply.chunk":
                self._on_reply_chunk(payload)

        try:
            query_params = self._brain_query_params(prompt, pending)
            rewrite_context = self._rewrite_context_from_query_params(query_params)
            log.info(
                "rewrite request context: prompt_chars=%d selected_chars=%d "
                "source_chars=%d source_labels=%r",
                len(prompt or ""),
                len(selected),
                len(rewrite_context),
                self._rewrite_source_labels(rewrite_context),
            )
            result = self._brain_call_with_events(
                "brain.rewrite",
                {
                    "selected_text": selected,
                    "intent_prompt": prompt,
                    "rewrite_context": rewrite_context,
                },
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001 - surface route/config failures in the UI
            log.exception("brain rewrite failed")
            self._notice(f"Rewrite failed: {self._friendly_error(exc)}")
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()
            return
        text = str((result or {}).get("text") or "").strip()
        visible_text = str((result or {}).get("visible_text") or "").strip()
        if not visible_text and text:
            visible_text = "Replacement pasted."
        log.info("rewrite result: text_chars=%d visible_chars=%d", len(text), len(visible_text))
        if text and self._is_current(gen):
            chat_context = "\n\n".join(
                part
                for part in (
                    f"[Selected text]\n{selected}",
                    rewrite_context,
                )
                if str(part or "").strip()
            )
            paste = self.native.call(
                "native.paste_text",
                {
                    "text": text,
                    "target_pid": pending.paste_target_pid,
                    "focus_token": int(pending.context.get("focus_token") or 0),
                    "restore_clipboard": True,
                },
                timeout=30.0,
            )
            paste = paste if isinstance(paste, dict) else {}
            log.info(
                "rewrite paste-back: target_pid=%s result=%s",
                pending.paste_target_pid, paste,
            )
            # Rewrite status must NOT land in the reply bubble (it would clobber the
            # streamed rewrite text). Success is silent â€” the pasted text in the
            # user's app is the confirmation. Only problems raise a system
            # notification, which needs user action / awareness.
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            if paste.get("ok"):
                pass  # silent success
            elif paste.get("clipboard_ok"):
                app = str(paste.get("app_name") or "").strip()
                where = f" into {app}" if app else ""
                log.warning(
                    "rewrite paste-back could not confirm focus%s (frontmost=%s); "
                    "clipboard_restored=%s",
                    where,
                    paste.get("frontmost_pid"),
                    paste.get("clipboard_restored"),
                )
                self._native_notify(
                    "Wisp rewrite could not paste",
                    "Couldn't replace the selected text. Your clipboard was restored.",
                )
            else:
                log.error("rewrite paste-back failed: %s", paste.get("error") or paste)
                self._native_notify("Wisp â€” rewrite failed", "Couldn't paste the rewrite. See native.stderr.log.")
            self._safe_call(
                self.ui,
                    "ui.chat.add_conversation",
                    {
                        "user": prompt,
                        "assistant": visible_text,
                        "context": chat_context,
                        "context_policy": query_params.get("context_policy") or {},
                    },
                timeout=30.0,
            )
        elif self._is_current(gen):
            log.warning("rewrite returned empty text; paste-back skipped")
            self._native_notify("Wisp rewrite returned nothing", "The model returned no replacement text.")
        self._set_idle()

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _rewrite_context_from_query_params(params: dict[str, Any]) -> str:
        """Render the shared Ctrl+Q context payload as source-only rewrite context."""
        parts: list[str] = []
        context_priority = str(params.get("context_priority") or "").strip()
        if context_priority:
            parts.append(f"[Context priority]\nPrioritize {context_priority} when sources disagree.")
        ambient_text = str(params.get("ambient_text") or "").strip()
        if ambient_text:
            parts.append(ambient_text)
        active_document_text = FlowController._rewrite_source_document_text(
            str(params.get("active_document_text") or ""),
            str(params.get("selected") or ""),
        ).strip()
        if active_document_text:
            label = " ".join(str(params.get("active_document_label") or "").split()).strip()
            if label:
                parts.append(
                    f"--- BEGIN ACTIVE DOCUMENT: {label} ---\n"
                    f"{active_document_text}\n"
                    f"--- END ACTIVE DOCUMENT: {label} ---"
                )
            else:
                parts.append(f"[Active document]\n{active_document_text}")
        return "\n\n".join(part for part in parts if str(part or "").strip())

    @staticmethod
    def _rewrite_source_document_text(active_document_text: str, selected_text: str) -> str:
        """Drop target-selection document blocks when other document sources exist."""
        raw = str(active_document_text or "").strip()
        selected = FlowController._rewrite_match_text(selected_text)
        if not raw or not selected:
            return raw
        matches = list(re.finditer(r"(?m)^\[([^\]\n]{1,160})\]\n", raw))
        if len(matches) <= 1:
            return raw

        kept: list[str] = []
        removed = 0
        for idx, match in enumerate(matches):
            label = match.group(1).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            body = raw[start:end].strip()
            body_match = FlowController._rewrite_match_text(body)
            is_target = bool(body_match) and (selected in body_match or body_match in selected)
            if is_target:
                removed += 1
                continue
            if label and body:
                kept.append(f"[{label}]\n{body}")
        if removed and kept:
            return "\n\n".join(kept)
        return raw

    @staticmethod
    def _rewrite_match_text(text: str) -> str:
        """Normalize text for target/source block matching."""
        return " ".join(str(text or "").split()).casefold()

    @staticmethod
    def _rewrite_source_labels(text: str) -> list[str]:
        """Return source block labels for rewrite diagnostics without logging content."""
        labels: list[str] = []
        for pattern in (
            r"(?m)^--- BEGIN [^-:\n]+: (.{1,160}?) ---$",
            r"(?m)^\[([^\]\n]{1,160})\]$",
        ):
            for match in re.finditer(pattern, str(text or "")):
                label = " ".join(match.group(1).split()).strip()
                if label and label not in labels:
                    labels.append(label)
        return labels[:8]

    @staticmethod
    def _paste_shortcut() -> str:
        """Paste shortcut."""
        return flow_utils.paste_shortcut()

    @staticmethod
    def _is_local_file_request(prompt: str) -> bool:
        """Return True when a paste-back prompt is really asking for disk edits."""
        return flow_utils.is_local_file_request(prompt)

    def _native_notify(self, title: str, message: str) -> None:
        """Best-effort system notification (keeps status out of the reply bubble)."""
        try:
            self.native.call("native.notify", {"title": title, "message": message}, timeout=10.0)
        except Exception:
            log.exception("native.notify failed")

    def _schedule(self, fn, *args) -> None:
        """Run *fn* on a daemon thread, or inline when async is disabled."""
        if not self.run_async:
            fn(*args)
            return
        threading.Thread(target=self._guarded, args=(fn, args), daemon=True).start()

    def _guarded(self, fn, args) -> None:
        """Handle guarded for flow controller."""
        try:
            fn(*args)
        except Exception:
            log.exception("flow %s failed", getattr(fn, "__name__", fn))
            self._set_idle()

    def _safe_call(self, worker: WorkerLike, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        """Handle safe call for flow controller."""
        try:
            return worker.call(method, params or {}, timeout=timeout)
        except Exception:
            log.exception("worker call failed: %s", method)
            return None

    def _fire(self, worker: WorkerLike, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a fire-and-forget request â€” the response is not awaited.

        For cosmetic / side-effect calls (e.g. doll animation state, stopping
        speech) that must never sit on the critical path. A slow or wedged worker
        then can't delay the thing the user is actually waiting for."""
        try:
            worker.call(method, params or {}, wait=False)
        except Exception:
            log.exception("worker fire failed: %s", method)

    def _brain_call_with_events(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        on_event: Callable[[str, Any, Any], None],
        on_started: Callable[[Any], None] | None = None,
    ) -> Any:
        """Handle brain call with events for flow controller."""
        call_with_events = getattr(self.brain, "call_with_events", None)
        if callable(call_with_events):
            return call_with_events(
                method,
                params,
                timeout=timeout,
                on_event=on_event,
                on_started=on_started,
            )
        return self.brain.call(method, params, timeout=timeout)

    def _new_generation(self) -> int:
        """Handle new generation for flow controller."""
        with self._lock:
            self._current_generation = next(self._generation)
            return self._current_generation

    def _is_current(self, generation: int) -> bool:
        """Return whether current is true."""
        with self._lock:
            return generation == self._current_generation

    def _reply_bubble_cancelled(self, generation: int) -> bool:
        """Return whether bubble/TTS output was muted for this generation."""
        with self._lock:
            return generation == self._reply_bubble_cancelled_generation

    def _claim_voice_start(self) -> bool:
        """Handle claim voice start for flow controller."""
        with self._lock:
            if self._voice_state != "idle":
                return False
            self._voice_state = "starting"
            self._voice_active = True
            return True

    def _ensure_voice_start_claimed(self) -> None:
        """Ensure voice start claimed."""
        with self._lock:
            if self._voice_state == "idle":
                self._voice_state = "starting"
                self._voice_active = True

    def _mark_voice_recording(self) -> bool:
        """Handle mark voice recording for flow controller."""
        with self._lock:
            if self._voice_state == "starting":
                self._voice_state = "recording"
                return True
            return self._voice_state == "recording"

    def _claim_voice_stop(self) -> bool:
        """Handle claim voice stop for flow controller."""
        with self._lock:
            if self._voice_state == "idle":
                return False
            self._voice_state = "stopping"
            self._voice_active = False
            return True

    def _ensure_voice_stop_claimed(self) -> bool:
        """Ensure voice stop claimed."""
        with self._lock:
            if self._voice_state == "idle":
                return False
            self._voice_state = "stopping"
            self._voice_active = False
            return True

    def _mark_voice_idle(self) -> None:
        """Handle mark voice idle for flow controller."""
        with self._lock:
            self._voice_state = "idle"
            self._voice_active = False

    def _claim_dictate_start(self) -> bool:
        """Handle claim dictate start for flow controller."""
        with self._lock:
            # Mutually exclusive with voice push-to-talk (one shared recorder).
            if self._dictate_state != "idle" or self._voice_state != "idle":
                return False
            self._dictate_state = "recording"
            return True

    def _claim_dictate_stop(self) -> bool:
        """Handle claim dictate stop for flow controller."""
        with self._lock:
            if self._dictate_state != "recording":
                return False
            self._dictate_state = "stopping"
            return True

    def _mark_dictate_idle(self) -> None:
        """Handle mark dictate idle for flow controller."""
        with self._lock:
            self._dictate_state = "idle"

    def _set_idle(self) -> None:
        # Fire-and-forget. This runs inline on the worker event-reader thread
        # (from _on_intent_cancelled / _on_snip_cancelled). A BLOCKING ui.call
        # here waits for a response that only that same reader thread can read ->
        # 30s self-deadlock that also stalls every other UI call queued behind it
        # (e.g. the next snip). The idle animation is cosmetic, so never wait --
        # mirrors the non-blocking "listening" state fired in begin_caller/snip.
        """Set idle."""
        self._fire(self.ui, "ui.overlay.state", {"state": "idle"})

    def _notice(self, text: str) -> None:
        """Show a transient warning/status bubble that dismisses itself.

        These are advisory ("didn't catch that", "couldn't start recording", …),
        so they auto-hide after a few seconds instead of lingering — long enough
        to read, short enough not to nag after an accidental tap.
        """
        from core.error_recommendations import format_error

        self._safe_call(
            self.ui,
            "ui.reply.notice",
            {"text": format_error(text), "timeout_ms": 6000},
            timeout=30.0,
        )

    def _handle_live_file_approval_request(self, payload: Any) -> None:
        """Ask the UI to approve a live model file edit, then answer the brain."""
        if not isinstance(payload, dict):
            return
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            return
        result = self._safe_call(
            self.ui,
            "ui.live_file.approval.request",
            payload,
            timeout=600.0,
        ) or {}
        approved = bool(result.get("approved")) if isinstance(result, dict) else False
        feedback = str(result.get("feedback") or "").strip() if isinstance(result, dict) else ""
        self._fire(
            self.brain,
            "brain.live_file.approval.respond",
            {"approval_id": approval_id, "approved": approved, "feedback": feedback},
        )

    def _stt_warming(self) -> bool:
        """True when the STT model isn't loaded/warmed yet, so the next transcribe
        will block on the slow first load. Fast and best-effort: any failure or
        timeout is treated as 'ready' so this never adds latency to the voice path."""
        try:
            res = self.audio.call("audio.stt.is_ready", timeout=3.0) or {}
            return not bool(res.get("ready", True))
        except Exception:
            return False

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        """Handle friendly error for flow controller."""
        return flow_utils.friendly_error(exc)

    def _caller(self, caller_idx: int) -> dict[str, Any]:
        """Handle caller for flow controller."""
        import config

        rows = getattr(config, "CALLER_ROWS", [])
        if 0 <= caller_idx < len(rows):
            return config.effective_caller(dict(rows[caller_idx]))
        return {}

    def _voice_caller(self) -> dict[str, Any]:
        """Context/tool config for push-to-talk; falls back to caller 1's row."""
        import config

        voice = getattr(config, "VOICE_CALLER", None)
        if isinstance(voice, dict) and voice:
            return config.effective_caller(dict(voice))
        return self._caller(0)

    def _voice_review_transcript_enabled(self) -> bool:
        """Return whether F9 should review the transcript in the intent picker."""
        import config

        return bool(getattr(config, "VOICE_REVIEW_TRANSCRIPT", False))

    def _snip_caller(self) -> dict[str, Any]:
        """Context/tool config for region snips, while reusing caller 1's prompts."""
        import config

        caller = self._caller(0)
        snip = getattr(config, "SNIP_CALLER", None)
        if isinstance(snip, dict) and snip:
            caller.update(config.effective_caller(dict(snip)))
        else:
            caller.update(
                {
                    "context_ambient": self._config_value("SNIP_CONTEXT_AMBIENT", True),
                    "context_documents": self._config_value("SNIP_CONTEXT_DOCUMENTS", True),
                    "context_tools": self._config_value("SNIP_CONTEXT_TOOLS", False),
                }
            )
        caller.update({"context_screenshot": "off", "paste_back": False})
        return caller

    @staticmethod
    def _current_config_mtime() -> float | None:
        """Handle current config mtime for flow controller."""
        try:
            import config

            env_file = Path(getattr(config, "_ENV_FILE", ""))
            return env_file.stat().st_mtime
        except (OSError, TypeError, ValueError):
            return None

    def _reload_supervisor_config_if_changed(self) -> None:
        """Handle reload supervisor config if changed for flow controller."""
        current_mtime = self._current_config_mtime()
        if current_mtime is None or current_mtime == self._config_mtime:
            return
        import config

        config.reload()
        self._config_mtime = current_mtime
        log.info("supervisor config reloaded after .env change")

    def _config_value(self, name: str, default: Any) -> Any:
        """Handle config value for flow controller."""
        import config

        return getattr(config, name, default)

    @staticmethod
    def _interactive_llm_timeout_seconds(params: dict[str, Any]) -> float:
        """Return a longer timeout when the model has live tools available."""
        return (
            _INTERACTIVE_LLM_TOOL_TIMEOUT_SECONDS
            if bool((params or {}).get("use_tools"))
            else _INTERACTIVE_LLM_TIMEOUT_SECONDS
        )

    def _log_caller_runtime(self, caller_idx: int, caller: dict[str, Any]) -> None:
        """Log caller runtime."""
        try:
            import config

            log.info(
                "caller %d config\n"
                "  label=%r hotkey=%r paste_back=%s\n"
                "  context: app=%s docs=%s browser=%s memory=%s screenshot=%r clipboard=%s\n"
                "  runtime: cwd=%r\n"
                "           config=%r\n"
                "           env=%r",
                caller_idx,
                caller.get("label"),
                caller.get("hotkey"),
                caller.get("paste_back"),
                caller.get("context_ambient"),
                self._context_mode(caller, "documents"),
                self._context_mode(caller, "browser"),
                self._context_mode(caller, "memory"),
                caller.get("context_screenshot"),
                caller.get("context_clipboard"),
                str(Path.cwd()),
                str(getattr(config, "__file__", "") or ""),
                str(getattr(config, "_ENV_FILE", "") or ""),
            )
        except Exception:
            log.exception("caller runtime logging failed")

    def _context_snapshot(
        self,
        caller: dict[str, Any],
        *,
        include_browser: bool = True,
        include_selected_paths: bool = False,
        preview_context_sources: bool = False,
    ) -> dict[str, Any]:
        # The browser-page fetch is a ~2-3s network read (requests.get). Keep it
        # OFF the hotkey -> picker path (include_browser=False) and fetch it lazily
        # at query time instead, where it overlaps the LLM round-trip. The URL and
        # window handle ARE captured now, while the browser is still foreground â€”
        # by query time the picker has stolen focus and re-detection would fail.
        """Handle context snapshot for flow controller."""
        browser_auto = self._context_mode(caller, "browser") == "auto"
        snapshot = self.native.call(
            "native.context.snapshot",
            {
                "include_clipboard": bool(caller.get("context_clipboard", False))
                or preview_context_sources,
                "include_selection": True,
                "include_selected_paths": bool(include_selected_paths),
                "include_browser_content": include_browser and browser_auto,
                "include_browser_url": browser_auto or preview_context_sources,
                # Paste-back callers capture the focused text element so the rewrite
                # can be written back in place (AX) without refocusing the app.
                "capture_focus": bool(caller.get("paste_back")),
            },
            timeout=30.0,
        ) or {}
        active_app = snapshot.get("active_app") if isinstance(snapshot.get("active_app"), dict) else {}
        debug = snapshot.get("debug") if isinstance(snapshot.get("debug"), dict) else {}
        runtime_debug = debug.get("runtime") if isinstance(debug.get("runtime"), dict) else {}
        window_debug = debug.get("window") if isinstance(debug.get("window"), dict) else {}
        browser_window = debug.get("browser_window") if isinstance(debug.get("browser_window"), dict) else {}
        log.info(
            "context snapshot\n"
            "  active: title=%r process=%r pid=%s hwnd=%s\n"
            "  foreground: raw=(hwnd=%s pid=%s process=%r title=%r)\n"
            "              chosen=(hwnd=%s pid=%s process=%r title=%r corrected=%s)\n"
            "  browser: url=%s hwnd=%s chars=%d\n"
            "  runtime: cwd=%r repo=%r exe=%r\n"
            "           config=%r env=%r",
            active_app.get("name"),
            active_app.get("process_name"),
            active_app.get("pid"),
            active_app.get("window_id") or active_app.get("pid") or 0,
            window_debug.get("raw_hwnd"),
            window_debug.get("raw_pid"),
            window_debug.get("raw_process"),
            window_debug.get("raw_title"),
            window_debug.get("chosen_hwnd"),
            window_debug.get("chosen_pid"),
            window_debug.get("chosen_process"),
            window_debug.get("chosen_title"),
            window_debug.get("corrected"),
            snapshot.get("browser_url") or "",
            snapshot.get("browser_hwnd") or 0,
            len(str(snapshot.get("browser_content") or "")),
            runtime_debug.get("cwd"),
            runtime_debug.get("repo_root"),
            runtime_debug.get("executable"),
            runtime_debug.get("config_file"),
            runtime_debug.get("env_file"),
        )
        if browser_window:
            log.info(
                "context browser window\n"
                "  hwnd=%s pid=%s process=%r\n"
                "  title=%r\n"
                "  url=%r",
                browser_window.get("hwnd"),
                browser_window.get("pid"),
                browser_window.get("process_name"),
                browser_window.get("title"),
                browser_window.get("url"),
            )
        if snapshot.get("browser_error"):
            log.warning("context browser error: %s", snapshot.get("browser_error"))
        return snapshot

    def _fetch_browser_snapshot(self) -> dict[str, Any]:
        """Fetch just the active browser tab's URL + page content â€” the deferred,
        slow part of the snapshot. Active-app only; no selection/clipboard."""
        return self.native.call(
            "native.context.snapshot",
            {
                "include_clipboard": False,
                "include_selection": False,
                "include_browser_content": True,
            },
            timeout=30.0,
        ) or {}

    def _intent_target_id(self, context: dict[str, Any]) -> int:
        """Return the hotkey-time target id used for paste-back and placement."""
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if str(context.get("platform") or "") == "darwin":
            return int(active_app.get("pid") or 0)
        return int(active_app.get("window_id") or active_app.get("pid") or 0)

    def _collect_initial_intent_context(
        self,
        pending: PendingInvocation,
        generation: int,
        started_at: float,
        shown_at: float,
    ) -> None:
        """Capture initial context after the picker is already visible."""
        try:
            if not self._is_current(generation):
                return
            t_ctx0 = time.monotonic()
            context = pending.context if isinstance(pending.context, dict) and pending.context else {}
            if not context:
                context = self._context_snapshot(
                    pending.caller,
                    include_browser=False,
                    preview_context_sources=True,
                )
            t_ctx = time.monotonic()
            if not self._is_current(generation):
                return
            screenshot_b64 = pending.screenshot_b64
            screenshot_tool_b64 = pending.screenshot_tool_b64
            t_shot = time.monotonic()
            if not self._is_current(generation):
                return
            target_id = self._intent_target_id(context)
            pending.context = context
            pending.screenshot_b64 = screenshot_b64
            pending.screenshot_tool_b64 = screenshot_tool_b64
            pending.intent_target_pid = target_id
            pending.paste_target_pid = target_id if pending.caller.get("paste_back") else 0
            with self._lock:
                if self._pending is pending:
                    self._pending = pending
            if self._is_current(generation):
                self._fire(
                    self.ui,
                    "ui.intent.context_items",
                    {"context_items": self._intent_context_items(pending)},
                )
            log.info(
                "caller %d context ready after show=%.2fs context=%.2fs screenshot=%.2fs total=%.2fs",
                pending.caller_idx,
                t_ctx - shown_at,
                t_ctx - t_ctx0,
                t_shot - t_ctx,
                t_shot - started_at,
            )
            pending.context_ready.set()
            self._prefetch_intent_context(pending, generation)
        finally:
            pending.context_ready.set()

    def _active_document_window(self, context: dict[str, Any]) -> dict[str, Any]:
        """Build the active-window payload used by active document extraction."""
        document_window = context.get("document_window") if isinstance(context.get("document_window"), dict) else {}
        if document_window:
            return {
                "title": document_window.get("title") or "",
                "process_name": document_window.get("process_name") or "",
                "pid": document_window.get("pid") or 0,
                "window_id": document_window.get("window_id") or 0,
            }
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        debug = context.get("debug") if isinstance(context.get("debug"), dict) else {}
        window_debug = debug.get("window") if isinstance(debug.get("window"), dict) else {}
        return {
            "title": window_debug.get("chosen_title") or window_debug.get("raw_title") or active_app.get("name") or "",
            "process_name": (
                window_debug.get("chosen_process")
                or window_debug.get("raw_process")
                or active_app.get("process_name")
                or active_app.get("name")
                or ""
            ),
            "pid": active_app.get("pid") or window_debug.get("chosen_pid") or window_debug.get("raw_pid") or 0,
            "window_id": active_app.get("window_id") or window_debug.get("chosen_hwnd") or window_debug.get("raw_hwnd") or 0,
        }

    def _fetch_active_document_text(self, context: dict[str, Any]) -> str:
        """Fetch active document text for preview and query reuse."""
        result = self._safe_call(
            self.brain,
            "brain.context.active_document",
            {"active_window": self._active_document_window(context)},
            timeout=15.0,
        ) or {}
        text = str(result.get("text") or "") if isinstance(result, dict) else ""
        doc_debug = result.get("debug") if isinstance(result, dict) else None
        if isinstance(context, dict):
            context["active_document_sources"] = self._active_document_source_previews(text, doc_debug)
        sources = context.get("active_document_sources") if isinstance(context, dict) else []
        source_labels = [
            str(item.get("label") or "")
            for item in (sources or [])
            if isinstance(item, dict) and str(item.get("label") or "")
        ]
        candidate_lines: list[str] = []
        if isinstance(doc_debug, dict):
            for item in (doc_debug.get("window_candidates") or [])[:12]:
                if not isinstance(item, dict):
                    continue
                candidate_lines.append(
                    "    - {label!r} process={process!r} hwnd={hwnd} chars={chars} accepted={accepted} method={method!r}".format(
                        label=item.get("label") or item.get("title") or "",
                        process=item.get("process_name") or "",
                        hwnd=item.get("hwnd") or 0,
                        chars=item.get("chars") or 0,
                        accepted=bool(item.get("accepted")),
                        method=item.get("method") or "",
                    )
                )
        candidate_text = "\n".join(candidate_lines) if candidate_lines else "    - none"
        log.info(
            "active document context\n"
            "  chars=%d sources=%r error=%r\n"
            "  paths=%r path_chars=%s\n"
            "  window_labels=%r window_chars=%s\n"
            "  window_candidates:\n%s",
            len(text),
            source_labels,
            result.get("error") if isinstance(result, dict) else None,
            doc_debug.get("paths") if isinstance(doc_debug, dict) else [],
            doc_debug.get("path_chars") if isinstance(doc_debug, dict) else 0,
            doc_debug.get("window_labels") if isinstance(doc_debug, dict) else [],
            doc_debug.get("window_chars") if isinstance(doc_debug, dict) else 0,
            candidate_text,
        )
        return text

    def _active_document_source_previews(self, text: str, debug: Any) -> list[dict[str, str]]:
        """Split active-document text into labelled preview rows for the overlay."""
        raw = str(text or "").strip()
        if not raw:
            return []
        labels: list[str] = []
        if isinstance(debug, dict):
            labels = [
                str(label or "").strip()
                for label in (debug.get("window_labels") or [])
                if str(label or "").strip()
            ]
            if not labels:
                labels = [
                    Path(str(path or "")).name
                    for path in (debug.get("paths") or [])
                    if str(path or "").strip()
                ]
        sources: list[dict[str, str]] = []
        matches = list(re.finditer(r"(?m)^\[([^\]\n]{1,160})\]\n", raw))
        for idx, match in enumerate(matches):
            label = " ".join(match.group(1).split()).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            preview = self._context_preview_text(raw[start:end])
            if label and preview:
                sources.append({"label": label, "preview": preview})
        if sources:
            return sources[:5]
        label = labels[0] if labels else self._active_document_label({})
        return [{"label": label, "preview": self._context_preview_text(raw)}]

    def _active_document_label(self, context: dict[str, Any]) -> str:
        """Return a human-readable source label for active document context."""
        active_window = self._active_document_window(context)
        process = " ".join(str(active_window.get("process_name") or "").split()).strip()
        title = " ".join(str(active_window.get("title") or "").split()).strip()
        if process and title and title != process:
            return f"{process} - {title}"
        return title or process or "Active document"

    def _active_document_context_label(self, context: dict[str, Any]) -> str:
        """Return the prompt boundary label for active-document context."""
        sources = [
            item
            for item in (context.get("active_document_sources") or [])
            if isinstance(item, dict) and str(item.get("preview") or "").strip()
        ]
        if len(sources) > 1:
            return "Open app documents"
        return self._active_document_label(context)

    def _fetch_browser_content_for_context(self, context: dict[str, Any]) -> dict[str, str]:
        """Fetch browser page text using the URL/handle captured at hotkey time."""
        browser_url = str(context.get("browser_url") or "").strip()
        browser_hwnd = int(context.get("browser_hwnd") or 0)
        browser_app = str(context.get("browser_app") or "").strip()
        browser_content = str(context.get("browser_content") or "").strip()
        if (browser_url or browser_hwnd or browser_app) and not browser_content:
            result = self._safe_call(
                self.native,
                "native.context.browser_content",
                {
                    "url": browser_url,
                    "hwnd": browser_hwnd,
                    "app": browser_app,
                },
                timeout=30.0,
            ) or {}
            browser_url = str(result.get("url") or browser_url).strip()
            browser_content = str(result.get("content") or "").strip()
            log.info(
                "browser context by captured hwnd url=%r hwnd=%s chars=%d error=%r",
                browser_url,
                browser_hwnd,
                len(browser_content),
                result.get("error") if isinstance(result, dict) else None,
            )
        elif not browser_url and not browser_content:
            fetched = self._fetch_browser_snapshot()
            browser_url = str(fetched.get("browser_url") or "").strip()
            browser_content = str(fetched.get("browser_content") or "").strip()
            log.info(
                "browser context fallback snapshot url=%s hwnd=%s chars=%d error=%r",
                "y" if browser_url else "n",
                fetched.get("browser_hwnd") or 0,
                len(browser_content),
                fetched.get("browser_error"),
            )
        return {"browser_url": browser_url, "browser_content": browser_content}

    def _prefetch_intent_context(self, pending: PendingInvocation, generation: int) -> None:
        """Fetch slow context while the intent overlay is open, then refresh chips."""
        if not self._is_current(generation):
            return
        changed = False
        context = pending.context if isinstance(pending.context, dict) else {}
        if not context.get("active_document_text"):
            text = self._fetch_active_document_text(context)
            if not self._is_current(generation):
                return
            if text:
                context["active_document_text"] = text
                changed = True
        browser_available = bool(context.get("browser_url") or context.get("browser_hwnd") or context.get("browser_app"))
        if browser_available and not context.get("browser_content"):
            browser = self._fetch_browser_content_for_context(context)
            if not self._is_current(generation):
                return
            if browser.get("browser_url") and not context.get("browser_url"):
                context["browser_url"] = browser["browser_url"]
                changed = True
            if browser.get("browser_content"):
                context["browser_content"] = browser["browser_content"]
                changed = True
        if not changed or not self._is_current(generation):
            return
        with self._lock:
            if self._pending is pending:
                self._pending.context = context
        self._fire(
            self.ui,
            "ui.intent.context_items",
            {"context_items": self._intent_context_items(pending)},
        )

    def _brain_query_params(self, prompt: str, pending: PendingInvocation) -> dict[str, Any]:
        """Handle brain query params for flow controller."""
        caller = pending.caller
        context = pending.context or {}
        ambient_parts: list[str] = []
        buffered_items, drop_items = self._consume_context_extras()
        if caller.get("_context_selection_enabled", True):
            drop_items.extend(self._path_context_items(context.get("selected_paths")))
        screenshot_b64 = pending.screenshot_b64
        screenshot_tool_b64: str | None = pending.screenshot_tool_b64
        if caller.get("_context_screenshot_enabled") is False:
            screenshot_b64 = None
            screenshot_tool_b64 = None
        elif (
            not screenshot_b64
            and str(caller.get("context_screenshot") or "").strip().lower() == "auto"
            and not caller.get("_context_screenshot_requires_snip")
        ):
            screenshot_b64 = self._capture_fullscreen_b64()
        allow_screenshot_tool = self._screenshot_tool_allowed(caller)
        if allow_screenshot_tool and screenshot_tool_b64 is None:
            screenshot_tool_b64 = self._capture_model_tool_b64()
        allowed_tools = self._allowed_model_tools(caller)
        pinned_tools = self._pinned_model_tools(caller)
        frontload_tools = self._frontloaded_model_tools(caller)
        memory_mode = self._context_mode(caller, "memory")
        documents_mode = self._effective_document_mode(caller)
        include_active_document = documents_mode == "auto"
        active_document_text = str(context.get("active_document_text") or "") if include_active_document else ""
        if include_active_document:
            active_document_text = active_document_text or self._fetch_active_document_text(context)
        if caller.get("context_ambient", True):
            active_app = context.get("active_app")
            if isinstance(active_app, dict) and active_app.get("name"):
                ambient_parts.append(f"[App]\nActive app: {active_app.get('name')}")
        if caller.get("context_clipboard") and context.get("clipboard_text"):
            ambient_parts.append(f"[Clipboard]\n{context.get('clipboard_text')}")
        if self._context_mode(caller, "browser") == "auto":
            browser_bits: list[str] = []
            browser_url = str(context.get("browser_url") or "").strip()
            browser_app = str(context.get("browser_app") or "").strip()
            browser_content = str(context.get("browser_content") or "").strip()
            if not browser_content:
                # URL + window handle (Windows) or browser app name (macOS) were
                # captured at hotkey time while the browser was foreground; read
                # the page now (deferred off the picker path). Windows reads by
                # handle; macOS asks the named app via AppleScript â€” both work
                # with the picker/overlay holding focus.
                browser = self._fetch_browser_content_for_context(context)
                browser_url = browser.get("browser_url") or browser_url
                browser_content = browser.get("browser_content") or ""
            if browser_url or browser_content or browser_app:
                browser_bits.append(
                    f"Priority: {'primary' if self._is_browser_active_context(context) else 'supporting'}"
                )
            if browser_url:
                browser_bits.append(f"URL: {browser_url}")
            if browser_content:
                browser_bits.append(browser_content)
            elif browser_app:
                # macOS only (browser_app is set only there). The page text came
                # back empty â€” almost always a permission the user hasn't granted
                # yet. Tell the model so it can relay the fix instead of just
                # claiming it cannot read the page.
                if browser_url:
                    browser_bits.append(
                        f"(Could not read the {browser_app} page text. In {browser_app}, enable "
                        f"View â†’ Developer â†’ Allow JavaScript from Apple Events.)"
                    )
                else:
                    browser_bits.append(
                        f"(Could not read {browser_app}. Allow this app to control {browser_app} in "
                        f"System Settings â†’ Privacy & Security â†’ Automation, then try again.)"
                    )
            if browser_bits:
                ambient_parts.append("[Browser/Web]\n" + "\n\n".join(browser_bits))
        if buffered_items:
            ambient_parts.append("[Buffered context]\n" + "\n\n".join(buffered_items))
        if drop_items:
            drop_text_parts = []
            for item in drop_items:
                item_type = str(item.get("type") or "text")
                content = item.get("content")
                if item_type == "image" and not screenshot_b64:
                    screenshot_b64 = self._image_content_b64(content)
                    continue
                name = " ".join(str(item.get("name") or "Context").split()).strip() or "Context"
                drop_text_parts.append(
                    f"--- BEGIN DROPPED CONTEXT: {name} ({item_type}) ---\n"
                    f"{self._content_to_text(content)}\n"
                    f"--- END DROPPED CONTEXT: {name} ({item_type}) ---"
                )
            if drop_text_parts:
                ambient_parts.append("[Dropped context]\n" + "\n\n".join(drop_text_parts))
        ambient_text = "\n\n".join(ambient_parts)
        context_priority = self._context_priority_source(
            context,
            ambient_text,
            active_document_text,
        )
        selected_text = (
            str(context.get("selected_text") or "")
            if caller.get("_context_selection_enabled", True)
            else ""
        )
        summary = self._context_summary_badges(
            selected=selected_text,
            screenshot_b64=screenshot_b64,
            buffered_items=buffered_items,
            drop_items=drop_items,
            clipboard_text=str(context.get("clipboard_text") or "") if caller.get("context_clipboard") else "",
            ambient_text=ambient_text,
            active_document_text=active_document_text,
        )
        return {
            "intent_prompt": prompt,
            "selected": selected_text,
            "screenshot_b64": screenshot_b64,
            "ambient_text": ambient_text,
            "memory_enabled": memory_mode == "on",
            "use_tools": bool(allowed_tools),
            "allowed_tools": allowed_tools,
            "pinned_tools": pinned_tools,
            "frontload_tools": frontload_tools,
            "file_access_mode": tool_modes.local_file_access_mode(caller),
            "allow_screenshot_tool": allow_screenshot_tool,
            "screenshot_tool_b64": screenshot_tool_b64,
            "include_active_document": include_active_document and not active_document_text,
            "active_document_text": active_document_text,
            "active_document_label": self._active_document_context_label(context) if include_active_document else "",
            "context_priority": context_priority,
            "_ui_context_summary": summary,
            "context_policy": _normalized_context_policy(caller),
        }

    @staticmethod
    def _discard_unused_pending_context(
        pending: PendingInvocation,
        params: dict[str, Any],
    ) -> None:
        """Drop gathered context that was left out of the final request payload.

        This is best-effort transient cleanup, not secure memory erasure. The
        provider-bound payload in ``params`` keeps the selected context; this
        removes unselected preview/capture values from the pending request state
        before the provider call starts.
        """
        context = pending.context if isinstance(pending.context, dict) else {}
        ambient = str(params.get("ambient_text") or "")

        selected_used = bool(params.get("selected"))
        clipboard_used = "[Clipboard]" in ambient
        browser_used = "[Browser/Web]" in ambient
        active_document_used = bool(params.get("active_document_text")) or (
            "--- BEGIN ACTIVE DOCUMENT:" in ambient or "[Active document]" in ambient
        )
        app_used = "[App]" in ambient or active_document_used

        if not selected_used:
            context.pop("selected_text", None)
        if not clipboard_used:
            context.pop("clipboard_text", None)
        if not browser_used:
            for key in (
                "browser_url",
                "browser_content",
                "browser_app",
                "browser_hwnd",
                "browser_window",
                "browser_error",
            ):
                context.pop(key, None)
        if not active_document_used:
            for key in ("active_document_text", "active_document_sources", "document_window"):
                context.pop(key, None)
        if not app_used:
            context.pop("active_app", None)

        # Debug snapshots can contain window titles/process metadata that are
        # useful for local diagnostics but are never needed after payload build.
        context.pop("debug", None)

        if not params.get("screenshot_b64"):
            pending.screenshot_b64 = None
        if not params.get("screenshot_tool_b64"):
            pending.screenshot_tool_b64 = None

    @staticmethod
    def _is_browser_active_context(context: dict[str, Any]) -> bool:
        """Return whether browser active context is true."""
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        candidates = [
            active_app.get("process_name"),
            active_app.get("name"),
            context.get("browser_app"),
        ]
        names = {str(name or "").strip().lower() for name in candidates if str(name or "").strip()}
        if names & _BROWSER_APP_NAMES:
            return True
        try:
            from core.context_fetcher import _BROWSER_PROCS

            if names & set(_BROWSER_PROCS):
                return True
        except Exception:
            pass
        return False

    @classmethod
    def _context_priority_source(
        cls,
        context: dict[str, Any],
        ambient_text: str,
        active_document_text: str,
    ) -> str:
        """Handle context priority source for flow controller."""
        if not active_document_text or "[Browser/Web]" not in (ambient_text or ""):
            return ""
        return "Browser/Web" if cls._is_browser_active_context(context) else "Active document"

    @staticmethod
    def _context_mode(caller: dict[str, Any], name: str) -> str:
        """Handle context mode for flow controller."""
        return tool_modes.context_mode(caller, name)

    def _effective_document_mode(self, caller: dict[str, Any]) -> str:
        """Treat enabled App context as active document context."""
        mode = self._context_mode(caller, "documents")
        if mode == "off" and bool(caller.get("context_ambient", False)):
            return "auto"
        return mode

    def _allowed_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Handle allowed model tools for flow controller."""
        allowed = tool_modes.allowed_model_tools(caller)
        overrides = tool_modes.tool_overrides(caller)
        for item in self._addon_model_tool_payloads():
            name = item["name"]
            server_id = mcp_server_id_from_tool(name, item.get("description", ""))
            group_mode = (
                overrides.get(mcp_server_override_key(server_id))
                if server_id
                else None
            )
            mode = overrides.get(name, group_mode or "on")
            if mode == "off":
                continue
            if name not in allowed:
                allowed.append(name)
        return allowed

    def _pinned_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Tools explicitly pinned by caller policy.

        Context dropdowns in "model" mode mean "offer the tool schema and let
        the model decide whether to call it." The allow-list uses dotted source
        grants like ``get_context.browser``, but the actual schema is named
        ``get_context``, so pin the schema name here.
        """
        return tool_modes.pinned_model_tools(caller)

    def _addon_model_tools(self) -> list[str]:
        """Return enabled addon tool names from the brain-owned addon registry."""
        return [item["name"] for item in self._addon_model_tool_payloads()]

    def _addon_model_tool_payloads(self) -> list[dict[str, str]]:
        """Return enabled addon tool payloads from the brain-owned addon registry."""
        try:
            result = self._safe_call(self.brain, "brain.addons.tools", timeout=3.0) or {}
        except Exception:
            return []
        tools = result.get("tools") if isinstance(result, dict) else []
        payloads: list[dict[str, str]] = []
        for item in tools or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                description = str(item.get("description") or name)
            else:
                name = str(item or "").strip()
                description = name
            if name and name not in {tool["name"] for tool in payloads}:
                payloads.append({"name": name, "description": description})
        return payloads

    def _chat_tool_policy(self, caller: dict[str, Any]) -> tuple[list[str], list[str], str]:
        """Return chat tool grants from the visible chat/caller policy."""
        allowed = self._allowed_model_tools(caller)
        pinned = self._pinned_model_tools(caller)
        file_access_mode = tool_modes.local_file_access_mode(caller)
        return allowed, pinned, file_access_mode

    def _messages_with_chat_context(
        self,
        messages: list,
        caller: dict[str, Any],
        parts: list[tuple[str, str, str]] | None = None,
    ) -> list:
        """Attach selected chat context as hidden system text."""
        if parts is None:
            parts = self._chat_context_parts(caller)
        context_text = "\n\n".join(block for _label, block, _src in parts if block.strip())
        if not context_text:
            return messages
        out = [dict(m) for m in messages]
        block = f"[Current Chat Context]\n{context_text}"
        for msg in out:
            if str(msg.get("role") or "").lower() == "system":
                msg["content"] = f"{str(msg.get('content') or '').rstrip()}\n\n---\n{block}"
                return out
        return [{"role": "system", "content": block}] + out

    def _chat_context_text(self, caller: dict[str, Any]) -> str:
        """Joined prompt text for the frontloaded chat context (model-facing)."""
        parts = self._chat_context_parts(caller)
        return "\n\n".join(block for _label, block, _src in parts if block.strip())

    def _chat_context_parts(self, caller: dict[str, Any]) -> list[tuple[str, str, str]]:
        """Fetch frontloaded chat context as ``(label, prompt_block, preview_source)``.

        ``prompt_block`` is injected verbatim into the model prompt. ``label`` and
        ``preview_source`` feed the display-only per-source snippets shown under the
        user's turn in the chat transcript; those snippets are never sent to the
        model.
        """
        wants_documents = self._effective_document_mode(caller) == "auto"
        wants_browser = self._context_mode(caller, "browser") == "auto"
        wants_clipboard = bool(caller.get("context_clipboard"))
        wants_ambient = bool(caller.get("context_ambient"))
        wants_selection = bool(caller.get("_context_selection_enabled", False))
        if not any((wants_documents, wants_browser, wants_clipboard, wants_ambient, wants_selection)):
            return []

        try:
            context = self._context_snapshot(caller, include_browser=False, preview_context_sources=wants_browser)
        except Exception:
            log.exception("chat context snapshot failed")
            context = {}

        parts: list[tuple[str, str, str]] = []
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if wants_ambient and active_app.get("name"):
            body = f"Active app: {active_app.get('name')}"
            parts.append(("App", f"[App]\n{body}", body))

        if wants_selection:
            selected = str(context.get("selected_text") or "").strip()
            if selected:
                parts.append(("Selection", f"[Selection]\n{selected}", selected))

        if wants_clipboard:
            clipboard = str(context.get("clipboard_text") or "").strip()
            if clipboard:
                parts.append(("Clipboard", f"[Clipboard]\n{clipboard}", clipboard))

        if wants_documents:
            active_document_text = str(context.get("active_document_text") or "").strip()
            if not active_document_text:
                active_document_text = self._fetch_active_document_text(context)
            if active_document_text:
                label = self._active_document_context_label(context)
                block = (
                    f"--- BEGIN ACTIVE DOCUMENT: {label} ---\n"
                    f"{active_document_text}\n"
                    f"--- END ACTIVE DOCUMENT: {label} ---"
                )
                parts.append((f"Document: {label}", block, active_document_text))

        if wants_browser:
            browser = self._fetch_browser_content_for_context(context)
            browser_url = str(browser.get("browser_url") or context.get("browser_url") or "").strip()
            browser_content = str(browser.get("browser_content") or "").strip()
            browser_bits = []
            if browser_url:
                browser_bits.append(f"URL: {browser_url}")
            if browser_content:
                browser_bits.append(browser_content)
            if browser_bits:
                joined = "\n\n".join(browser_bits)
                parts.append(("Browser/Web", f"[Browser/Web]\n{joined}", joined))

        return parts

    def _screenshot_tool_allowed(self, caller: dict[str, Any]) -> bool:
        """Whether capture_screen is exposed: the Screenshot dropdown's "model"
        mode, overridable per-tool from the Allowed Tools list (auto-capture
        stays dropdown-governed)."""
        return tool_modes.screenshot_tool_allowed(caller)

    @staticmethod
    def _tool_overrides(caller: dict[str, Any]) -> dict[str, str]:
        """Handle tool overrides for flow controller."""
        return tool_modes.tool_overrides(caller)

    def _frontloaded_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Handle frontloaded model tools for flow controller."""
        return tool_modes.frontloaded_model_tools(caller)

    @staticmethod
    def _estimate_context_tokens(text: str) -> int:
        """Fast token estimate for context preview chips."""
        return flow_estimates.estimate_context_tokens(text)

    @classmethod
    def _token_label(cls, text: str) -> str:
        """Return a compact token estimate label."""
        return flow_estimates.token_label(text)

    @staticmethod
    def _deferred_token_label() -> str:
        """Return the token label for context fetched after the picker."""
        return flow_estimates.deferred_token_label()

    @staticmethod
    def _image_size_from_b64(data: str | None) -> tuple[int, int] | None:
        """Best-effort PNG/JPEG dimension read for screenshot token estimates."""
        return flow_estimates.image_size_from_b64(data)

    @classmethod
    def _image_size_token_label(cls, size: tuple[int, int] | None) -> str:
        """Return a rough token estimate for an image of known dimensions."""
        return flow_estimates.image_size_token_label(size)

    @classmethod
    def _image_token_label(cls, data: str | None) -> str:
        """Return a rough token estimate for image input."""
        return flow_estimates.image_token_label(data)

    @classmethod
    def _screen_token_label(cls, context: dict[str, Any]) -> str:
        """Return screenshot token estimate from screen metadata."""
        return flow_estimates.screen_token_label(context)

    def _intent_context_keys(self) -> str:
        """Return eight unique overlay-local context toggle keys."""
        raw = str(self._config_value("INTENT_CONTEXT_TOGGLE_KEYS", "12345678") or "12345678")
        keys: list[str] = []
        for ch in raw + "12345678":
            if ch.isspace() or ch in keys:
                continue
            keys.append(ch)
            if len(keys) >= 8:
                break
        return "".join(keys)

    @staticmethod
    def _mode_to_context_state(mode: str) -> str:
        """Map stored context mode to overlay state."""
        mode = (mode or "").strip().lower()
        if mode in {"auto", "on"}:
            return "on"
        if mode == "model":
            return "auto"
        return "off"

    @staticmethod
    def _file_access_to_context_state(mode: str) -> str:
        """Map local file access mode to overlay state."""
        return "off" if (mode or "").strip().lower() == "off" else "auto"

    @staticmethod
    def _context_warning(
        tokens: int,
        *,
        available: bool = True,
        deferred: bool = False,
        deferred_warning: str = "",
    ) -> str:
        """Return the warning text shown when hovering a context warning sign."""
        if deferred:
            return deferred_warning or "This context may be fetched or used after you send the prompt, so this token cost is not known yet."
        if tokens >= 1500:
            return "This context source is large and may cost noticeable input tokens."
        return ""

    @staticmethod
    def _redaction_count(text: str) -> int:
        """Return detected sensitive item count for preview-only privacy badges."""
        if not str(text or "").strip():
            return 0
        try:
            import config
            if not bool(getattr(config, "TRUST_PRIVACY_MODE", True)):
                return 0
            from core.privacy_redaction import redact_with_report

            _redacted, report = redact_with_report(str(text), source="preview")
            return int(report.get("count") or 0)
        except Exception:
            return 0

    @staticmethod
    def _context_preview_text(text: str, limit: int = 180) -> str:
        """Return a compact, privacy-safe snippet for context previews."""
        flat = " ".join(str(text or "").split())
        if not flat:
            return ""
        try:
            import config
            if bool(getattr(config, "TRUST_PRIVACY_MODE", True)):
                from core.privacy_redaction import redact_with_report

                flat, _report = redact_with_report(flat, source="preview")
                flat = " ".join(str(flat or "").split())
        except Exception:
            pass
        if len(flat) <= limit:
            return flat
        return flat[: max(0, limit - 3)].rstrip() + "..."

    def _file_context_progress_texts(self, file_context: list[dict[str, Any]] | None) -> list[str]:
        """Return one-line, display-only summaries for local file tool use."""
        texts: list[str] = []
        seen: set[tuple[str, str, bool]] = set()
        for raw in file_context or []:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool") or "").strip()
            path = str(raw.get("relative_path") or raw.get("path") or "").strip()
            ok = bool(raw.get("ok"))
            if not tool or not path:
                continue
            key = (tool, path, ok)
            if key in seen:
                continue
            seen.add(key)
            if not ok:
                texts.append(t("Tool failed: {tool}: {path}").format(tool=tool, path=path))
            elif tool == "read_file":
                texts.append(t("Read file: {path}").format(path=path))
            elif tool == "list_files":
                texts.append(t("Listed files: {path}").format(path=path))
            else:
                texts.append(t("Used {tool}: {path}").format(tool=tool, path=path))
        return texts

    def _emit_file_context_progress(
        self,
        file_context: list[dict[str, Any]] | None,
        *,
        chat_request_id: str = "",
        conversation_index: int | None = None,
        include_bubble: bool = True,
    ) -> None:
        """Display local-file tool summaries without adding them to reply text."""
        for text in self._file_context_progress_texts(file_context):
            payload = {"text": text, "is_progress": True, "is_thought": True}
            if include_bubble:
                self._safe_call(self.ui, "ui.reply.chunk", payload, timeout=30.0)
            if chat_request_id:
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {"request_id": chat_request_id, **payload},
                    timeout=30.0,
                )
            elif conversation_index is not None:
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {"conversation_index": conversation_index, **payload},
                    timeout=30.0,
                )

    @staticmethod
    def _with_privacy_warning(warning: str, redactions: int) -> str:
        """Append detected-and-censored privacy detail to a context warning."""
        if redactions <= 0:
            return warning
        privacy = t("Privacy: {count} item(s) detected and censored.").format(count=redactions)
        return f"{warning}\n\n{privacy}" if warning else privacy

    def _intent_context_items(self, pending: PendingInvocation | None) -> list[dict[str, Any]]:
        """Build context preview chips for the intent overlay."""
        keys = self._intent_context_keys()
        caller = pending.caller if pending else {}
        context = pending.context if pending else {}
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        document_window = context.get("document_window") if isinstance(context.get("document_window"), dict) else {}
        active_document_text = str(context.get("active_document_text") or "")
        app_source_previews = [
            dict(item)
            for item in (context.get("active_document_sources") or [])
            if isinstance(item, dict) and str(item.get("preview") or "").strip()
        ]
        active_text = " ".join(
            str(part)
            for part in (
                active_app.get("name"),
                active_app.get("process_name"),
                active_app.get("title"),
                document_window.get("process_name"),
                document_window.get("title"),
                active_document_text,
            )
            if part
        )
        app_available = bool(active_text)
        document_state = self._mode_to_context_state(self._effective_document_mode(caller))
        app_on = bool(caller.get("context_ambient", True)) and app_available
        app_state = "on" if app_on or (document_state == "on" and app_available) else ("auto" if document_state == "auto" and app_available else "off")
        app_deferred = app_state != "off" and document_state in {"on", "auto"} and app_available and not active_document_text

        browser_text = "\n".join(
            str(part)
            for part in (
                context.get("browser_url"),
                context.get("browser_content"),
            )
            if part
        )
        browser_available = bool(
            browser_text
            or context.get("browser_hwnd")
            or context.get("browser_app")
        )
        browser_state = self._mode_to_context_state(self._context_mode(caller, "browser"))
        browser_tokens = self._estimate_context_tokens(browser_text)
        browser_requested = browser_state != "off"
        browser_deferred = browser_requested and not context.get("browser_content")

        selected_text = str(context.get("selected_text") or "")
        selected_paths = self._selected_paths_from_context(context)
        selected_path_items = self._path_context_items(selected_paths)
        selected_path_parts: list[str] = []
        for item in selected_path_items:
            name = str(item.get("name") or "Selected file")
            if str(item.get("type") or "") == "image":
                selected_path_parts.append(f"[Image file]\n{name}")
            else:
                selected_path_parts.append(f"[{name}]\n{self._content_to_text(item.get('content'))}".strip())
        selected_path_text = "\n\n".join(part for part in selected_path_parts if part.strip())
        selected_context_text = "\n\n".join(
            part for part in (selected_text, selected_path_text) if part.strip()
        )
        clipboard_text = str(context.get("clipboard_text") or "")
        github_mode = self._context_mode(caller, "github")
        memory_mode = self._context_mode(caller, "memory")
        file_mode = tool_modes.local_file_access_mode(caller)
        screenshot_mode = str(caller.get("context_screenshot") or "off").strip().lower()
        screenshot_preview = (pending.screenshot_b64 or pending.screenshot_tool_b64) if pending else None
        has_screenshot = bool(screenshot_preview)
        screenshot_tokens = (
            self._image_token_label(screenshot_preview)
            if has_screenshot
            else self._screen_token_label(context)
        )
        app_redactions = self._redaction_count(active_text)
        browser_redactions = self._redaction_count(browser_text)
        selected_redactions = self._redaction_count(selected_context_text)
        clipboard_redactions = self._redaction_count(clipboard_text)
        app_preview = self._context_preview_text(active_document_text or active_text)
        if app_source_previews:
            app_preview = str(app_source_previews[0].get("preview") or app_preview)
        browser_preview = self._context_preview_text(browser_text)
        if not browser_preview and browser_requested and browser_available:
            browser_preview = self._context_preview_text(
                context.get("browser_url")
                or context.get("browser_app")
                or "Browser page text may be fetched after you send the prompt."
            )
        selected_preview = self._context_preview_text(selected_context_text)
        clipboard_preview = self._context_preview_text(clipboard_text)

        screenshot_state = "on" if (screenshot_mode == "auto" or (pending and pending.screenshot_b64)) else (
            "auto" if screenshot_mode == "model" else "off"
        )

        return [
            {
                "id": "ambient",
                "key": keys[0],
                "label": "App",
                "state": app_state,
                "tokens": self._deferred_token_label() if app_deferred else self._token_label(active_text),
                "preview": app_preview,
                "sources": app_source_previews,
                "privacy_count": app_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        self._estimate_context_tokens(active_text),
                        available=app_available,
                        deferred=app_deferred,
                        deferred_warning="Active app or document context may be fetched after you send the prompt, so this token cost is not known yet.",
                    ) if app_state != "off" else "",
                    app_redactions,
                ),
            },
            {
                "id": "browser",
                "key": keys[1],
                "label": "Browser/Web",
                "state": browser_state if browser_requested else "off",
                "tokens": self._deferred_token_label() if browser_deferred else self._token_label(browser_text),
                "preview": browser_preview,
                "privacy_count": browser_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        browser_tokens,
                        available=browser_available,
                        deferred=browser_deferred,
                        deferred_warning="Browser page text may be fetched after you send the prompt, so this token cost is not known yet.",
                    ) if browser_state != "off" else "",
                    browser_redactions,
                ),
            },
            {
                "id": "selection",
                "key": keys[2],
                "label": "Selection",
                "available": True,
                "state": "on" if selected_context_text else "off",
                "tokens": self._token_label(selected_context_text) if selected_context_text else "",
                "preview": selected_preview,
                "privacy_count": selected_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        self._estimate_context_tokens(selected_context_text),
                        available=bool(selected_context_text),
                    ) if selected_context_text else "",
                    selected_redactions,
                ),
            },
            {
                "id": "clipboard",
                "key": keys[3],
                "label": "Clipboard",
                "state": "on" if caller.get("context_clipboard") and clipboard_text else "off",
                "tokens": self._token_label(clipboard_text),
                "preview": clipboard_preview,
                "privacy_count": clipboard_redactions,
                "warning": self._with_privacy_warning(
                    self._context_warning(
                        self._estimate_context_tokens(clipboard_text),
                        available=bool(clipboard_text),
                    ) if caller.get("context_clipboard") else "",
                    clipboard_redactions,
                ),
            },
            {
                "id": "screenshot",
                "key": keys[4],
                "label": "Screenshot",
                "state": screenshot_state,
                "tokens": screenshot_tokens,
                "warning": "",
            },
            {
                "id": "github",
                "key": keys[5],
                "label": "Git/GitHub",
                "state": self._mode_to_context_state(github_mode),
                "tokens": self._deferred_token_label() if github_mode != "off" else "0 tok",
                "warning": self._context_warning(0, deferred=True) if github_mode != "off" else "",
            },
            {
                "id": "memory",
                "key": keys[6],
                "label": "Memory",
                "state": self._mode_to_context_state(memory_mode),
                "tokens": self._deferred_token_label() if memory_mode != "off" else "0 tok",
                "warning": "Memory tokens are estimated after the prompt is known." if memory_mode != "off" else "",
            },
            {
                "id": "files",
                "key": keys[7],
                "label": "Files",
                "state": self._file_access_to_context_state(file_mode),
                "tokens": "",
                "warning": "",
            },
        ]

    @staticmethod
    def _apply_intent_context_choices(
        caller: dict[str, Any],
        choices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply per-prompt context chip choices to a caller policy copy."""
        updated = dict(caller or {})
        for item in choices or []:
            source = str(item.get("id") or "")
            state = str(item.get("state") or "off").lower()
            if source == "ambient":
                updated["context_ambient"] = state != "off"
                default_state = str(item.get("default_state") or state).lower()
                touched = bool(item.get("touched")) or state != default_state
                if state == "off":
                    updated["context_documents_mode"] = "off"
                elif touched:
                    updated["context_documents_mode"] = "model" if state == "auto" else "auto"
            elif source == "browser":
                updated["context_browser_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
            elif source == "selection":
                updated["_context_selection_enabled"] = state != "off"
            elif source == "clipboard":
                updated["context_clipboard"] = state != "off"
            elif source == "screenshot":
                updated["context_screenshot"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
                updated["_context_screenshot_enabled"] = state != "off"
            elif source == "github":
                updated["context_github_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
            elif source == "memory":
                updated["context_memory_mode"] = "off" if state == "off" else ("model" if state == "auto" else "on")
            elif source == "files":
                if state == "off":
                    updated["file_access"] = "off"
                elif tool_modes.local_file_access_mode(updated) == "off":
                    updated["file_access"] = "ask"
        return updated

    def _consume_context_extras(self) -> tuple[list[str], list[dict[str, Any]]]:
        """Handle consume context extras for flow controller."""
        buffered = list(self._context_buffer)
        dropped = list(self._drop_context_items)
        self._context_buffer.clear()
        self._drop_context_items.clear()
        if dropped:
            self._safe_call(self.ui, "ui.context.clear", timeout=30.0)
        return buffered, dropped

    @staticmethod
    def _normalize_context_item(item: Any) -> dict[str, Any]:
        """Normalize context item."""
        if isinstance(item, dict):
            return {
                "name": str(item.get("name") or item.get("label") or "Context"),
                "content": item.get("content", ""),
                "type": str(item.get("type") or item.get("item_type") or "text"),
            }
        return {"name": "Context", "content": str(item), "type": "text"}

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """Handle content to text for flow controller."""
        if content is None:
            return ""
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        if isinstance(content, (dict, list, tuple)):
            return json_safe_dumps(content)
        return str(content)

    def _image_content_b64(self, content: Any) -> str | None:
        """Handle image content b64 for flow controller."""
        if isinstance(content, str):
            try:
                p = Path(content).expanduser()
                if p.exists():
                    return self._file_b64(p)
            except (OSError, ValueError):
                pass
            cleaned = content.strip()
            if cleaned:
                return cleaned
        return None

    @staticmethod
    def _short(text: str, n: int = 24) -> str:
        """Handle short for flow controller."""
        return flow_utils.short(text, n)

    @staticmethod
    def _selected_paths_from_context(context: dict[str, Any]) -> list[str]:
        """Return normalized selected file/folder paths from a native snapshot."""
        raw_paths = context.get("selected_paths") if isinstance(context, dict) else []
        if not isinstance(raw_paths, list):
            return []
        seen: set[str] = set()
        paths: list[str] = []
        for raw in raw_paths:
            path = str(raw or "").strip()
            if not path:
                continue
            try:
                key = os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))
            except Exception:
                key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths

    @staticmethod
    def _path_context_items(paths: Any) -> list[dict[str, Any]]:
        """Build dropped-context items for explicitly selected file/folder paths."""
        selected_paths = FlowController._selected_paths_from_context({"selected_paths": paths or []})
        items: list[dict[str, Any]] = []
        for raw_path in selected_paths:
            path = Path(raw_path).expanduser()
            path_text = str(path)
            name = path.name or path_text
            ext = path.suffix.lower()
            if path.is_dir():
                items.append({"name": name, "content": f"[Folder: {path_text}]", "type": "file"})
                continue
            if ext in _SELECTED_PATH_IMAGE_EXTS:
                try:
                    content = base64.b64encode(path.read_bytes()).decode("ascii")
                    items.append({"name": name, "content": content, "type": "image"})
                except OSError:
                    items.append({"name": name, "content": f"[Image file: {path_text}]", "type": "file"})
                continue
            if ext in _SELECTED_PATH_TEXT_EXTS or ext == "":
                try:
                    content = path.read_bytes()[:_SELECTED_PATH_TEXT_BYTES].decode("utf-8", errors="replace")
                    items.append({"name": name, "content": content, "type": "text"})
                except OSError:
                    items.append({"name": name, "content": f"[File: {path_text}]", "type": "file"})
                continue
            if ext in _SELECTED_PATH_DOCUMENT_EXTS:
                try:
                    from core.llm_clients.client import read_document_file

                    content = str(read_document_file(path_text) or "").strip()
                except Exception:
                    content = ""
                if content:
                    items.append({"name": name, "content": content, "type": "text"})
                else:
                    items.append({"name": name, "content": f"[File: {path_text}]", "type": "file"})
                continue
            items.append({"name": name, "content": f"[File: {path_text}]", "type": "file"})
        return items

    def _context_summary_badges(
        self,
        *,
        selected: str,
        screenshot_b64: str | None,
        buffered_items: list[str],
        drop_items: list[dict[str, Any]],
        clipboard_text: str,
        ambient_text: str,
        active_document_text: str,
    ) -> list[dict[str, str]]:
        """Handle context summary badges for flow controller."""
        items: list[dict[str, str]] = []

        def add_source(label: str, item_type: str) -> None:
            if not any(item.get("label") == label for item in items):
                items.append({"label": label, "type": item_type})

        if screenshot_b64:
            add_source("Screenshot", "image")
        if selected:
            add_source("Selection", "text")
        for item in drop_items:
            items.append(
                {
                    "label": self._short(str(item.get("name") or "Context"), 24),
                    "type": "image" if item.get("type") == "image" else "file",
                }
            )
        for buffered in buffered_items:
            items.append({"label": self._short(buffered, 24), "type": "text"})
        if clipboard_text:
            add_source("Clipboard", "text")
        if active_document_text:
            add_source("App", "file")
        if "[Browser/Web]" in (ambient_text or ""):
            add_source("Browser/Web", "file")
        ambient_without_browser = (ambient_text or "").replace("[Browser/Web]", "").strip()
        if ambient_without_browser:
            add_source("App", "file")
        return items[:8]

    def _capture_fullscreen_b64(self) -> str | None:
        """Handle capture fullscreen b64 for flow controller."""
        started = time.monotonic()
        result = self.native.call("native.capture.fullscreen", timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        image_b64 = self._file_b64(path) if path else None
        log.info(
            "auto screenshot capture ok=%s path=%r size=%s b64_chars=%d error=%r after %.2fs",
            result.get("ok") if isinstance(result, dict) else None,
            path,
            result.get("size") if isinstance(result, dict) else None,
            len(image_b64 or ""),
            result.get("error") if isinstance(result, dict) else None,
            time.monotonic() - started,
        )
        return image_b64

    def _capture_model_tool_b64(self) -> str:
        """Handle capture model tool b64 for flow controller."""
        started = time.monotonic()
        try:
            result = self.native.call("native.capture.fullscreen", timeout=8.0)
        except Exception:
            log.exception("model screenshot pre-capture failed after %.2fs", time.monotonic() - started)
            return ""
        path = result.get("path") if isinstance(result, dict) else ""
        image_b64 = self._file_b64(path) or ""
        log.info(
            "model screenshot pre-capture %s path=%r size=%s error=%r after %.2fs",
            "succeeded" if image_b64 else "returned empty",
            path,
            result.get("size") if isinstance(result, dict) else None,
            result.get("error") if isinstance(result, dict) else None,
            time.monotonic() - started,
        )
        return image_b64

    @staticmethod
    def _file_b64(path: str | Path | None) -> str | None:
        """Handle file b64 for flow controller."""
        return flow_estimates.file_b64(path)

    def _tts_enabled(self) -> bool:
        """Handle TTS enabled for flow controller."""
        import config

        return str(getattr(config, "TTS_PROVIDER", "none")).strip().lower() != "none"

    def _tts_replies_enabled(self) -> bool:
        """Return whether assistant replies should be spoken automatically."""
        import config

        return self._tts_enabled() and bool(getattr(config, "TTS_SPEAK_REPLIES", False))

    def _tts_sequence_is_active(self) -> bool:
        """Return whether a segmented TTS queue owns playback state."""
        with self._tts_lock:
            return self._tts_sequence_active

    def _cancel_tts_sequence(self, generation: int) -> None:
        """Cancel queued TTS segments for one generation."""
        with self._tts_lock:
            q = self._tts_queue if self._tts_generation == generation else None
            if q is not None:
                self._tts_queue = None
                self._tts_sequence_active = False
        if q is not None:
            q.put(None)

    def _ensure_tts_sequence(self, generation: int) -> "queue.Queue[str | None]":
        """Create or return the segmented TTS queue for this generation."""
        if self._reply_bubble_cancelled(generation):
            raise RuntimeError("reply bubble output is muted for this generation")
        with self._tts_lock:
            if self._tts_queue is not None and self._tts_generation == generation:
                return self._tts_queue
            q: "queue.Queue[str | None]" = queue.Queue()
            self._tts_generation = generation
            self._tts_queue = q
            self._tts_sequence_active = True
            threading.Thread(target=self._tts_sequence_worker, args=(generation, q), daemon=True).start()
            return q

    def _queue_tts_segment(self, generation: int, text: str) -> None:
        """Queue one completed reply segment for TTS playback."""
        segment = " ".join((text or "").split())
        if not segment or not self._is_current(generation) or self._reply_bubble_cancelled(generation):
            return
        try:
            q = self._ensure_tts_sequence(generation)
        except RuntimeError:
            return
        self._safe_call(self.ui, "ui.reply.track_speech", timeout=30.0)
        q.put(segment)

    def _finish_tts_sequence(self, generation: int) -> None:
        """Close the segmented TTS queue for this generation."""
        if self._reply_bubble_cancelled(generation):
            return
        with self._tts_lock:
            q = self._tts_queue if self._tts_generation == generation else None
        if q is not None:
            q.put(None)

    def _tts_sequence_worker(self, generation: int, q: "queue.Queue[str | None]") -> None:
        """Synthesize and play queued TTS segments sequentially."""
        try:
            while self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                segment = q.get()
                if segment is None:
                    break
                if not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    break
                played = self._speak_text(segment, generation=generation, wait_for_playback=True)
                if not played or not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    break
        finally:
            with self._tts_lock:
                if self._tts_queue is q:
                    self._tts_queue = None
                    self._tts_sequence_active = False
            if self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()

    def _begin_manual_tts_sequence(self, generation: int) -> None:
        """Mark playback as owned by a manual TTS flow."""
        with self._tts_lock:
            self._tts_generation = generation
            self._tts_queue = None
            self._tts_sequence_active = True

    def _end_manual_tts_sequence(self, generation: int) -> None:
        """Release manual TTS playback ownership."""
        with self._tts_lock:
            if self._tts_generation == generation and self._tts_queue is None:
                self._tts_sequence_active = False

    @staticmethod
    def _read_aloud_chunks(text: str) -> list[str]:
        """Split read-aloud text into responsive TTS chunks."""
        import config

        min_words = max(1, int(getattr(config, "TTS_READ_ALOUD_MIN_WORDS", _READ_ALOUD_MIN_WORDS)))
        max_words = max(min_words, int(getattr(config, "TTS_READ_ALOUD_MAX_WORDS", _READ_ALOUD_MAX_WORDS)))
        words = re.findall(r"\S+", text or "")
        if not words:
            return []
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            current.append(word)
            word_count = len(current)
            should_split = (
                word_count >= min_words
                and _READ_ALOUD_PAUSE_RE.search(word) is not None
            ) or word_count >= max_words
            if should_split:
                chunks.append(" ".join(current))
                current = []
        if current:
            chunks.append(" ".join(current))
        return chunks

    def _read_aloud_text(self, text: str, *, generation: int) -> bool:
        """Read selected text with one synthesized chunk buffered ahead."""
        chunks = self._read_aloud_chunks(text)
        if not chunks or not self._is_current(generation) or self._reply_bubble_cancelled(generation):
            return False
        if len(chunks) == 1:
            played = False
            reported_error = False
            self._begin_manual_tts_sequence(generation)
            try:
                try:
                    result = self.audio.call("audio.tts.synthesize", {"text": chunks[0]}, timeout=180.0)
                except Exception as exc:  # noqa: BLE001 - keep read-aloud user-facing
                    if "warming up" in str(exc).lower():
                        self._notice("Local voice is still warming up. Try again when Wisp says local speech is ready.")
                        reported_error = True
                    else:
                        log.exception("read selection aloud synthesis failed")
                    return reported_error
                path = result.get("path") if isinstance(result, dict) else ""
                if not path:
                    log.error("read selection aloud synthesis returned no path: %r", result)
                    return False
                if not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    return False
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                play_result = self.audio.call("audio.play_file", {"path": path}, timeout=180.0)
                played = not (isinstance(play_result, dict) and play_result.get("stopped"))
                return played
            except Exception:
                log.exception("read selection aloud playback failed")
                return played
            finally:
                self._end_manual_tts_sequence(generation)
                if self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                    self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                    self._set_idle()

        synth_queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue(maxsize=1)
        stop_synth = threading.Event()

        def put_synth_result(item: dict[str, Any] | None) -> bool:
            while not stop_synth.is_set():
                try:
                    synth_queue.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def synthesize_ahead() -> None:
            try:
                for chunk in chunks:
                    if (
                        stop_synth.is_set()
                        or not self._is_current(generation)
                        or self._reply_bubble_cancelled(generation)
                    ):
                        break
                    try:
                        result = self.audio.call("audio.tts.synthesize", {"text": chunk}, timeout=180.0)
                    except Exception as exc:  # noqa: BLE001 - surface playback failure below
                        put_synth_result({"error": exc})
                        return
                    if not put_synth_result({"chunk": chunk, "result": result}):
                        return
            finally:
                put_synth_result(None)

        self._begin_manual_tts_sequence(generation)
        threading.Thread(target=synthesize_ahead, daemon=True).start()
        played_any = False
        reported_error = False
        try:
            while self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                try:
                    item = synth_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                error = item.get("error")
                if error is not None:
                    if "warming up" in str(error).lower():
                        self._notice("Local voice is still warming up. Try again when Wisp says local speech is ready.")
                        reported_error = True
                        break
                    log.error(
                        "read selection aloud synthesis failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    break
                result = item.get("result")
                path = result.get("path") if isinstance(result, dict) else ""
                if not path:
                    break
                if not self._is_current(generation) or self._reply_bubble_cancelled(generation):
                    break
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                play_result = self.audio.call("audio.play_file", {"path": path}, timeout=180.0)
                if isinstance(play_result, dict) and play_result.get("stopped"):
                    break
                played_any = True
            return played_any or reported_error
        except Exception:
            log.exception("read selection aloud playback failed")
            return played_any
        finally:
            stop_synth.set()
            self._end_manual_tts_sequence(generation)
            if self._is_current(generation) and not self._reply_bubble_cancelled(generation):
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()

    def _speak_text(
        self,
        text: str,
        *,
        generation: int | None = None,
        wait_for_playback: bool = False,
    ) -> bool:
        """Handle speak text for flow controller."""
        if generation is not None and (
            not self._is_current(generation) or self._reply_bubble_cancelled(generation)
        ):
            return False
        try:
            result = self.audio.call("audio.tts.synthesize", {"text": text}, timeout=180.0)
            path = result.get("path") if isinstance(result, dict) else ""
            if path:
                if generation is not None and (
                    not self._is_current(generation) or self._reply_bubble_cancelled(generation)
                ):
                    return False
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                # Buffer Cartesia word timestamps in the bubble *before* playback
                # starts. start_word_reveal â€” fired by the audio.playback.started
                # event below â€” drains them anchored to the real audio clock, so
                # the word highlight tracks the spoken voice instead of the
                # normal bubble reveal speed. Do NOT call ui.reply.start_reveal here: it would
                # anchor the reveal to synth-completion (before audio is audible)
                # and the playback-started reveal would then cancel it.
                wts = result.get("word_timestamps") if isinstance(result, dict) else None
                if isinstance(wts, dict) and wts.get("words") and not wts.get("estimated"):
                    self._safe_call(
                        self.ui,
                        "ui.reply.schedule_words",
                        {"words": wts.get("words"), "start_ms": wts.get("start_ms")},
                        timeout=30.0,
                    )
                if wait_for_playback:
                    play_result = self.audio.call("audio.play_file", {"path": path}, timeout=180.0)
                    if isinstance(play_result, dict) and play_result.get("stopped"):
                        return False
                else:
                    self.audio.call("audio.play_file", {"path": path}, wait=False)
                return True
            else:
                if not wait_for_playback:
                    self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                    self._set_idle()
                return False
        except Exception:
            log.exception("audio playback failed")
            if not wait_for_playback:
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()
            return False
