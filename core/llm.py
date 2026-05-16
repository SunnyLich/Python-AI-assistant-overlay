"""
core/llm.py — Cloud LLM client with streaming support.

Supports:
  - Groq (OpenAI-compatible, fast TTFT)
  - OpenAI
  - Anthropic Claude

Clients are module-level singletons so the TLS connection is reused across
calls, eliminating handshake overhead from every request.
"""
from __future__ import annotations
import config
from typing import Generator

# ------------------------------------------------------------------
# Singleton clients — initialised once, reused across all requests
# ------------------------------------------------------------------
_openai_client = None
_anthropic_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        if config.LLM_PROVIDER.lower() == "groq":
            _openai_client = OpenAI(
                api_key=config.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
        else:
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def stream_response(
    user_message: str,
    image_base64: str | None = None,
) -> Generator[str, None, None]:
    """
    Stream a response from the configured LLM.

    Args:
        user_message: The user's query text.
        image_base64: Optional base64-encoded PNG for vision input.

    Yields:
        Text chunks as they arrive from the API.
    """
    provider = config.LLM_PROVIDER.lower()
    if provider in ("groq", "openai"):
        yield from _stream_openai_compat(user_message, image_base64)
    elif provider == "anthropic":
        yield from _stream_anthropic(user_message, image_base64)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


# ------------------------------------------------------------------
# OpenAI / Groq (OpenAI-compatible)
# ------------------------------------------------------------------

def _stream_openai_compat(
    user_message: str,
    image_base64: str | None,
) -> Generator[str, None, None]:
    client = _get_openai_client()
    messages = _build_openai_messages(user_message, image_base64)

    with client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        stream=True,
        max_tokens=256,
        temperature=0.5,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def _build_openai_messages(user_message: str, image_base64: str | None) -> list:
    system = config.get_system_prompt()
    if image_base64:
        content = [
            {"type": "text", "text": user_message},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
    else:
        content = user_message

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


# ------------------------------------------------------------------
# Anthropic Claude
# ------------------------------------------------------------------

def _stream_anthropic(
    user_message: str,
    image_base64: str | None,
) -> Generator[str, None, None]:
    client = _get_anthropic_client()
    system = config.get_system_prompt()

    if image_base64:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64,
                },
            },
            {"type": "text", "text": user_message},
        ]
    else:
        content = user_message

    with client.messages.stream(
        model=config.LLM_MODEL,
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for text in stream.text_stream:
            yield text
