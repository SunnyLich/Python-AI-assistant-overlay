"""Tests for macos py test supervisor ipc."""

from __future__ import annotations

import importlib.util
import os
import sys
import time

import pytest

from runtime.supervisor.ipc import WorkerClient, WorkerError, WorkerSpec, WispSupervisor, default_specs


pytestmark = pytest.mark.workflow


def _worker(module: str, role: str, name: str | None = None, env: dict[str, str] | None = None) -> WorkerClient:
    """Verify worker behavior."""
    merged_env = {"WISP_BRAIN_FAKE_LLM": "1", **(env or {})}
    return WorkerClient(WorkerSpec(name or role, module, role, env=merged_env))


def _is_macos_offscreen_qt() -> bool:
    """Return whether this test is using macOS' headless Qt backend."""
    return sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "offscreen") == "offscreen"


def _app_supervisor(tmp_path) -> WispSupervisor:
    """Create the same worker process set the app supervisor starts."""
    specs = default_specs()
    for name, spec in specs.items():
        spec.env = {
            **spec.env,
            "WISP_BRAIN_FAKE_LLM": "1",
            "WISP_RUN_LOG_DIR": str(tmp_path),
        }
        if name == "ui":
            spec.env["QT_QPA_PLATFORM"] = "offscreen"
            spec.env["WISP_UI_FREEZE_THRESHOLD_SECONDS"] = "2.5"
            spec.env["WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS"] = "0.25"
    return WispSupervisor(specs)


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_wisp_supervisor_starts_real_app_worker_process_set(tmp_path):
    """The app architecture starts UI/native/brain/audio as separate workers."""
    supervisor = _app_supervisor(tmp_path)
    try:
        results = supervisor.start_all()

        assert set(results) == {"native", "ui", "brain", "audio"}
        pids = {result["pid"] for result in results.values()}
        assert len(pids) == len(results)
        for name, result in results.items():
            assert result["pong"] is True
            assert result["role"] == name
            assert result["boundary"]["ok"] is True
            assert result["boundary"]["forbidden_loaded"] == []

        assert supervisor.call("ui", "ui.show_chat", {"new": True}, timeout=30) == {
            "shown": True,
            "reused": False,
        }
        if not _is_macos_offscreen_qt():
            # The real macOS app uses the Cocoa backend. The headless offscreen
            # backend can exit when multiple top-level UI surfaces are opened in
            # one worker, which is a test harness limitation rather than the app
            # architecture contract this smoke test is meant to cover.
            assert supervisor.call("ui", "ui.show_settings", timeout=10) == {"queued": True}
        assert supervisor.call("ui", "ui.ping", timeout=10)["pong"] is True

        events = []
        reply = supervisor.workers["brain"].call_with_events(
            "brain.query",
            {
                "intent_prompt": "architecture smoke prompt",
                "ambient_text": "architecture smoke context",
                "memory_enabled": False,
            },
            timeout=30,
            on_event=lambda event, data, req_id: events.append((event, data, req_id)),
        )
        assert "[fake-llm]" in reply["text"]
        assert "architecture smoke prompt" in reply["text"]
        assert any(event == "reply.chunk" for event, _data, _req_id in events)

        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        supervisor.shutdown()


def test_native_worker_ping_and_boundary_status():
    """Verify native worker ping and boundary status behavior."""
    worker = _worker("runtime.workers.native_host", "native")
    try:
        result = worker.call("ping", {"value": "hello"}, timeout=10)
        assert result["pong"] is True
        assert result["value"] == "hello"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_audio_worker_ping_does_not_import_audio_stack():
    """Verify audio worker ping does not import audio stack behavior."""
    worker = _worker("runtime.workers.audio_host", "audio")
    try:
        result = worker.call("audio.ping", timeout=10)
        assert result["role"] == "audio"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_brain_worker_exposes_boundary_status_without_ui_or_native_imports():
    """Verify brain worker exposes boundary status without ui or native imports behavior."""
    worker = _worker("runtime.workers.brain_host", "brain")
    try:
        result = worker.call("brain.ping", timeout=20)
        assert result["role"] == "brain"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_unknown_method_reports_error_without_killing_worker():
    """Verify unknown method reports error without killing worker behavior."""
    worker = _worker("runtime.workers.native_host", "native")
    try:
        with pytest.raises(WorkerError):
            worker.call("native.nope", timeout=10)
        result = worker.call("ping", timeout=10)
        assert result["pong"] is True
    finally:
        worker.shutdown()


def test_worker_stderr_log_file_is_created_in_run_log_dir(tmp_path):
    """Verify worker stderr log file is created in run log dir behavior."""
    worker = _worker(
        "runtime.workers.native_host",
        "native",
        env={"WISP_RUN_LOG_DIR": str(tmp_path)},
    )
    try:
        result = worker.call("ping", timeout=10)
        assert result["pong"] is True

        deadline = time.time() + 5
        log_path = tmp_path / "native.stderr.log"
        while time.time() < deadline and not log_path.exists():
            time.sleep(0.05)
        assert log_path.exists()
    finally:
        worker.shutdown()


def test_worker_respawns_after_process_death():
    """Verify worker respawns after process death behavior."""
    worker = _worker("runtime.workers.native_host", "native")
    try:
        first = worker.call("ping", timeout=10)
        assert worker._proc is not None
        worker._proc.kill()
        worker._proc.wait(timeout=5)
        second = worker.call("ping", timeout=10)
        assert second["pong"] is True
        assert second["pid"] != first["pid"]
    finally:
        worker.shutdown()


def test_worker_exit_handler_fires_when_process_exits():
    """Verify worker exit handler fires when process exits behavior."""
    seen = []
    worker = _worker("runtime.workers.native_host", "native")
    worker.on_exit(lambda returncode: seen.append(returncode))
    try:
        worker.call("ping", timeout=10)
        assert worker._proc is not None
        worker._proc.kill()
        worker._proc.wait(timeout=5)

        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        assert seen
        assert seen[-1] is not None
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_emits_ready_event_and_passes_boundary():
    """Verify ui worker emits ready event and passes boundary behavior."""
    seen = []
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    worker.on_event("ui.ready", lambda data, req_id: seen.append(data))
    try:
        result = worker.call("ui.ping", timeout=30)
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        assert seen
        assert result["role"] == "ui"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_freeze_watchdog_writes_log(tmp_path):
    """Verify ui worker freeze watchdog writes log behavior."""
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_DEBUG_METHODS": "1",
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "0.1",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.05",
            "WISP_UI_FREEZE_LOG_COOLDOWN_SECONDS": "0.1",
            "WISP_UI_SLOW_DISPATCH_SECONDS": "0.1",
        },
    )
    try:
        result = worker.call("ui.debug.block_event_loop", {"seconds": 0.35}, timeout=10)
        assert result["blocked_seconds"] == 0.35

        deadline = time.time() + 5
        freeze_logs = []
        slow_logs = []
        while time.time() < deadline:
            freeze_logs = list(tmp_path.glob("ui_freeze_*.log"))
            slow_logs = list(tmp_path.glob("ui_slow_dispatch_*.log"))
            if freeze_logs and slow_logs:
                break
            time.sleep(0.05)

        assert freeze_logs
        assert slow_logs
        freeze_text = freeze_logs[0].read_text(encoding="utf-8")
        slow_text = slow_logs[0].read_text(encoding="utf-8")
        assert "active_method=ui.debug.block_event_loop" in freeze_text
        assert "Thread stacks:" in freeze_text
        assert "method=ui.debug.block_event_loop" in slow_text
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_show_settings_does_not_block_event_loop(tmp_path):
    """Verify ui worker show settings does not block event loop behavior."""
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "2.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.25",
        },
    )
    try:
        started = time.perf_counter()
        result = worker.call("ui.show_settings", timeout=10)
        elapsed = time.perf_counter() - started
        assert result == {"queued": True}
        assert elapsed < 2.0

        time.sleep(0.5)
        ping = worker.call("ui.ping", timeout=10)
        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_show_memory_does_not_crash_or_block_event_loop(tmp_path):
    """Verify ui worker show memory does not crash or block event loop behavior."""
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "2.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.25",
        },
    )
    try:
        started = time.perf_counter()
        result = worker.call(
            "ui.show_memory",
            {"facts": [{"id": "1", "text": "remember this", "category": "general"}]},
            timeout=10,
        )
        elapsed = time.perf_counter() - started
        assert result == {"queued": True}
        assert elapsed < 2.0

        time.sleep(0.5)
        ping = worker.call("ui.ping", timeout=10)
        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_bubble_clear_does_not_import_audio_or_freeze(tmp_path):
    """Verify ui worker bubble clear does not import audio or freeze behavior."""
    if _is_macos_offscreen_qt():
        pytest.skip("macOS offscreen Qt cannot reliably construct overlay/tray surfaces")
    worker = _worker(
        "runtime.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "2.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.25",
        },
    )
    try:
        worker.call("ui.show_overlay", timeout=30)
        worker.call("ui.reply.notice", {"text": "hello", "timeout_ms": 10}, timeout=10)
        worker.call("ui.reply.reset", timeout=10)
        ping = worker.call("ui.ping", timeout=10)

        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
        assert "core.audio" not in worker.stderr_tail(80)
        assert "numpy" not in worker.stderr_tail(80).lower()
    finally:
        worker.shutdown()
