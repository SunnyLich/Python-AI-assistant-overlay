"""
ui/animation.py — Doll sprite animator.

Loads PNG frames from assets/doll/ and cycles through them on a QTimer.
Frame naming convention: {state}_{frame_index}.png (e.g., idle_0.png, speaking_1.png)
A single frame file named {state}.png is also accepted.
"""
from __future__ import annotations
import os
from typing import Callable
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import QTimer


class DollAnimator:
    """
    Manages per-state sprite animations.
    Calls `on_frame(pixmap)` whenever the displayed frame changes.
    """

    FRAME_INTERVAL_MS = 120  # ~8 fps

    def __init__(self, assets_dir: str, size: tuple[int, int]):
        self._dir = assets_dir
        self._size = size
        self._frames: dict[str, list[QPixmap]] = {}
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._current_state = "idle"
        self._frame_idx = 0
        self._on_frame: Callable[[QPixmap], None] | None = None

    def frame(self, state: str) -> QPixmap:
        """Return the first frame for a state (for static display)."""
        frames = self._get_frames(state)
        return frames[0] if frames else QPixmap()

    def play(self, state: str, on_frame: Callable[[QPixmap], None]):
        """Start animating `state`, calling on_frame for each new frame."""
        self._timer.stop()
        self._current_state = state
        self._frame_idx = 0
        self._on_frame = on_frame
        frames = self._get_frames(state)
        if frames:
            on_frame(frames[0])
        if len(frames) > 1:
            self._timer.start(self.FRAME_INTERVAL_MS)

    def _tick(self):
        frames = self._get_frames(self._current_state)
        if not frames:
            return
        self._frame_idx = (self._frame_idx + 1) % len(frames)
        if self._on_frame:
            self._on_frame(frames[self._frame_idx])

    def _get_frames(self, state: str) -> list[QPixmap]:
        if state in self._frames:
            return self._frames[state]

        frames = []
        # Try numbered frames first: idle_0.png, idle_1.png ...
        idx = 0
        while True:
            path = os.path.join(self._dir, f"{state}_{idx}.png")
            if not os.path.exists(path):
                break
            pm = QPixmap(path).scaled(*self._size)
            frames.append(pm)
            idx += 1

        # Fall back to single file: idle.png
        if not frames:
            path = os.path.join(self._dir, f"{state}.png")
            if os.path.exists(path):
                frames = [QPixmap(path).scaled(*self._size)]

        # Last resort: blank pixmap so the app doesn't crash without assets
        if not frames:
            pm = QPixmap(*self._size)
            pm.fill()
            frames = [pm]

        self._frames[state] = frames
        return frames
