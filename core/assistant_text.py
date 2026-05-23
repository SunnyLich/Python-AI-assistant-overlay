from __future__ import annotations

from typing import Iterable


_OPEN_TAGS = ("<think>", "<thinking>", "<thought>")
_CLOSE_TAGS = ("</think>", "</thinking>", "</thought>")


def _relevant_tags(in_thought: bool) -> tuple[str, ...]:
    return _CLOSE_TAGS if in_thought else _OPEN_TAGS


def _find_next_tag(buffer: str, in_thought: bool) -> tuple[int, str] | None:
    lowered = buffer.lower()
    matches = [
        (idx, tag)
        for tag in _relevant_tags(in_thought)
        if (idx := lowered.find(tag)) >= 0
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: item[0])


def _partial_suffix_len(buffer: str, in_thought: bool) -> int:
    lowered = buffer.lower()
    best = 0
    for tag in _relevant_tags(in_thought):
        max_len = min(len(tag) - 1, len(lowered))
        for size in range(max_len, 0, -1):
            if lowered.endswith(tag[:size]):
                best = max(best, size)
                break
    return best


def _merge_segments(segments: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    merged: list[tuple[str, bool]] = []
    for text, is_thought in segments:
        if not text:
            continue
        if merged and merged[-1][1] == is_thought:
            merged[-1] = (merged[-1][0] + text, is_thought)
        else:
            merged.append((text, is_thought))
    return merged


class ThoughtStreamParser:
    def __init__(self):
        self._buffer = ""
        self._in_thought = False

    def feed(self, chunk: str) -> list[tuple[str, bool]]:
        if not chunk:
            return []
        self._buffer += chunk
        return self._drain(final=False)

    def finish(self) -> list[tuple[str, bool]]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        while self._buffer:
            match = _find_next_tag(self._buffer, self._in_thought)
            if match is None:
                keep = 0 if final else _partial_suffix_len(self._buffer, self._in_thought)
                emit = self._buffer if keep == 0 else self._buffer[:-keep]
                self._buffer = "" if keep == 0 else self._buffer[-keep:]
                if emit:
                    out.append((emit, self._in_thought))
                break
            idx, tag = match
            if idx > 0:
                out.append((self._buffer[:idx], self._in_thought))
            self._buffer = self._buffer[idx + len(tag):]
            self._in_thought = not self._in_thought
        return _merge_segments(out)


def split_tagged_text(text: str) -> list[tuple[str, bool]]:
    parser = ThoughtStreamParser()
    segments = parser.feed(text)
    segments.extend(parser.finish())
    return _merge_segments(segments)


def extract_reply_text(text: str) -> str:
    return "".join(segment for segment, is_thought in split_tagged_text(text) if not is_thought)


def extract_thought_text(text: str) -> str:
    return "".join(segment for segment, is_thought in split_tagged_text(text) if is_thought)


def merge_segment_iterables(*segment_lists: Iterable[tuple[str, bool]]) -> list[tuple[str, bool]]:
    merged: list[tuple[str, bool]] = []
    for segments in segment_lists:
        merged.extend(segments)
    return _merge_segments(merged)