"""
core/crash.py — Local crash report writer.

On any unhandled exception, writes a timestamped .crash file next to wisp.log
so problems survive across restarts and are easy to find and share.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime


def write_crash_report(
    exc_type: type,
    exc_value: BaseException,
    exc_tb,
    *,
    thread_name: str = "",
) -> str:
    """
    Write a .crash file and return its path (or "" on failure).
    Safe to call from any thread; never raises.
    """
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv else __file__))
        path = os.path.join(log_dir, f"wisp_{ts}.crash")

        header = [
            f"Wisp crash report — {datetime.now().isoformat()}",
            f"Platform : {sys.platform}",
            f"Python   : {sys.version.splitlines()[0]}",
        ]
        if thread_name:
            header.append(f"Thread   : {thread_name}")

        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(header))
            f.write("\n\n")
            f.write("".join(tb_lines))

        return path
    except Exception:
        return ""
