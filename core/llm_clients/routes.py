"""Provider and fallback routing helpers for LLM clients."""
from __future__ import annotations

import config
from core import secret_store


GOOGLE_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def api_key_for(provider: str) -> str:
    p = provider.lower()
    if p == "groq":
        return config.GROQ_API_KEY
    if p == "openai":
        return config.OPENAI_API_KEY
    if p == "anthropic":
        return config.ANTHROPIC_API_KEY
    if p == "google":
        return config.GOOGLE_API_KEY
    if p == "chatgpt":
        return "chatgpt-oauth"
    if p == "copilot":
        return "copilot-token"
    return ""


def credential_source_for_provider(provider: str) -> str:
    p = provider.lower()
    if p == "groq":
        return secret_store.secret_source("GROQ_API_KEY")
    if p == "openai":
        return secret_store.secret_source("OPENAI_API_KEY")
    if p == "anthropic":
        return secret_store.secret_source("ANTHROPIC_API_KEY")
    if p == "google":
        return secret_store.secret_source("GOOGLE_API_KEY")
    if p == "chatgpt":
        return "chatgpt-oauth"
    if p == "copilot":
        return "copilot-keychain"
    return "none"


def parse_model_fallbacks(raw: str) -> list[tuple[str, str]]:
    """Parse provider:model fallback lines from settings."""
    routes: list[tuple[str, str]] = []
    for part in raw.replace(";", "\n").splitlines():
        item = part.strip()
        if not item or item.startswith("#") or ":" not in item:
            continue
        provider, model = item.split(":", 1)
        provider = provider.strip().lower()
        model = model.strip()
        if provider and model:
            routes.append((provider, model))
    return routes


def route_candidates(provider: str, model: str, fallback_raw: str) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    if provider and model:
        routes.append((provider.lower(), model))
    for candidate in parse_model_fallbacks(fallback_raw):
        if candidate not in routes:
            routes.append(candidate)
    return routes
