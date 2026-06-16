"""Product flow controller for the pure-Python worker target."""

from __future__ import annotations

import base64
import itertools
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger("wisp.macos_py.flows")
_INTERACTIVE_LLM_TIMEOUT_SECONDS = 120.0
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


class WorkerLike(Protocol):
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        ...

    def on_event(self, event: str, handler) -> None:
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
        ...


@dataclass
class PendingInvocation:
    caller_idx: int = 0
    caller: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    screenshot_b64: str | None = None
    screenshot_tool_b64: str | None = None
    intent_target_pid: int = 0
    paste_target_pid: int = 0
    is_snip: bool = False


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
        self._last_reply = ""
        self._active_agent_stream_id: Any = None
        self._config_mtime = self._current_config_mtime()

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.native.on_event("native.hotkey", self._on_native_hotkey)
        self.ui.on_event("ui.summon_caller", self._on_summon_caller)
        self.ui.on_event("ui.request_snip", self._on_request_snip)
        self.ui.on_event("ui.intent.chosen", self._on_intent_chosen)
        self.ui.on_event("ui.intent.cancelled", self._on_intent_cancelled)
        self.ui.on_event("ui.snip.region", self._on_snip_region)
        self.ui.on_event("ui.snip.cancelled", self._on_snip_cancelled)
        self.ui.on_event("ui.context.dropped", self._on_context_dropped)
        self.ui.on_event("ui.context.remove", self._on_context_remove)
        self.ui.on_event("ui.chat.request", self._on_chat_request)
        self.ui.on_event("ui.memory.open_requested", self._on_memory_open_requested)
        self.ui.on_event("ui.memory.add", self._on_memory_add)
        self.ui.on_event("ui.memory.update", self._on_memory_update)
        self.ui.on_event("ui.memory.delete", self._on_memory_delete)
        self.ui.on_event("ui.plugins.open_requested", self._on_plugins_open_requested)
        self.ui.on_event("ui.plugins.run_action", self._on_plugins_run_action)
        self.ui.on_event("ui.plugins.set_enabled", self._on_plugins_set_enabled)
        self.ui.on_event("ui.plugins.set_setting", self._on_plugins_set_setting)
        self.ui.on_event("ui.plugins.repair_environment", self._on_plugins_repair_environment)
        self.ui.on_event("ui.plugins.install_archive", self._on_plugins_install_archive)
        self.ui.on_event("ui.plugins.install_folder", self._on_plugins_install_folder)
        self.ui.on_event("ui.agent.task_requested", self._on_agent_task_requested)
        self.ui.on_event("ui.agent.history_requested", self._on_agent_history_requested)
        self.ui.on_event("ui.agent.run_requested", self._on_agent_run_requested)
        self.ui.on_event("ui.agent.cancel_requested", self._on_agent_cancel_requested)
        self.ui.on_event("ui.agent.approval.respond", self._on_agent_approval_respond)
        self.ui.on_event("ui.agent.history.refresh", self._on_agent_history_refresh)
        self.ui.on_event("ui.agent.history.read", self._on_agent_history_read)
        self.ui.on_event("ui.agent.history.retry", self._on_agent_history_retry)
        self.ui.on_event("ui.agent.history.continue", self._on_agent_history_continue)
        self.ui.on_event("ui.settings.applied", self._on_settings_applied)
        self.ui.on_event("ui.bubble.speed", self._on_bubble_speed)
        self.brain.on_event("reply.chunk", self._on_reply_chunk)
        self.brain.on_event("reply.done", self._on_reply_done)
        self.brain.on_event("agent.log", self._forward_agent_event("ui.agent.log"))
        self.brain.on_event("agent.trace", self._forward_agent_event("ui.agent.trace"))
        self.brain.on_event("agent.done", self._forward_agent_event("ui.agent.done"))
        self.brain.on_event("agent.approval.request", self._on_agent_approval_request)
        self.audio.on_event("audio.playback.started", self._on_audio_playback_started)
        self.audio.on_event("audio.playback.done", self._on_audio_playback_done)
        self.ui.call("ui.show_overlay", timeout=30.0)
        try:
            self.audio.call("audio.prewarm", timeout=30.0, wait=False)
        except Exception:
            log.exception("audio prewarm did not start")

    def start_hotkeys(self) -> dict[str, Any]:
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
            self._schedule(self.plugin_run_hotkey, data or {})

    def _on_summon_caller(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.begin_caller, int((data or {}).get("caller_idx") or 0))

    def _on_request_snip(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.begin_snip)

    def _on_intent_chosen(self, data: dict[str, Any], _req_id: Any = None) -> None:
        prompt = str((data or {}).get("custom") or (data or {}).get("prompt") or "").strip()
        self._schedule(self.intent_chosen, prompt)

    def _on_intent_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._pending = None
        self._set_idle()

    def _on_snip_region(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.snip_region_selected, data or {})

    def _on_snip_cancelled(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._pending = None
        self._set_idle()

    def _on_context_dropped(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.context_items_dropped, list((data or {}).get("items") or []))

    def _on_context_remove(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.remove_context_item, int((data or {}).get("index") or 0))

    def _on_chat_request(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.chat_request, data or {})

    def _on_memory_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.open_memory)

    def _on_memory_add(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.memory_add, data or {})

    def _on_memory_update(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.memory_update, data or {})

    def _on_memory_delete(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.memory_delete, data or {})

    def _on_plugins_open_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.open_plugins)

    def _on_plugins_run_action(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.plugin_run_action, data or {})

    def _on_plugins_set_enabled(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.plugin_set_enabled, data or {})

    def _on_plugins_set_setting(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.plugin_set_setting, data or {})

    def _on_plugins_repair_environment(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.plugin_repair_environment, data or {})

    def _on_plugins_install_archive(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.plugin_install_archive, data or {})

    def _on_plugins_install_folder(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.plugin_install_folder, data or {})

    def _on_agent_task_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.open_agent_task)

    def _on_agent_history_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.open_agent_history)

    def _on_agent_run_requested(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.run_agent_task, dict((data or {}).get("spec") or {}))

    def _on_agent_cancel_requested(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.cancel_agent_task)

    def _on_agent_approval_respond(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.respond_agent_approval, data or {})

    def _on_agent_history_refresh(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.open_agent_history)

    def _on_agent_history_read(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.read_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_agent_history_retry(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.retry_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_agent_history_continue(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.continue_agent_history, str((data or {}).get("run_dir") or ""))

    def _on_settings_applied(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._schedule(self.reload_settings)

    def _on_bubble_speed(self, data: dict[str, Any], _req_id: Any = None) -> None:
        self._safe_call(
            self.audio,
            "audio.speed_boost",
            {"enabled": bool((data or {}).get("enabled"))},
            timeout=5.0,
        )

    def _on_audio_playback_started(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
        self._safe_call(self.ui, "ui.reply.start_reveal", timeout=30.0)

    def _on_audio_playback_done(self, _data: dict[str, Any], _req_id: Any = None) -> None:
        self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
        self._set_idle()

    def _on_reply_chunk(self, data: dict[str, Any], _req_id: Any = None) -> None:
        text = str((data or {}).get("text") or "")
        if text:
            self._safe_call(self.ui, "ui.reply.chunk", {"text": text}, timeout=30.0)

    def _on_reply_done(self, data: dict[str, Any], _req_id: Any = None) -> None:
        text = str((data or {}).get("text") or "")
        if text:
            self._last_reply = text
        # flush=False: the LLM finished streaming but no audio will pace the
        # bubble (this path only runs with TTS off) — let the WPM reveal drain
        # at BUBBLE_REVEAL_WPM instead of slamming the full reply in at once.
        self._safe_call(self.ui, "ui.reply.done", {"flush": False}, timeout=30.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "idle"}, timeout=30.0)

    def _forward_agent_event(self, event_name: str):
        def forward(data: Any, _req_id: Any = None) -> None:
            log.debug("%s: %s", event_name, data)
            self._safe_call(self.ui, event_name, {"data": data}, timeout=30.0)

        return forward

    def _on_agent_approval_request(self, data: Any, _req_id: Any = None) -> None:
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
        import time

        t0 = time.monotonic()
        self._reload_supervisor_config_if_changed()
        caller = self._caller(caller_idx)
        self._log_caller_runtime(caller_idx, caller)
        self._new_generation()
        # Silence any in-progress speech, but don't block the picker waiting for
        # it — audio.stop just flips a flag in the audio worker.
        self._fire(self.audio, "audio.stop")
        context = self._context_snapshot(caller, include_browser=False)
        t_ctx = time.monotonic()
        screenshot_b64 = None
        screenshot_tool_b64 = None
        if caller.get("context_screenshot") == "auto":
            screenshot_b64 = self._capture_fullscreen_b64()
        elif self._screenshot_tool_allowed(caller):
            screenshot_tool_b64 = self._capture_model_tool_b64()
        t_shot = time.monotonic()
        active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
        if str(context.get("platform") or "") == "darwin":
            target_id = int(active_app.get("pid") or 0)
        else:
            target_id = int(active_app.get("window_id") or active_app.get("pid") or 0)
        pending = PendingInvocation(
            caller_idx=caller_idx,
            caller=caller,
            context=context,
            screenshot_b64=screenshot_b64,
            screenshot_tool_b64=screenshot_tool_b64,
            intent_target_pid=target_id,
            paste_target_pid=target_id if caller.get("paste_back") else 0,
        )
        with self._lock:
            self._pending = pending
        # Show the picker first and do not wait for the UI worker's ack. Some
        # document apps can make the UI process slow to respond; the hotkey path
        # should not sit orange before the picker appears.
        self._fire(
            self.ui,
            "ui.show_intent",
            {"caller_idx": caller_idx, "target_hwnd": target_id},
        )
        t_show = time.monotonic()
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        log.info(
            "caller %d shown: context=%.2fs screenshot=%.2fs show_intent=%.2fs total=%.2fs",
            caller_idx, t_ctx - t0, t_shot - t_ctx, t_show - t_shot, t_show - t0,
        )

    def begin_snip(self) -> None:
        self._new_generation()
        # Show the selector FIRST; it must never wait on audio teardown or
        # cosmetic UI state. Stopping audio and the "listening" animation are
        # fired afterwards without blocking (mirrors begin_caller). Previously the
        # blocking audio.stop call delayed the overlay once the audio worker was
        # busy — fast on the first snip, slow on later ones.
        t0 = time.monotonic()
        self.ui.call("ui.show_snip", timeout=30.0)
        log.info("snip: ui.show_snip round-trip %.2fs", time.monotonic() - t0)
        self._fire(self.audio, "audio.stop")
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})

    def snip_region_selected(self, region: dict[str, Any]) -> None:
        result = self.native.call("native.capture.region", {"region": region}, timeout=30.0)
        path = result.get("path") if isinstance(result, dict) else ""
        screenshot_b64 = self._file_b64(path) if path else None
        caller = self._caller(0)
        caller.update(
            {
                "context_ambient": self._config_value("SNIP_CONTEXT_AMBIENT", True),
                "context_documents": self._config_value("SNIP_CONTEXT_DOCUMENTS", False),
                "context_tools": self._config_value("SNIP_CONTEXT_TOOLS", False),
                "context_screenshot": "off",
                "paste_back": False,
            }
        )
        with self._lock:
            self._pending = PendingInvocation(
                caller_idx=0,
                caller=caller,
                context=self._context_snapshot(caller),
                screenshot_b64=screenshot_b64,
                is_snip=True,
            )
        self.ui.call("ui.show_intent", {"caller_idx": 0}, timeout=30.0)

    def intent_chosen(self, prompt: str) -> None:
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            pending = PendingInvocation(caller_idx=0, caller=self._caller(0), context=self._context_snapshot({}))
        if not prompt:
            prompt = "What is this?"
        if pending.caller.get("paste_back"):
            self._rewrite_and_paste(prompt, pending)
        else:
            self._query(prompt, pending)

    def add_context(self) -> None:
        context = self._context_snapshot({"context_clipboard": True})
        text = str(context.get("selected_text") or context.get("clipboard_text") or "").strip()
        if not text:
            self._notice("No selected text or clipboard text to add.")
            return
        # Show the added context as a removable badge to the right of the icon,
        # exactly like a dropped file -- not as a speech-bubble notice. Routing
        # it through _drop_context_items keeps the badge's X-to-remove indexing
        # consistent with remove_context_item.
        name = f"Selection - {self._short(text, 18)}"
        self._drop_context_items.append({"name": name, "content": text, "type": "text"})
        self._fire(self.ui, "ui.context.add_item", {"name": name, "item_type": "text"})

    def clear_context(self) -> None:
        self._context_buffer.clear()
        self._drop_context_items.clear()
        # The panel visibly empties (ui.context.clear), so no bubble notice.
        self._safe_call(self.ui, "ui.context.clear", timeout=30.0)

    def context_items_dropped(self, items: list[dict[str, Any]]) -> None:
        cleaned = [self._normalize_context_item(item) for item in items]
        self._drop_context_items.extend(cleaned)

    def remove_context_item(self, index: int) -> None:
        if 0 <= index < len(self._drop_context_items):
            self._drop_context_items.pop(index)

    def voice_start(self) -> None:
        self._reload_supervisor_config_if_changed()
        self._ensure_voice_start_claimed()
        self._new_generation()
        caller = self._voice_caller()
        self._voice_context = {}
        self._voice_screenshot_b64 = None
        self._fire(self.audio, "audio.stop")
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        self._fire(self.ui, "ui.reply.listening")
        self.audio.call("audio.record.start", timeout=20.0)
        if not self._mark_voice_recording():
            return
        # include_browser=False keeps a slow page fetch off the record-start
        # path; _brain_query_params fetches it lazily at query time instead.
        self._voice_context = self._context_snapshot(caller, include_browser=False)
        # Capture AFTER recording starts so the screenshot overlaps the speech
        # instead of delaying the record start.
        if caller.get("context_screenshot") == "auto":
            self._voice_screenshot_b64 = self._capture_fullscreen_b64()

    def voice_stop(self) -> None:
        if not self._ensure_voice_stop_claimed():
            return
        self._fire(self.ui, "ui.overlay.state", {"state": "thinking"})
        # The first transcription after launch blocks on the (slow) model load /
        # warmup. Tell the user that's what's happening instead of leaving them
        # staring at the generic "thinking" dots wondering why it's slow.
        if self._stt_warming():
            self._fire(self.ui, "ui.reply.notice",
                       {"text": "Warming up speech model — the first transcription is slower…"})
        else:
            self._fire(self.ui, "ui.reply.thinking")
        try:
            result = self.audio.call("audio.record.stop_transcribe", timeout=180.0)
            text = str((result or {}).get("text") or "").strip()
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
        self._fire(self.ui, "ui.overlay.state", {"state": "listening"})
        self._fire(self.ui, "ui.reply.listening")
        try:
            self.audio.call("audio.record.start", timeout=20.0)
        except Exception as exc:  # noqa: BLE001 — surface mic/worker failure in the UI
            log.exception("dictation record start failed")
            self._notice(f"Couldn't start dictation: {self._friendly_error(exc)}")
            self._mark_dictate_idle()
            self._set_idle()

    def dictate_stop(self) -> None:
        """Stop dictation, transcribe, optionally LLM-clean, and paste into the
        text field that was focused when recording started."""
        try:
            if self._stt_warming():
                self._fire(self.ui, "ui.reply.notice",
                           {"text": "Warming up speech model — the first transcription is slower…"})
            try:
                result = self.audio.call("audio.record.stop_transcribe", timeout=180.0)
            except Exception as exc:  # noqa: BLE001 — surface transcribe failure in the UI
                log.exception("dictation transcribe failed")
                self._notice(f"Dictation failed: {self._friendly_error(exc)}")
                self._set_idle()
                return
            text = str((result or {}).get("text") or "").strip()
            if not text:
                self._fire(self.ui, "ui.reply.reset")
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
        except Exception:  # noqa: BLE001 — never block a paste on cleanup
            log.exception("dictation LLM cleanup failed; pasting raw transcript")
            return text

    def _paste_dictation(self, text: str) -> None:
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
        self._fire(self.ui, "ui.reply.reset")
        self._set_idle()
        if paste.get("ok"):
            return  # silent success — the pasted text is the confirmation
        if paste.get("clipboard_ok"):
            self._native_notify(
                "Wisp — dictation on clipboard",
                f"Couldn't focus the field. Press {self._paste_shortcut()} to paste.",
            )
        else:
            log.error("dictation paste failed: %s", paste.get("error") or paste)
            self._native_notify("Wisp — dictation failed", "Couldn't paste the text. See native.stderr.log.")

    def reload_settings(self) -> None:
        import config

        config.reload()
        self._config_mtime = self._current_config_mtime()
        log.info("supervisor config reloaded")
        self._safe_call(self.brain, "brain.config.reload", timeout=30.0)
        # The audio worker owns the live TTS path and is long-lived, so it must
        # reload config + drop cached TTS connections here — prewarm alone leaves
        # the old provider/voice in effect until restart.
        self._safe_call(self.audio, "audio.config.reload", timeout=30.0)
        # The native worker is a separate long-lived process; reload its config
        # before re-registering hotkeys or it re-binds the OLD keys (a changed
        # hotkey would only take effect after an app restart).
        self._safe_call(self.native, "native.config.reload", timeout=10.0)
        self._safe_call(self.native, "native.hotkeys.stop", timeout=10.0)
        result = self._safe_call(
            self.native,
            "native.hotkeys.start",
            {"addon_hotkeys": self._addon_hotkeys()},
            timeout=10.0,
        ) or {}
        if isinstance(result, dict) and not result.get("started"):
            self._notice("Global hotkeys did not start. Click the Wisp icon to summon it.")

    def chat_request(self, data: dict[str, Any]) -> None:
        request_id = str(data.get("request_id") or "")
        messages = data.get("messages") or []
        if not request_id:
            return

        done_seen = False

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            nonlocal done_seen
            if event == "reply.chunk":
                self._safe_call(
                    self.ui,
                    "ui.chat.chunk",
                    {"request_id": request_id, "text": str((payload or {}).get("text") or "")},
                    timeout=30.0,
                )
            elif event == "reply.done":
                done_seen = True
                self._safe_call(self.ui, "ui.chat.done", {"request_id": request_id}, timeout=30.0)

        try:
            result = self._brain_call_with_events(
                "brain.chat",
                {"messages": messages},
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
            )
            if not done_seen:
                text = str((result or {}).get("text") or "")
                if text:
                    self._safe_call(
                        self.ui,
                        "ui.chat.chunk",
                        {"request_id": request_id, "text": text},
                        timeout=30.0,
                    )
                self._safe_call(self.ui, "ui.chat.done", {"request_id": request_id}, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("chat request failed")
            self._safe_call(
                self.ui,
                "ui.chat.error",
                {"request_id": request_id, "error": f"{type(exc).__name__}: {exc}"},
                timeout=30.0,
            )

    def open_memory(self) -> None:
        result = self._safe_call(self.brain, "brain.memory.list", timeout=30.0) or {}
        facts = result.get("facts") if isinstance(result, dict) else []
        self._safe_call(self.ui, "ui.show_memory", {"facts": facts or []}, timeout=30.0)

    def memory_add(self, data: dict[str, Any]) -> None:
        self._safe_call(
            self.brain,
            "brain.memory.add",
            {"text": str(data.get("text") or ""), "category": data.get("category")},
            timeout=30.0,
        )

    def memory_update(self, data: dict[str, Any]) -> None:
        self._safe_call(
            self.brain,
            "brain.memory.update",
            {
                "fact_id": str(data.get("id") or data.get("fact_id") or ""),
                "text": str(data.get("text") or ""),
                "category": data.get("category"),
            },
            timeout=30.0,
        )

    def memory_delete(self, data: dict[str, Any]) -> None:
        self._safe_call(
            self.brain,
            "brain.memory.delete",
            {"fact_id": str(data.get("id") or data.get("fact_id") or "")},
            timeout=30.0,
        )

    def open_plugins(self) -> None:
        result = self._safe_call(self.brain, "brain.plugins.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            result = {}
        self._safe_call(
            self.ui,
            "ui.show_plugins",
            {
                "plugins": result.get("plugins") or [],
                "plugins_dir": str(result.get("plugins_dir") or ""),
            },
            timeout=30.0,
        )

    def plugin_run_action(self, data: dict[str, Any]) -> None:
        result = self._safe_call(
            self.brain,
            "brain.plugins.run_action",
            {
                "plugin_name": str(data.get("plugin_name") or ""),
                "label": str(data.get("label") or ""),
            },
            timeout=60.0,
        )
        message = "Plugin action finished."
        if isinstance(result, dict) and result.get("message"):
            message = str(result["message"])
        self._notice(message)

    def plugin_set_enabled(self, data: dict[str, Any]) -> None:
        name = str(data.get("plugin_name") or "")
        if not name:
            return
        self._safe_call(
            self.brain,
            "brain.plugins.set_enabled",
            {"plugin_name": name, "enabled": bool(data.get("enabled"))},
            timeout=30.0,
        )
        self.open_plugins()  # refresh the dialog so it reflects the new state

    def plugin_set_setting(self, data: dict[str, Any]) -> None:
        name = str(data.get("plugin_name") or "")
        key = str(data.get("key") or "")
        if not name or not key:
            return
        self._safe_call(
            self.brain,
            "brain.plugins.set_setting",
            {"plugin_name": name, "key": key, "value": data.get("value")},
            timeout=30.0,
        )

    def plugin_repair_environment(self, data: dict[str, Any]) -> None:
        name = str(data.get("plugin_name") or "")
        if not name:
            return
        result = self._safe_call(
            self.brain,
            "brain.plugins.repair_environment",
            {"plugin_name": name},
            timeout=600.0,
        )
        message = "Addon dependency environment repaired."
        if isinstance(result, dict) and not result.get("ready", True):
            message = str(result.get("error") or "Addon dependency environment is not ready.")
        self._notice(message)
        self.open_plugins()

    def plugin_install_archive(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if not path:
            return
        result = self._safe_call(
            self.brain,
            "brain.plugins.install_archive",
            {"path": path},
            timeout=120.0,
        )
        message = "Addon archive installed."
        if isinstance(result, dict) and result.get("id"):
            message = f"Installed addon: {result['id']}"
        self._notice(message)
        self.open_plugins()

    def plugin_install_folder(self, data: dict[str, Any]) -> None:
        path = str(data.get("path") or "")
        if not path:
            return
        result = self._safe_call(
            self.brain,
            "brain.plugins.install_folder",
            {"path": path},
            timeout=120.0,
        )
        message = "Addon folder installed."
        if isinstance(result, dict) and result.get("id"):
            message = f"Installed addon: {result['id']}"
        self._notice(message)
        self.open_plugins()

    def plugin_run_hotkey(self, data: dict[str, Any]) -> None:
        addon_id = str(data.get("addon_id") or "")
        hotkey_id = str(data.get("hotkey_id") or "")
        if not addon_id or not hotkey_id:
            return
        result = self._safe_call(
            self.brain,
            "brain.plugins.run_hotkey",
            {"plugin_name": addon_id, "hotkey_id": hotkey_id},
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
                    "brain.plugins.llm_call",
                    {
                        "plugin_name": addon_id,
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
        result = self._safe_call(self.brain, "brain.plugins.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            return []
        out: list[dict[str, Any]] = []
        for plugin in result.get("plugins") or []:
            if not isinstance(plugin, dict):
                continue
            addon_id = str(plugin.get("id") or plugin.get("name") or "")
            for item in plugin.get("hotkeys") or []:
                if not isinstance(item, dict):
                    continue
                combo = str(item.get("hotkey") or "")
                hotkey_id = str(item.get("id") or "")
                if addon_id and combo and hotkey_id:
                    out.append({"addon_id": addon_id, "id": hotkey_id, "hotkey": combo})
        return out

    def _show_addon_notifications(self) -> None:
        result = self._safe_call(self.brain, "brain.plugins.list", timeout=30.0) or {}
        if not isinstance(result, dict):
            return
        for plugin in result.get("plugins") or []:
            if not isinstance(plugin, dict):
                continue
            for item in plugin.get("notifications") or []:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("message") or "")
                if not message:
                    continue
                notify_result = self._safe_call(
                    self.native,
                    "native.notify",
                    {
                        "title": str(item.get("title") or plugin.get("name") or "Wisp"),
                        "message": message,
                    },
                    timeout=10.0,
                )
                if not (isinstance(notify_result, dict) and notify_result.get("ok")):
                    self._notice(message)

    def open_agent_task(self, spec: dict[str, Any] | None = None) -> None:
        params = {"spec": spec} if isinstance(spec, dict) and spec else {}
        self._safe_call(self.ui, "ui.show_agent_task", params, timeout=30.0)

    def open_agent_history(self) -> None:
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
        if not isinstance(spec, dict) or not spec:
            self._notice("Agent task spec was empty.")
            return

        timeout = max(600.0, float(spec.get("max_runtime_minutes") or 60) * 60.0 + 120.0)
        done_seen = False
        stream_id: Any = None

        def on_started(req_id: Any) -> None:
            nonlocal stream_id
            stream_id = req_id
            with self._lock:
                self._active_agent_stream_id = req_id

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            nonlocal done_seen
            params = payload if isinstance(payload, dict) else {"data": payload}
            if event == "agent.log":
                self._safe_call(self.ui, "ui.agent.log", params, timeout=30.0)
            elif event == "agent.trace":
                self._safe_call(self.ui, "ui.agent.trace", params, timeout=30.0)
            elif event == "agent.approval.request":
                self._safe_call(self.ui, "ui.agent.approval.request", params, timeout=30.0)
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

    def respond_agent_approval(self, data: dict[str, Any]) -> None:
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
        self._open_agent_spec_from_history("brain.agent.history.retry_spec", run_dir)

    def continue_agent_history(self, run_dir: str) -> None:
        self._open_agent_spec_from_history("brain.agent.history.continue_spec", run_dir)

    def _open_agent_spec_from_history(self, method: str, run_dir: str) -> None:
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
        query_started = time.monotonic()
        gen = self._new_generation()
        self._safe_call(self.audio, "audio.stop", timeout=5.0)
        self._safe_call(self.ui, "ui.overlay.state", {"state": "thinking"}, timeout=30.0)
        if not preserve_reply_bubble:
            self._safe_call(self.ui, "ui.reply.reset", timeout=30.0)
            self._safe_call(self.ui, "ui.reply.thinking", timeout=30.0)
        params = self._brain_query_params(prompt, pending)
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

        def on_event(event: str, payload: Any, _req_id: Any = None) -> None:
            nonlocal done_seen, first_chunk_seen
            if event == "reply.chunk":
                if not first_chunk_seen:
                    first_chunk_seen = True
                    log.info("query first reply chunk after %.2fs", time.monotonic() - query_started)
                self._on_reply_chunk(payload)
            elif event == "reply.done":
                done_seen = True
                text_done = str((payload or {}).get("text") or "")
                if text_done:
                    self._last_reply = text_done
                if not (self._tts_enabled() and text_done):
                    self._on_reply_done(payload)

        try:
            log.info("query brain call started")
            result = self._brain_call_with_events(
                "brain.query",
                params,
                timeout=_INTERACTIVE_LLM_TIMEOUT_SECONDS,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001 - surface route/config failures in the UI
            log.exception("brain query failed after %.2fs", time.monotonic() - query_started)
            self._notice(f"LLM request failed: {self._friendly_error(exc)}")
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()
            return
        log.info("query brain call finished after %.2fs", time.monotonic() - query_started)
        text = str((result or {}).get("text") or "")
        self._last_reply = text
        if text:
            self._safe_call(
                self.ui,
                "ui.chat.add_conversation",
                {
                    "user": prompt,
                    "assistant": text,
                    "context": chat_context,
                    "image_base64": pending.screenshot_b64,
                },
                timeout=30.0,
            )
        if self._is_current(gen) and text and self._tts_enabled():
            self._speak_text(text)
        elif not done_seen:
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()

    def _rewrite_and_paste(self, prompt: str, pending: PendingInvocation) -> None:
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
            if event == "reply.chunk":
                self._on_reply_chunk(payload)

        try:
            result = self._brain_call_with_events(
                "brain.rewrite",
                {"selected_text": selected, "intent_prompt": prompt},
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
        if text and self._is_current(gen):
            paste = self.native.call(
                "native.paste_text",
                {
                    "text": text,
                    "target_pid": pending.paste_target_pid,
                    "focus_token": int(pending.context.get("focus_token") or 0),
                },
                timeout=30.0,
            )
            paste = paste if isinstance(paste, dict) else {}
            log.info(
                "rewrite paste-back: target_pid=%s result=%s",
                pending.paste_target_pid, paste,
            )
            # Rewrite status must NOT land in the reply bubble (it would clobber the
            # streamed rewrite text). Success is silent — the pasted text in the
            # user's app is the confirmation. Only problems raise a system
            # notification, which needs user action / awareness.
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            if paste.get("ok"):
                pass  # silent success
            elif paste.get("clipboard_ok"):
                # Focus didn't land on the target app (common on remote/headless
                # macOS where activateWithOptions_ is ignored). The rewrite is on
                # the clipboard, so tell the user how to recover it.
                app = str(paste.get("app_name") or "").strip()
                where = f" into {app}" if app else ""
                log.warning(
                    "rewrite paste-back could not confirm focus%s (frontmost=%s); "
                    "left rewrite on clipboard", where, paste.get("frontmost_pid"),
                )
                self._native_notify(
                    "Wisp — rewrite on clipboard",
                    f"Couldn't focus the app. Press {self._paste_shortcut()} to paste the rewrite.",
                )
            else:
                log.error("rewrite paste-back failed: %s", paste.get("error") or paste)
                self._native_notify("Wisp — rewrite failed", "Couldn't paste the rewrite. See native.stderr.log.")
        self._set_idle()

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _paste_shortcut() -> str:
        return "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"

    def _native_notify(self, title: str, message: str) -> None:
        """Best-effort system notification (keeps status out of the reply bubble)."""
        try:
            self.native.call("native.notify", {"title": title, "message": message}, timeout=10.0)
        except Exception:
            log.exception("native.notify failed")

    def _schedule(self, fn, *args) -> None:
        if not self.run_async:
            fn(*args)
            return
        threading.Thread(target=self._guarded, args=(fn, args), daemon=True).start()

    def _guarded(self, fn, args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("flow %s failed", getattr(fn, "__name__", fn))
            self._set_idle()

    def _safe_call(self, worker: WorkerLike, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        try:
            return worker.call(method, params or {}, timeout=timeout)
        except Exception:
            log.exception("worker call failed: %s", method)
            return None

    def _fire(self, worker: WorkerLike, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a fire-and-forget request — the response is not awaited.

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
        with self._lock:
            self._current_generation = next(self._generation)
            return self._current_generation

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._current_generation

    def _claim_voice_start(self) -> bool:
        with self._lock:
            if self._voice_state != "idle":
                return False
            self._voice_state = "starting"
            self._voice_active = True
            return True

    def _ensure_voice_start_claimed(self) -> None:
        with self._lock:
            if self._voice_state == "idle":
                self._voice_state = "starting"
                self._voice_active = True

    def _mark_voice_recording(self) -> bool:
        with self._lock:
            if self._voice_state == "starting":
                self._voice_state = "recording"
                return True
            return self._voice_state == "recording"

    def _claim_voice_stop(self) -> bool:
        with self._lock:
            if self._voice_state == "idle":
                return False
            self._voice_state = "stopping"
            self._voice_active = False
            return True

    def _ensure_voice_stop_claimed(self) -> bool:
        with self._lock:
            if self._voice_state == "idle":
                return False
            self._voice_state = "stopping"
            self._voice_active = False
            return True

    def _mark_voice_idle(self) -> None:
        with self._lock:
            self._voice_state = "idle"
            self._voice_active = False

    def _claim_dictate_start(self) -> bool:
        with self._lock:
            # Mutually exclusive with voice push-to-talk (one shared recorder).
            if self._dictate_state != "idle" or self._voice_state != "idle":
                return False
            self._dictate_state = "recording"
            return True

    def _claim_dictate_stop(self) -> bool:
        with self._lock:
            if self._dictate_state != "recording":
                return False
            self._dictate_state = "stopping"
            return True

    def _mark_dictate_idle(self) -> None:
        with self._lock:
            self._dictate_state = "idle"

    def _set_idle(self) -> None:
        # Fire-and-forget. This runs inline on the worker event-reader thread
        # (from _on_intent_cancelled / _on_snip_cancelled). A BLOCKING ui.call
        # here waits for a response that only that same reader thread can read ->
        # 30s self-deadlock that also stalls every other UI call queued behind it
        # (e.g. the next snip). The idle animation is cosmetic, so never wait --
        # mirrors the non-blocking "listening" state fired in begin_caller/snip.
        self._fire(self.ui, "ui.overlay.state", {"state": "idle"})

    def _notice(self, text: str) -> None:
        self._safe_call(self.ui, "ui.reply.notice", {"text": text}, timeout=30.0)

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
        text = str(exc).strip() or type(exc).__name__
        for prefix in ("ValueError: ", "RuntimeError: "):
            if text.startswith(prefix):
                return text[len(prefix):].strip()
        return text

    def _caller(self, caller_idx: int) -> dict[str, Any]:
        import config

        rows = getattr(config, "CALLER_ROWS", [])
        if 0 <= caller_idx < len(rows):
            return dict(rows[caller_idx])
        return {}

    def _voice_caller(self) -> dict[str, Any]:
        """Context/tool config for push-to-talk; falls back to caller 1's row."""
        import config

        voice = getattr(config, "VOICE_CALLER", None)
        if isinstance(voice, dict) and voice:
            return dict(voice)
        return self._caller(0)

    @staticmethod
    def _current_config_mtime() -> float | None:
        try:
            import config

            env_file = Path(getattr(config, "_ENV_FILE", ""))
            return env_file.stat().st_mtime
        except (OSError, TypeError, ValueError):
            return None

    def _reload_supervisor_config_if_changed(self) -> None:
        current_mtime = self._current_config_mtime()
        if current_mtime is None or current_mtime == self._config_mtime:
            return
        import config

        config.reload()
        self._config_mtime = current_mtime
        log.info("supervisor config reloaded after .env change")

    def _config_value(self, name: str, default: Any) -> Any:
        import config

        return getattr(config, name, default)

    def _log_caller_runtime(self, caller_idx: int, caller: dict[str, Any]) -> None:
        try:
            import config

            log.info(
                "caller %d config label=%r hotkey=%r ambient=%s docs=%s browser=%s memory=%s "
                "screenshot=%r clipboard=%s paste_back=%s cwd=%r config_file=%r env_file=%r",
                caller_idx,
                caller.get("label"),
                caller.get("hotkey"),
                caller.get("context_ambient"),
                self._context_mode(caller, "documents"),
                self._context_mode(caller, "browser"),
                self._context_mode(caller, "memory"),
                caller.get("context_screenshot"),
                caller.get("context_clipboard"),
                caller.get("paste_back"),
                str(Path.cwd()),
                str(getattr(config, "__file__", "") or ""),
                str(getattr(config, "_ENV_FILE", "") or ""),
            )
        except Exception:
            log.exception("caller runtime logging failed")

    def _context_snapshot(self, caller: dict[str, Any], *, include_browser: bool = True) -> dict[str, Any]:
        # The browser-page fetch is a ~2-3s network read (requests.get). Keep it
        # OFF the hotkey -> picker path (include_browser=False) and fetch it lazily
        # at query time instead, where it overlaps the LLM round-trip. The URL and
        # window handle ARE captured now, while the browser is still foreground —
        # by query time the picker has stolen focus and re-detection would fail.
        browser_auto = self._context_mode(caller, "browser") == "auto"
        snapshot = self.native.call(
            "native.context.snapshot",
            {
                "include_clipboard": bool(caller.get("context_clipboard", False)),
                "include_selection": True,
                "include_browser_content": include_browser and browser_auto,
                "include_browser_url": browser_auto,
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
            "context snapshot active=%r hwnd=%s browser_url=%s browser_hwnd=%s browser_chars=%d",
            active_app.get("name"),
            active_app.get("window_id") or active_app.get("pid") or 0,
            "y" if snapshot.get("browser_url") else "n",
            snapshot.get("browser_hwnd") or 0,
            len(str(snapshot.get("browser_content") or "")),
        )
        log.info(
            "context runtime cwd=%r repo=%r exe=%r config_file=%r env_file=%r",
            runtime_debug.get("cwd"),
            runtime_debug.get("repo_root"),
            runtime_debug.get("executable"),
            runtime_debug.get("config_file"),
            runtime_debug.get("env_file"),
        )
        log.info(
            "context foreground raw_hwnd=%s raw_pid=%s raw_process=%r raw_title=%r "
            "corrected=%s chosen_hwnd=%s chosen_pid=%s chosen_process=%r chosen_title=%r",
            window_debug.get("raw_hwnd"),
            window_debug.get("raw_pid"),
            window_debug.get("raw_process"),
            window_debug.get("raw_title"),
            window_debug.get("corrected"),
            window_debug.get("chosen_hwnd"),
            window_debug.get("chosen_pid"),
            window_debug.get("chosen_process"),
            window_debug.get("chosen_title"),
        )
        if browser_window:
            log.info(
                "context browser window hwnd=%s pid=%s process=%r title=%r url=%r",
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
        """Fetch just the active browser tab's URL + page content — the deferred,
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

    def _brain_query_params(self, prompt: str, pending: PendingInvocation) -> dict[str, Any]:
        caller = pending.caller
        context = pending.context or {}
        ambient_parts: list[str] = []
        buffered_items, drop_items = self._consume_context_extras()
        screenshot_b64 = pending.screenshot_b64
        screenshot_tool_b64: str | None = pending.screenshot_tool_b64
        allow_screenshot_tool = self._screenshot_tool_allowed(caller)
        if allow_screenshot_tool and screenshot_tool_b64 is None:
            screenshot_tool_b64 = self._capture_model_tool_b64()
        allowed_tools = self._allowed_model_tools(caller)
        pinned_tools = self._pinned_model_tools(caller)
        frontload_tools = self._frontloaded_model_tools(caller)
        memory_mode = self._context_mode(caller, "memory")
        include_active_document = self._context_mode(caller, "documents") == "auto"
        active_document_text = ""
        if include_active_document:
            active_app = context.get("active_app") if isinstance(context.get("active_app"), dict) else {}
            debug = context.get("debug") if isinstance(context.get("debug"), dict) else {}
            window_debug = debug.get("window") if isinstance(debug.get("window"), dict) else {}
            active_window = {
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
            result = self._safe_call(
                self.brain,
                "brain.context.active_document",
                {"active_window": active_window},
                timeout=15.0,
            ) or {}
            if isinstance(result, dict):
                active_document_text = str(result.get("text") or "")
            doc_debug = result.get("debug") if isinstance(result, dict) else None
            log.info(
                "active document context chars=%d debug=%r error=%r",
                len(active_document_text),
                doc_debug,
                result.get("error") if isinstance(result, dict) else None,
            )
        if caller.get("context_ambient", True):
            active_app = context.get("active_app")
            if isinstance(active_app, dict) and active_app.get("name"):
                ambient_parts.append(f"Active app: {active_app.get('name')}")
        if caller.get("context_clipboard") and context.get("clipboard_text"):
            ambient_parts.append(f"Clipboard:\n{context.get('clipboard_text')}")
        if self._context_mode(caller, "browser") == "auto":
            browser_bits: list[str] = []
            browser_url = str(context.get("browser_url") or "").strip()
            browser_hwnd = int(context.get("browser_hwnd") or 0)
            browser_app = str(context.get("browser_app") or "").strip()
            browser_content = str(context.get("browser_content") or "").strip()
            if (browser_url or browser_hwnd or browser_app) and not browser_content:
                # URL + window handle (Windows) or browser app name (macOS) were
                # captured at hotkey time while the browser was foreground; read
                # the page now (deferred off the picker path). Windows reads by
                # handle; macOS asks the named app via AppleScript — both work
                # with the picker/overlay holding focus.
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
                browser_content = str(result.get("content") or "").strip()
                log.info(
                    "browser context by captured hwnd url=%r hwnd=%s chars=%d error=%r",
                    browser_url,
                    browser_hwnd,
                    len(browser_content),
                    result.get("error") if isinstance(result, dict) else None,
                )
            elif not browser_url and not browser_content:
                # No URL captured at hotkey time (e.g. older snapshot shape).
                # Last resort: re-detect the foreground browser now. Prone to the
                # focus race, but better than nothing.
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
            if browser_url:
                browser_bits.append(f"URL: {browser_url}")
            if browser_content:
                browser_bits.append(browser_content)
            elif browser_app:
                # macOS only (browser_app is set only there). The page text came
                # back empty — almost always a permission the user hasn't granted
                # yet. Tell the model so it can relay the fix instead of just
                # claiming it cannot read the page.
                if browser_url:
                    browser_bits.append(
                        f"(Could not read the {browser_app} page text. In {browser_app}, enable "
                        f"View → Developer → Allow JavaScript from Apple Events.)"
                    )
                else:
                    browser_bits.append(
                        f"(Could not read {browser_app}. Allow this app to control {browser_app} in "
                        f"System Settings → Privacy & Security → Automation, then try again.)"
                    )
            if browser_bits:
                ambient_parts.append("[Browser/Web]\n" + "\n\n".join(browser_bits))
        if buffered_items:
            ambient_parts.append("Buffered context:\n" + "\n\n".join(buffered_items))
        if drop_items:
            drop_text_parts = []
            for item in drop_items:
                item_type = str(item.get("type") or "text")
                content = item.get("content")
                if item_type == "image" and not screenshot_b64:
                    screenshot_b64 = self._image_content_b64(content)
                    continue
                drop_text_parts.append(
                    f"{item.get('name') or 'Context'} ({item_type}):\n{self._content_to_text(content)}"
                )
            if drop_text_parts:
                ambient_parts.append("Dropped context:\n" + "\n\n".join(drop_text_parts))
        ambient_text = "\n\n".join(ambient_parts)
        context_priority = self._context_priority_source(
            context,
            ambient_text,
            active_document_text,
        )
        summary = self._context_summary_badges(
            selected=str(context.get("selected_text") or ""),
            screenshot_b64=screenshot_b64,
            buffered_items=buffered_items,
            drop_items=drop_items,
            clipboard_text=str(context.get("clipboard_text") or "") if caller.get("context_clipboard") else "",
            ambient_text=ambient_text,
            active_document_text=active_document_text,
        )
        return {
            "intent_prompt": prompt,
            "selected": context.get("selected_text") or "",
            "screenshot_b64": screenshot_b64,
            "ambient_text": ambient_text,
            "memory_enabled": memory_mode == "auto",
            "use_tools": bool(allowed_tools),
            "allowed_tools": allowed_tools,
            "pinned_tools": pinned_tools,
            "frontload_tools": frontload_tools,
            "allow_screenshot_tool": allow_screenshot_tool,
            "screenshot_tool_b64": screenshot_tool_b64,
            "include_active_document": include_active_document and not active_document_text,
            "active_document_text": active_document_text,
            "context_priority": context_priority,
            "_ui_context_summary": summary,
        }

    @staticmethod
    def _is_browser_active_context(context: dict[str, Any]) -> bool:
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
        if not active_document_text or "[Browser/Web]" not in (ambient_text or ""):
            return ""
        return "Browser/Web" if cls._is_browser_active_context(context) else "Active document"

    @staticmethod
    def _context_mode(caller: dict[str, Any], name: str) -> str:
        key = f"context_{name}_mode"
        mode = str(caller.get(key) or "").strip().lower()
        if mode in {"off", "auto", "model"}:
            return mode
        if name == "documents":
            if caller.get("context_documents", False):
                return "auto"
            if caller.get("context_tools", False):
                return "model"
        if name in {"browser", "github"} and caller.get("context_tools", False):
            return "model"
        if name == "memory":
            return "auto"
        return "off"

    def _allowed_model_tools(self, caller: dict[str, Any]) -> list[str]:
        allowed: list[str] = []
        if self._context_mode(caller, "documents") == "model":
            allowed.append("get_context.documents")
        if self._context_mode(caller, "browser") == "model":
            allowed.extend(["web_search", "get_context.browser"])
        if self._context_mode(caller, "github") == "model":
            allowed.extend(["git_status", "git_diff", "github_repo", "github_issue"])
        if self._context_mode(caller, "memory") == "model":
            allowed.append("memory_search")
        overrides = self._tool_overrides(caller)
        for name, mode in overrides.items():
            if mode != "off" and name not in allowed:
                allowed.append(name)
        # Per-tool "off" beats whatever a context dropdown granted. A plain
        # get_context override covers both of its dotted source entries.
        removed = {name for name, mode in overrides.items() if mode == "off"}
        if removed:
            allowed = [
                name
                for name in allowed
                if name not in removed
                and not (name.startswith("get_context.") and "get_context" in removed)
            ]
        return allowed

    def _pinned_model_tools(self, caller: dict[str, Any]) -> list[str]:
        """Tools always offered to the model, bypassing prompt keyword filters.

        Context dropdowns in "model" mode mean "offer the tool schema and let
        the model decide whether to call it." The allow-list uses dotted source
        grants like ``get_context.browser``, but the actual schema is named
        ``get_context``, so pin the schema name here.
        """
        pinned: list[str] = []
        if self._context_mode(caller, "documents") == "model":
            pinned.append("get_context")
        if self._context_mode(caller, "browser") == "model":
            pinned.extend(["web_search", "get_context"])
        if self._context_mode(caller, "github") == "model":
            pinned.extend(["git_status", "git_diff", "github_repo", "github_issue"])
        if self._context_mode(caller, "memory") == "model":
            pinned.append("memory_search")
        overrides = self._tool_overrides(caller)
        pinned.extend(name for name, mode in overrides.items() if mode == "on")
        removed = {name for name, mode in overrides.items() if mode == "off"}
        allowed = set(self._allowed_model_tools(caller))
        result: list[str] = []
        for name in pinned:
            if name == "get_context":
                if not ({"get_context", "get_context.browser", "get_context.documents"} & allowed):
                    continue
            elif name not in allowed:
                continue
            if name in removed:
                continue
            if name == "get_context" and (
                "get_context" in removed
                or (
                    "get_context.browser" in removed
                    and "get_context.documents" in removed
                )
            ):
                continue
            if name not in result:
                result.append(name)
        return result

    def _screenshot_tool_allowed(self, caller: dict[str, Any]) -> bool:
        """Whether capture_screen is exposed: the Screenshot dropdown's "model"
        mode, overridable per-tool from the Allowed Tools list (auto-capture
        stays dropdown-governed)."""
        override = self._tool_overrides(caller).get("capture_screen")
        if override == "off":
            return False
        if override in {"on", "model"}:
            return True
        return caller.get("context_screenshot") == "model"

    @staticmethod
    def _tool_overrides(caller: dict[str, Any]) -> dict[str, str]:
        overrides = caller.get("tools")
        if not isinstance(overrides, dict):
            return {}
        return {
            str(name): str(mode).strip().lower()
            for name, mode in overrides.items()
            if str(mode).strip().lower() in {"on", "model", "off"}
        }

    def _frontloaded_model_tools(self, caller: dict[str, Any]) -> list[str]:
        frontload: list[str] = []
        if self._context_mode(caller, "github") == "auto":
            frontload.extend(["git_status", "git_diff"])
        overrides = self._tool_overrides(caller)
        return [name for name in frontload if overrides.get(name) != "off"]

    def _consume_context_extras(self) -> tuple[list[str], list[dict[str, Any]]]:
        buffered = list(self._context_buffer)
        dropped = list(self._drop_context_items)
        self._context_buffer.clear()
        self._drop_context_items.clear()
        if dropped:
            self._safe_call(self.ui, "ui.context.clear", timeout=30.0)
        return buffered, dropped

    @staticmethod
    def _normalize_context_item(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return {
                "name": str(item.get("name") or item.get("label") or "Context"),
                "content": item.get("content", ""),
                "type": str(item.get("type") or item.get("item_type") or "text"),
            }
        return {"name": "Context", "content": str(item), "type": "text"}

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        if isinstance(content, (dict, list, tuple)):
            return json_safe_dumps(content)
        return str(content)

    def _image_content_b64(self, content: Any) -> str | None:
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
        flat = " ".join((text or "").split())
        return (flat[: n - 1] + "...") if len(flat) > n else flat

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
        items: list[dict[str, str]] = []
        if screenshot_b64:
            items.append({"label": "Screenshot", "type": "image"})
        if selected:
            items.append({"label": f"Selection - {self._short(selected, 14)}", "type": "text"})
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
            items.append({"label": f"Clipboard - {self._short(clipboard_text, 14)}", "type": "text"})
        if active_document_text:
            items.append({"label": "Active document", "type": "file"})
        if "[Browser/Web]" in (ambient_text or ""):
            items.append({"label": "Browser/Web", "type": "file"})
        ambient_without_browser = (ambient_text or "").replace("[Browser/Web]", "").strip()
        if ambient_without_browser:
            items.append({"label": "Window context", "type": "file"})
        return items[:8]

    def _capture_fullscreen_b64(self) -> str | None:
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
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        return base64.b64encode(p.read_bytes()).decode("ascii")

    def _tts_enabled(self) -> bool:
        import config

        return str(getattr(config, "TTS_PROVIDER", "none")).strip().lower() != "none"

    def _speak_text(self, text: str) -> None:
        try:
            result = self.audio.call("audio.tts.synthesize", {"text": text}, timeout=180.0)
            path = result.get("path") if isinstance(result, dict) else ""
            if path:
                self._safe_call(self.ui, "ui.overlay.state", {"state": "speaking"}, timeout=30.0)
                # Buffer Cartesia word timestamps in the bubble *before* playback
                # starts. start_word_reveal — fired by the audio.playback.started
                # event below — drains them anchored to the real audio clock, so
                # the word highlight tracks the spoken voice instead of a fixed
                # 170-WPM guess. Do NOT call ui.reply.start_reveal here: it would
                # anchor the reveal to synth-completion (before audio is audible)
                # and the playback-started reveal would then cancel it.
                wts = result.get("word_timestamps") if isinstance(result, dict) else None
                if isinstance(wts, dict) and wts.get("words"):
                    self._safe_call(
                        self.ui,
                        "ui.reply.schedule_words",
                        {"words": wts.get("words"), "start_ms": wts.get("start_ms")},
                        timeout=30.0,
                    )
                self.audio.call("audio.play_file", {"path": path}, wait=False)
            else:
                self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
                self._set_idle()
        except Exception:
            log.exception("audio playback failed")
            self._safe_call(self.ui, "ui.reply.done", timeout=30.0)
            self._set_idle()


def json_safe_dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=True)
    except TypeError:
        return str(value)
