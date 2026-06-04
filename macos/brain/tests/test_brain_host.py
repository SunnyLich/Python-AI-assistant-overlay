"""
Phase-1 verification for the wisp_brain sidecar (the Swift <-> Python seam).

Exercises the transport with the dependency-free ``ping`` and streaming
``brain.echo`` handlers, so it runs on any OS with no LLM stack, models, or API
keys -- exactly the off-Mac handshake the rewrite plan calls for. This proves the
foundation (framing, request/response correlation, id-tagged streaming events,
concurrency, cancel, error propagation) before any Swift is compiled.

Run directly:   python macos/brain/tests/test_brain_host.py
Run via pytest: pytest macos/brain/tests/test_brain_host.py
"""
from __future__ import annotations

import itertools
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_BRAIN_DIR = Path(__file__).resolve().parents[1]   # macos/brain
sys.path.insert(0, str(_BRAIN_DIR))

from wisp_brain import protocol  # noqa: E402


class BrainSidecar:
    """Minimal Python stand-in for the Swift BrainClient: spawn the sidecar, send
    requests, correlate responses by id, and collect id-tagged stream events."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "wisp_brain.host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_BRAIN_DIR),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            bufsize=0,
        )
        self._pending: dict[int, dict[str, Any]] = {}
        self._events: dict[Any, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        out = self._proc.stdout
        assert out is not None
        while True:
            msg = protocol.read_message(out)
            if msg is None:
                break
            if msg.get("event") is not None:
                with self._lock:
                    self._events.setdefault(msg.get("id"), []).append(msg)
                continue
            rid = msg.get("id")
            with self._lock:
                slot = self._pending.pop(rid, None)
            if slot is not None:
                slot["resp"] = msg
                slot["event"].set()
        with self._lock:
            for slot in self._pending.values():
                slot["resp"] = {"ok": False, "error": "sidecar exited"}
                slot["event"].set()
            self._pending.clear()

    def call(self, method: str, params: dict | None = None, *, timeout: float = 15.0) -> Any:
        rid = next(self._ids)
        ev = threading.Event()
        slot = {"event": ev, "resp": None}
        with self._lock:
            self._pending[rid] = slot
        with self._write_lock:
            protocol.write_message(self._proc.stdin, {"id": rid, "method": method, "params": params or {}})
        if not ev.wait(timeout):
            raise TimeoutError(f"{method!r} timed out")
        resp = slot["resp"] or {"ok": False, "error": "no response"}
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error") or f"{method!r} failed")
        return rid, resp.get("result")

    def events_for(self, rid: Any) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events.get(rid, []))

    def shutdown(self) -> None:
        try:
            with self._write_lock:
                protocol.write_message(self._proc.stdin, {"id": 0, "method": "__shutdown__"})
            self._proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self._proc.kill()


def test_ping_round_trip():
    s = BrainSidecar()
    try:
        _, result = s.call("ping", {"value": "hi"})
        assert result["pong"] is True
        assert result["value"] == "hi"
        assert isinstance(result["pid"], int) and result["pid"] > 0
    finally:
        s.shutdown()


def test_echo_streams_id_tagged_chunks_then_final():
    s = BrainSidecar()
    try:
        rid, result = s.call("brain.echo", {"text": "one two three four"})
        # Final result is the reassembled text...
        assert result["text"] == "one two three four"
        # ...and the partials arrived as reply.chunk events tagged with this id.
        evs = s.events_for(rid)
        chunks = [e for e in evs if e["event"] == "reply.chunk"]
        done = [e for e in evs if e["event"] == "reply.done"]
        assert len(chunks) == 4, f"expected 4 chunks, got {len(chunks)}"
        assert all(e["id"] == rid for e in evs), "every event must carry the request id"
        assert "".join(c["data"]["text"] for c in chunks) == "one two three four"
        assert done and done[-1]["data"]["text"] == "one two three four"
    finally:
        s.shutdown()


def test_concurrent_streams_do_not_interleave_ids():
    """A long stream must not block a second request, and each stream's events
    must stay correlated to its own id (proves per-request threading)."""
    s = BrainSidecar()
    try:
        # Kick off a slow stream on a background thread...
        slow_box: dict[str, Any] = {}

        def slow():
            slow_box["rid"], slow_box["res"] = s.call(
                "brain.echo", {"text": "a b c d e", "delay": 0.05}, timeout=20
            )

        t = threading.Thread(target=slow)
        t.start()
        # ...and a fast ping should return well before the slow stream finishes.
        _, ping_res = s.call("ping", timeout=5)
        assert ping_res["pong"] is True
        t.join(timeout=20)
        slow_chunks = [e for e in s.events_for(slow_box["rid"]) if e["event"] == "reply.chunk"]
        assert len(slow_chunks) == 5
    finally:
        s.shutdown()


def test_cancel_stops_a_stream_early():
    s = BrainSidecar()
    try:
        rid = next(s._ids)
        ev = threading.Event()
        s._pending[rid] = {"event": ev, "resp": None}
        # Long, slow stream we will cancel mid-flight.
        with s._write_lock:
            protocol.write_message(
                s._proc.stdin,
                {"id": rid, "method": "brain.echo",
                 "params": {"text": " ".join(str(i) for i in range(50)), "delay": 0.02}},
            )
        # Give it a moment to emit a few chunks, then cancel by target id.
        threading.Event().wait(0.1)
        s.call("brain.cancel", {"target": rid})
        ev.wait(5)
        chunks = [e for e in s.events_for(rid) if e["event"] == "reply.chunk"]
        assert len(chunks) < 50, "cancel should cut the stream short"
    finally:
        s.shutdown()


def test_unknown_method_errors():
    s = BrainSidecar()
    try:
        raised = False
        try:
            s.call("does.not.exist", timeout=5)
        except RuntimeError:
            raised = True
        assert raised
    finally:
        s.shutdown()


def _run_directly() -> int:
    tests = [
        test_ping_round_trip,
        test_echo_streams_id_tagged_chunks_then_final,
        test_concurrent_streams_do_not_interleave_ids,
        test_cancel_stops_a_stream_early,
        test_unknown_method_errors,
    ]
    passed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {fn.__name__}")
            passed += 1
    print(f"--- {passed}/{len(tests)} passed ---")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run_directly())
