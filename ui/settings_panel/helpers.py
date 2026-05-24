"""Pure helper functions for settings UI values."""
from __future__ import annotations


def parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for part in raw.replace(";", "\n").splitlines():
        item = part.strip()
        if not item or item.startswith("#") or ":" not in item:
            continue
        provider, model = [piece.strip() for piece in item.split(":", 1)]
        if provider and model:
            rows.append((provider, model))
    return rows
