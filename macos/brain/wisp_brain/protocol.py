"""
wisp_brain.protocol — newline-delimited JSON framing for the Swift <-> Python seam.

One JSON object per line, UTF-8, terminated by ``\\n``. Message shapes:

  request   {"id": int, "method": str, "params": {...}}          # host  -> brain
  response  {"id": int, "ok": bool, "result": <any> | "error": str}  # brain -> host
  event     {"event": str, "id": int|null, "data": <any>}        # brain -> host

The framing is intentionally identical to ``core.macos_helper.protocol`` so the
two transports stay interchangeable and the existing IPC tests carry over. The
one superset is the optional ``id`` on events: a streaming method (e.g.
``brain.query``) tags every ``reply.chunk`` event with the originating request
id so the host can route partial output to the right in-flight call. Events with
``id == null`` are broadcast/unsolicited (lifecycle, logs).

Large binary payloads (PCM audio) must NOT be base64'd through here on the hot
path. Per the rewrite plan, audio stays native in Swift and only *paths* cross
this channel; if a streaming binary frame is ever needed it gets its own
length-prefixed type rather than bloating the JSON line protocol.
"""
from __future__ import annotations

import json
from typing import Any, BinaryIO


def write_message(stream: BinaryIO, obj: dict[str, Any]) -> None:
    """Serialize *obj* as one JSON line and flush it to *stream* (a binary file)."""
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read one JSON line from *stream*. Returns the decoded dict, or None on EOF.

    A line that is not valid JSON is skipped (the reader loops) so a stray
    non-protocol write on the channel cannot wedge the reader. Callers treat
    None as "peer closed the pipe".
    """
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF -- peer closed the pipe
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # Not a protocol line (e.g. a library wrote to the wrong fd). Skip it.
            continue


def make_response(req_id: Any, ok: bool, *, result: Any = None, error: str | None = None) -> dict[str, Any]:
    """Build a response message dict for *req_id*."""
    msg: dict[str, Any] = {"id": req_id, "ok": ok}
    if ok:
        msg["result"] = result
    else:
        msg["error"] = error
    return msg


def make_event(event: str, *, data: Any = None, req_id: Any = None) -> dict[str, Any]:
    """Build an event message dict, optionally correlated to *req_id*."""
    return {"event": event, "id": req_id, "data": data}
