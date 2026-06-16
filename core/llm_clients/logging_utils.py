"""Structured logging helpers for LLM client code."""
from __future__ import annotations

import logging
from typing import Any

LOGGER_NAME = "wisp.llm"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_event(
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Log an LLM event with stable extra fields for searchable runtime logs."""
    get_logger().log(level, message, extra={"event": event, **fields})
