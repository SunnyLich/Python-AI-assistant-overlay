"""Parent-side worker supervisor and JSON transport."""

from __future__ import annotations

import atexit
import itertools
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime import protocol
from runtime.bootstrap import repo_root

log = logging.getLogger("wisp.runtime.supervisor")


class WorkerError(RuntimeError):
    """A worker call failed, timed out, or the worker is unavailable."""


@dataclass
class WorkerSpec:
    """Store worker spec configuration data."""
    name: str
    module: str
    role: str
    cwd: Path = field(default_factory=repo_root)
    env: dict[str, str] = field(default_factory=dict)
    restart_limit: int = 3


class WorkerClient:
    """Spawn, monitor, and talk to one worker process."""

    def __init__(self, spec: WorkerSpec) -> None:
        """Initialize the worker client instance."""
        self.spec = spec
        self._proc: subprocess.Popen | None = None
        self._ids = itertools.count(1)
        self._spawn_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._event_handlers: dict[str, list[Callable[[Any, Any], None]]] = {}
        self._exit_handlers: list[Callable[[int | None], None]] = []
        self._scoped_event_lock = threading.Lock()
        self._scoped_event_handlers: dict[int, Callable[[str, Any, Any], None]] = {}
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._stderr_log_path: Path | None = None
        self._restart_count = 0
        self._shutting_down = False
        atexit.register(self.shutdown)

    def alive(self) -> bool:
        """Handle alive for worker client."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        """Handle pid for worker client."""
        return self._proc.pid if self._proc is not None else None

    def start(self) -> None:
        """Spawn the worker subprocess if it is not already running."""
        self._ensure_started()

    def _ensure_started(self) -> None:
        """Ensure started."""
        if self.alive():
            return
        with self._spawn_lock:
            if self.alive():
                return
            if self._shutting_down:
                raise WorkerError(f"{self.spec.name} is shutting down")
            self._spawn()

    def _spawn(self) -> None:
        """Handle spawn for worker client."""
        env = os.environ.copy()
        env.update(self.spec.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("WISP_REPO_ROOT", str(repo_root()))
        self._stderr_log_path = self._worker_log_path(env)
        log.info("starting %s: %s", self.spec.name, self.spec.module)
        if self._stderr_log_path is not None:
            log.info("%s stderr log: %s", self.spec.name, self._stderr_log_path)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", self.spec.module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.spec.cwd),
            env=env,
            bufsize=0,
        )
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._stderr_loop, args=(self._proc,), daemon=True).start()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """Read loop."""
        stdout = proc.stdout
        assert stdout is not None
        while True:
            msg = protocol.read_message(stdout)
            if msg is None:
                break
            if msg.get("event") is not None:
                self._dispatch_event(msg["event"], msg.get("data"), msg.get("id"))
                continue
            rid = msg.get("id")
            if rid is None:
                continue
            with self._pending_lock:
                slot = self._pending.pop(rid, None)
            if slot is not None:
                slot["resp"] = msg
                slot["event"].set()
        with self._spawn_lock:
            if self._proc is proc:
                self._fail_pending("worker exited")
        returncode = proc.poll()
        if returncode is None:
            try:
                returncode = proc.wait(timeout=0.2)
            except Exception:  # noqa: BLE001
                returncode = proc.poll()
        self._notify_exit(returncode)

    def _stderr_loop(self, proc: subprocess.Popen) -> None:
        """Handle stderr loop for worker client."""
        stderr = proc.stderr
        assert stderr is not None
        log_file = None
        if self._stderr_log_path is not None:
            try:
                self._stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
                log_file = self._stderr_log_path.open("a", encoding="utf-8")
            except Exception:  # noqa: BLE001
                log.exception("could not open %s stderr log", self.spec.name)
        for raw in iter(stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self._stderr_tail.append(line)
                if log_file is not None:
                    try:
                        log_file.write(line + "\n")
                        log_file.flush()
                    except Exception:
                        pass
                if (
                    line.startswith("[plugin]")
                    or line.startswith("[plugin:")
                    or line.startswith("[kokoro install]")
                    or line.startswith("[tts] Kokoro")
                    or line.startswith("[tts] Building Kokoro")
                    or line.startswith("[tts] Installed Kokoro")
                    or line.startswith("[audio] Kokoro warmup")
                    or ("warmup exceeded" in line and line.startswith("[audio]"))
                ):
                    log.info("[%s] %s", self.spec.name, line)
                else:
                    log.debug("[%s] %s", self.spec.name, line)
        if log_file is not None:
            log_file.close()

    def _worker_log_path(self, env: dict[str, str]) -> Path | None:
        """Handle worker log path for worker client."""
        root = env.get("WISP_RUN_LOG_DIR")
        if not root:
            return None
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in self.spec.name)
        return Path(root) / f"{safe_name}.stderr.log"

    def stderr_tail(self, max_lines: int = 20) -> str:
        """Handle stderr tail for worker client."""
        lines = list(self._stderr_tail)[-max_lines:]
        return "\n".join(lines)

    def _fail_pending(self, error: str) -> None:
        """Handle fail pending for worker client."""
        with self._pending_lock:
            for slot in self._pending.values():
                slot["resp"] = {"ok": False, "error": error}
                slot["event"].set()
            self._pending.clear()

    def _dispatch_event(self, event: str, data: Any, req_id: Any) -> None:
        """Dispatch event."""
        scoped = None
        if req_id is not None:
            with self._scoped_event_lock:
                scoped = self._scoped_event_handlers.get(req_id)
        if scoped is not None:
            try:
                scoped(event, data, req_id)
            except Exception:  # noqa: BLE001
                log.exception("%s scoped event handler failed for %s", self.spec.name, event)
            return
        for handler in list(self._event_handlers.get(event, ())):
            try:
                handler(data, req_id)
            except Exception:  # noqa: BLE001
                log.exception("%s event handler failed for %s", self.spec.name, event)

    def on_event(self, event: str, handler: Callable[[Any, Any], None]) -> None:
        """Handle event events."""
        self._event_handlers.setdefault(event, []).append(handler)

    def on_exit(self, handler: Callable[[int | None], None]) -> None:
        """Handle exit events."""
        self._exit_handlers.append(handler)

    def _notify_exit(self, returncode: int | None) -> None:
        """Handle notify exit for worker client."""
        for handler in list(self._exit_handlers):
            try:
                handler(returncode)
            except Exception:  # noqa: BLE001
                log.exception("%s exit handler failed", self.spec.name)

    def _write(self, req: dict[str, Any]) -> None:
        """Write a request to the worker's stdin (thread-safe)."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise WorkerError(f"{self.spec.name} is not running")
        with self._write_lock:
            try:
                protocol.write_message(proc.stdin, req)
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise WorkerError(f"{self.spec.name} write failed: {exc}") from exc

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        """Send a request to the worker; await and return the response unless wait=False."""
        self._ensure_started()
        rid = next(self._ids)
        req = protocol.make_request(rid, method, params or {})
        if not wait:
            self._write(req)
            return None

        ev = threading.Event()
        slot: dict[str, Any] = {"event": ev, "resp": None}
        with self._pending_lock:
            self._pending[rid] = slot
        try:
            self._write(req)
        except WorkerError:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise

        if not ev.wait(timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            tail = self.stderr_tail()
            detail = f"{self.spec.name} call {method!r} timed out after {timeout:.1f}s"
            if tail:
                detail += f"\nRecent {self.spec.name} stderr:\n{tail}"
            raise WorkerError(detail)
        resp = slot["resp"] or {"ok": False, "error": "missing response"}
        if not resp.get("ok"):
            raise WorkerError(str(resp.get("error") or f"{method!r} failed"))
        return resp.get("result")

    def call_with_events(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        on_event: Callable[[str, Any, Any], None],
        on_started: Callable[[Any], None] | None = None,
    ) -> Any:
        """Call a worker method and route events tagged with its request id.

        Streaming brain methods emit generic names like ``reply.chunk``. Scoped
        routing lets the supervisor decide whether those chunks belong to the
        overlay, chat, auth, or an agent run without changing the wire format.
        """
        self._ensure_started()
        rid = next(self._ids)
        req = protocol.make_request(rid, method, params or {})
        ev = threading.Event()
        slot: dict[str, Any] = {"event": ev, "resp": None}
        with self._pending_lock:
            self._pending[rid] = slot
        with self._scoped_event_lock:
            self._scoped_event_handlers[rid] = on_event
        if on_started is not None:
            try:
                on_started(rid)
            except Exception:  # noqa: BLE001
                log.exception("%s stream start callback failed for %s", self.spec.name, method)
        try:
            try:
                self._write(req)
            except WorkerError:
                with self._pending_lock:
                    self._pending.pop(rid, None)
                raise

            if not ev.wait(timeout):
                with self._pending_lock:
                    self._pending.pop(rid, None)
                tail = self.stderr_tail()
                detail = f"{self.spec.name} call {method!r} timed out after {timeout:.1f}s"
                if tail:
                    detail += f"\nRecent {self.spec.name} stderr:\n{tail}"
                raise WorkerError(detail)
            resp = slot["resp"] or {"ok": False, "error": "missing response"}
            if not resp.get("ok"):
                raise WorkerError(str(resp.get("error") or f"{method!r} failed"))
            return resp.get("result")
        finally:
            with self._scoped_event_lock:
                self._scoped_event_handlers.pop(rid, None)

    def restart(self) -> None:
        """Handle restart for worker client."""
        with self._spawn_lock:
            if self._restart_count >= self.spec.restart_limit:
                raise WorkerError(f"{self.spec.name} restart limit exceeded")
            self._restart_count += 1
            self._terminate_locked()
            self._spawn()

    def shutdown(self) -> None:
        """Handle shutdown for worker client."""
        with self._spawn_lock:
            self._shutting_down = True
            self._terminate_locked()

    def _terminate_locked(self) -> None:
        """Handle terminate locked for worker client."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            with self._write_lock:
                if proc.stdin and not proc.stdin.closed:
                    protocol.write_message(proc.stdin, protocol.make_request(0, "__shutdown__"))
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            proc.kill()
            proc.wait(timeout=5.0)


def default_specs() -> dict[str, WorkerSpec]:
    """Handle default specs for runtime supervisor ipc."""
    return {
        "native": WorkerSpec("wisp-native", "runtime.workers.native_host", "native"),
        "ui": WorkerSpec("wisp-ui", "runtime.workers.ui_host", "ui"),
        "brain": WorkerSpec("wisp-brain", "runtime.workers.brain_host", "brain"),
        # The audio worker is the isolated subprocess whose whole purpose is to run
        # native CoreAudio/PortAudio off the Qt UI process, so audio must be enabled
        # here regardless of the global macOS safe-mode default — otherwise
        # core.tts.stream_audio drops every chunk and TTS plays silence even though
        # the brain's "Test TTS" (no device gate) reports OK. A crash in this worker
        # only restarts the worker, which is the point of the isolation.
        "audio": WorkerSpec(
            "wisp-audio",
            "runtime.workers.audio_host",
            "audio",
            env={"WISP_MACOS_ENABLE_AUDIO": "1"},
        ),
    }


class WispSupervisor:
    """Owns all pure-Python workers."""

    def __init__(self, specs: dict[str, WorkerSpec] | None = None) -> None:
        """Initialize the wisp supervisor instance."""
        self.workers = {
            name: WorkerClient(spec)
            for name, spec in (specs or default_specs()).items()
        }

    def start_all(self) -> dict[str, Any]:
        """Start all."""
        startup_timeouts = {
            "native": 20.0,
            "ui": 90.0,
            "brain": 90.0,
            "audio": 45.0,
        }
        results: dict[str, Any] = {}
        for name, worker in self.workers.items():
            results[name] = worker.call(f"{name}.ping", {"value": name}, timeout=startup_timeouts.get(name, 30.0))
        return results

    def call(self, worker: str, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        """Call a method on the named worker and return its result."""
        return self.workers[worker].call(method, params, timeout=timeout)

    def shutdown(self) -> None:
        """Handle shutdown for wisp supervisor."""
        for worker in self.workers.values():
            worker.shutdown()
