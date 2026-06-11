from __future__ import annotations

import importlib.util
import os
import time

import pytest

from macos_py.supervisor.ipc import WorkerClient, WorkerError, WorkerSpec


def _worker(module: str, role: str, name: str | None = None, env: dict[str, str] | None = None) -> WorkerClient:
    merged_env = {"WISP_BRAIN_FAKE_LLM": "1", **(env or {})}
    return WorkerClient(WorkerSpec(name or role, module, role, env=merged_env))


def test_native_worker_ping_and_boundary_status():
    worker = _worker("macos_py.workers.native_host", "native")
    try:
        result = worker.call("ping", {"value": "hello"}, timeout=10)
        assert result["pong"] is True
        assert result["value"] == "hello"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_audio_worker_ping_does_not_import_audio_stack():
    worker = _worker("macos_py.workers.audio_host", "audio")
    try:
        result = worker.call("audio.ping", timeout=10)
        assert result["role"] == "audio"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_brain_worker_exposes_boundary_status_without_ui_or_native_imports():
    worker = _worker("macos_py.workers.brain_host", "brain")
    try:
        result = worker.call("brain.ping", timeout=20)
        assert result["role"] == "brain"
        assert result["boundary"]["ok"] is True
        assert result["boundary"]["forbidden_loaded"] == []
    finally:
        worker.shutdown()


def test_unknown_method_reports_error_without_killing_worker():
    worker = _worker("macos_py.workers.native_host", "native")
    try:
        with pytest.raises(WorkerError):
            worker.call("native.nope", timeout=10)
        result = worker.call("ping", timeout=10)
        assert result["pong"] is True
    finally:
        worker.shutdown()


def test_worker_stderr_log_file_is_created_in_run_log_dir(tmp_path):
    worker = _worker(
        "macos_py.workers.native_host",
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
    worker = _worker("macos_py.workers.native_host", "native")
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
    seen = []
    worker = _worker("macos_py.workers.native_host", "native")
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
    seen = []
    worker = _worker(
        "macos_py.workers.ui_host",
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
    worker = _worker(
        "macos_py.workers.ui_host",
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
    worker = _worker(
        "macos_py.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "0.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.1",
        },
    )
    try:
        started = time.perf_counter()
        result = worker.call("ui.show_settings", timeout=10)
        elapsed = time.perf_counter() - started
        assert result == {"queued": True}
        assert elapsed < 1.0

        time.sleep(0.5)
        ping = worker.call("ui.ping", timeout=10)
        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_show_memory_does_not_crash_or_block_event_loop(tmp_path):
    worker = _worker(
        "macos_py.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "0.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.1",
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
        assert elapsed < 1.0

        time.sleep(0.5)
        ping = worker.call("ui.ping", timeout=10)
        assert ping["pong"] is True
        assert not list(tmp_path.glob("ui_freeze_*.log"))
    finally:
        worker.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed")
def test_ui_worker_bubble_clear_does_not_import_audio_or_freeze(tmp_path):
    worker = _worker(
        "macos_py.workers.ui_host",
        "ui",
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "WISP_RUN_LOG_DIR": str(tmp_path),
            "WISP_UI_FREEZE_THRESHOLD_SECONDS": "0.5",
            "WISP_UI_FREEZE_WATCHDOG_INTERVAL_SECONDS": "0.1",
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
