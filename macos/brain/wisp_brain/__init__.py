"""
wisp_brain — the headless Python "brain" sidecar for the native macOS app.

This is the INVERSION of ``core.macos_helper``: there, the Qt GUI was the host
and a Python subprocess did native work; here, a native Swift app is the host and
*this* Python process is the worker that runs the OS-agnostic brain (LLM routing,
agent, context router, memory, STT model). Swift spawns ``python -m
wisp_brain.host`` and speaks newline-delimited JSON over its stdin/stdout.

See ``macos/README.md`` for the protocol contract and ``MACOS_NATIVE_PLAN.md``
(repo root) for the overall rewrite plan.
"""
from __future__ import annotations

__all__ = ["protocol", "handlers", "host"]
