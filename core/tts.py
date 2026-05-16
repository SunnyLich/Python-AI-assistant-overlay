"""
core/tts.py — Streaming TTS client.

Supports:
  - Cartesia 3.x (~75ms TTFT) via WebSocket — pcm_f32le, 44100 Hz
  - ElevenLabs (~300-500ms TTFT)

The Cartesia WebSocket connection is kept alive as a singleton so every call
avoids the ~600ms handshake penalty. Call prewarm() at app startup.

Yields raw PCM audio bytes as they stream in, ready for sounddevice playback.

For Cartesia, also exposes stream_audio_from_chunks() which accepts an
iterator of text pieces (e.g. LLM stream chunks) so TTS starts before the
full LLM response is available — this is the lowest-latency path.
"""
from __future__ import annotations
import config
from typing import Generator, Iterable


SAMPLE_RATE = 44100   # Hz
CHANNELS = 1
DTYPE = "float32"     # pcm_f32le

# ------------------------------------------------------------------
# Singleton Cartesia WebSocket connection
# ------------------------------------------------------------------
_cartesia_client = None
_cartesia_ws_manager = None   # the context manager returned by websocket_connect()
_cartesia_ws = None           # the entered connection object (has .context())


def _get_cartesia_ws():
    global _cartesia_client, _cartesia_ws_manager, _cartesia_ws
    if _cartesia_ws is None:
        from cartesia import Cartesia  # type: ignore
        _cartesia_client = Cartesia(api_key=config.CARTESIA_API_KEY)
        _cartesia_ws_manager = _cartesia_client.tts.websocket_connect()
        _cartesia_ws = _cartesia_ws_manager.__enter__()
    return _cartesia_ws


def prewarm():
    """
    Open connections eagerly at app startup so the first user query is fast.
    Call once from main.py after the event loop starts.
    """
    if config.TTS_PROVIDER.lower() == "cartesia":
        _get_cartesia_ws()


def close():
    """Close the persistent WebSocket (call on app exit)."""
    global _cartesia_ws, _cartesia_ws_manager
    if _cartesia_ws_manager is not None:
        try:
            _cartesia_ws_manager.__exit__(None, None, None)
        except Exception:
            pass
        _cartesia_ws = None
        _cartesia_ws_manager = None


def stream_audio(text: str) -> Generator[bytes, None, None]:
    """
    Stream TTS audio bytes for the given complete text string.

    Yields:
        Raw PCM float32 audio bytes (mono, SAMPLE_RATE Hz).
    """
    yield from stream_audio_from_chunks(iter([text]))


def stream_audio_from_chunks(text_chunks: Iterable[str]) -> Generator[bytes, None, None]:
    """
    Stream TTS by feeding text pieces incrementally (e.g. from an LLM stream).
    Audio starts arriving before all text is available.

    Yields:
        Raw PCM float32 audio bytes.
    """
    provider = config.TTS_PROVIDER.lower()
    if provider == "cartesia":
        yield from _stream_cartesia(text_chunks)
    elif provider == "elevenlabs":
        # ElevenLabs doesn't support incremental push; collect then stream
        yield from _stream_elevenlabs("".join(text_chunks))
    elif provider == "none":
        return
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


# ------------------------------------------------------------------
# Cartesia 3.x  (WebSocket, pcm_f32le, sonic-3)
# ------------------------------------------------------------------

def _stream_cartesia(text_chunks: Iterable[str]) -> Generator[bytes, None, None]:
    ws = _get_cartesia_ws()

    ctx = ws.context(
        model_id="sonic-3",
        voice={"mode": "id", "id": config.CARTESIA_VOICE_ID},
        output_format={
            "container": "raw",
            "encoding": "pcm_f32le",
            "sample_rate": SAMPLE_RATE,
        },
        language="en",
    )

    for piece in text_chunks:
        if piece:
            ctx.push(piece)
    ctx.no_more_inputs()

    for response in ctx.receive():
        if response.type == "chunk" and response.audio:
            yield response.audio


# ------------------------------------------------------------------
# ElevenLabs
# ------------------------------------------------------------------

def _stream_elevenlabs(text: str) -> Generator[bytes, None, None]:
    global SAMPLE_RATE, DTYPE
    SAMPLE_RATE = 22050
    DTYPE = "int16"

    from elevenlabs.client import ElevenLabs  # type: ignore

    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    audio_stream = client.generate(
        text=text,
        stream=True,
        output_format="pcm_22050",
    )
    for chunk in audio_stream:
        if chunk:
            yield chunk
