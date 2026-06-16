"""Post-processing helpers for speech-to-text output."""
from __future__ import annotations

from collections import Counter


def _normal_token(token: str) -> str:
    return token.strip(" \t\r\n.,!?;:\"'()[]{}<>").casefold()


def looks_like_repeated_token_noise(text: str) -> bool:
    """Detect pathological Whisper loops such as ``Cont Cont Cont ...``.

    Real speech can repeat words, but a short token dominating a long transcript
    is almost always decoder noise. Keep the threshold conservative so normal
    phrases like "yes yes yes" are not discarded.
    """
    tokens = [_normal_token(part) for part in text.split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 8:
        return False
    counts = Counter(tokens)
    token, count = counts.most_common(1)[0]
    return len(token) <= 16 and count >= 8 and count / len(tokens) >= 0.8


def clean_transcript(text: str) -> str:
    text = " ".join((text or "").split()).strip()
    if looks_like_repeated_token_noise(text):
        return ""
    return text
