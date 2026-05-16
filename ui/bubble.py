"""
ui/bubble.py — Live speech bubble next to the doll icon.

Shows the last 2 word-wrapped lines of streaming LLM text.
Positioned to the left of the doll, tail points right toward it.
Auto-hides a few seconds after the response finishes.
"""
from __future__ import annotations
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, QRect, QElapsedTimer
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics,
    QBrush, QPen, QPainterPath,
)
import config

_BG           = QColor(28, 28, 36, 220)
_TEXT         = QColor(230, 230, 230)
_DOTS_COLOR   = QColor(140, 140, 165)
_PAD          = 12
_LINE_GAP     = 5
_TAIL_W       = 12
_TAIL_H       = 14
_RADIUS       = 10
_FONT_SIZE    = 10
_DOLL_W       = 80
_DOLL_H       = 80
_DOLL_MARGIN  = 20
_HIDE_DELAY    = 8_000   # ms after finish() before hiding
_DEFAULT_WPM = 170  # fallback if config not yet loaded


class SpeechBubble(QWidget):
    """Compact always-on-top widget that streams LLM text next to the doll."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._font = QFont("Segoe UI", _FONT_SIZE)
        self._fm = QFontMetrics(self._font)
        self._line_h = self._fm.height() + _LINE_GAP

        self._full_text = ""
        self._lines: list[str] = []
        self._thinking = False
        self._dot_count = 1

        # Word-reveal mode (syncs text display to audio playback speed)
        self._reveal_mode = False
        self._pending_words: list[str] = []
        self._revealed_count = 0
        self._timestamp_mode = False      # True = driven by Cartesia timestamps
        self._audio_elapsed = QElapsedTimer()  # measures ms since audio start
        self._pre_audio_timestamps: list[tuple] = []  # batches that arrived before audio start

        # Derive size from config
        screen = QApplication.primaryScreen().availableGeometry()
        self._bubble_w = config.BUBBLE_WIDTH
        self._text_w = self._bubble_w - _PAD * 2
        self._bubble_h = _PAD * 2 + self._line_h * config.BUBBLE_LINES - _LINE_GAP
        self.setFixedSize(self._bubble_w + _TAIL_W, self._bubble_h)

        # Position: left of doll, vertically centered with it
        doll_sz = config.DOLL_SIZE
        doll_x = screen.x() + screen.width()  - doll_sz - _DOLL_MARGIN
        doll_y = screen.y() + screen.height() - doll_sz - _DOLL_MARGIN
        bx = doll_x - self._bubble_w - _TAIL_W - 6
        by = doll_y + (config.DOLL_SIZE - self._bubble_h) // 2
        self.move(bx, by)

        # Dot animation (while thinking)
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(450)
        self._dot_timer.timeout.connect(self._tick_dots)

        # Auto-hide after response finishes
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_DELAY)
        self._hide_timer.timeout.connect(self.hide)

        # Word-reveal timer
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setInterval(int(60_000 / config.BUBBLE_REVEAL_WPM))
        self._reveal_timer.timeout.connect(self._reveal_next_word)

    # ------------------------------------------------------------------
    # Public API (called via Qt signals from worker thread)
    # ------------------------------------------------------------------

    def start_thinking(self):
        """Show animated dots while waiting for the first LLM token."""
        self._full_text = ""
        self._lines = []
        self._thinking = True
        self._dot_count = 1
        self._pending_words = []
        self._revealed_count = 0
        self._reveal_mode = False
        self._pre_audio_timestamps = []
        self._reveal_timer.stop()
        self._hide_timer.stop()
        self._dot_timer.start()
        self.show()
        self.raise_()
        self.update()

    def start_word_reveal(self):
        """Start revealing buffered words at speech rate (called when audio begins)."""
        self._thinking = False
        self._dot_timer.stop()
        self._audio_elapsed.start()   # t=0 for timestamp scheduling
        if not self._reveal_mode:
            # Reveal not yet started (e.g. very fast first chunk) — start fresh.
            self._reveal_mode = True
            self._timestamp_mode = False
            self._revealed_count = 0
            self._full_text = ""
            self._lines = []
            self._reveal_timer.setInterval(int(60_000 / max(1, config.BUBBLE_REVEAL_WPM)))
            self._reveal_timer.start()
        # else: WPM already running from append_chunk — keep going, elapsed timer updated above.
        # Drain any timestamp batches that arrived before audio started
        for words, start_ms in self._pre_audio_timestamps:
            self.schedule_words(words, start_ms)
        self._pre_audio_timestamps = []
        self.show()
        self.update()

    def schedule_words(self, words: list, start_ms: list):
        """
        Cartesia timestamp-driven reveal. Cancels the WPM fallback timer and
        schedules each word to appear at its exact spoken time.
        """
        if not self._reveal_mode:
            # Audio hasn't started yet — buffer for later
            self._pre_audio_timestamps.append((words, start_ms))
            return
        if not self._timestamp_mode:
            # First timestamp batch: switch modes and cancel the fallback timer.
            self._reveal_timer.stop()
            self._timestamp_mode = True
            self._full_text = ""
            self._lines = []
        elapsed = self._audio_elapsed.elapsed()
        for word, t_ms in zip(words, start_ms):
            delay = max(0, t_ms - elapsed)
            QTimer.singleShot(delay, lambda w=word: self._show_word(w))

    def _show_word(self, word: str):
        if self._full_text:
            self._full_text += " " + word
        else:
            self._full_text = word
        self._rewrap()
        self.update()

    def append_chunk(self, chunk: str):
        """Buffer incoming LLM chunk. Starts WPM reveal on first token if not already active."""
        if self._thinking:
            self._thinking = False
            self._dot_timer.stop()
        self._pending_words.extend(chunk.split())
        # Kick off WPM reveal on first token so words always appear gradually,
        # even when TTS=none.  start_word_reveal() / schedule_words() will take
        # over once audio actually starts.
        if not self._reveal_mode and not self._timestamp_mode:
            self._reveal_mode = True
            self._reveal_timer.setInterval(int(60_000 / max(1, config.BUBBLE_REVEAL_WPM)))
            self._reveal_timer.start()

    def finish(self):
        """Called when TTS playback finishes; starts hide countdown."""
        self._dot_timer.stop()
        if self._timestamp_mode:
            # Timestamp mode: all words already scheduled via QTimer.singleShot.
            self._reveal_timer.stop()
        elif self._reveal_mode:
            # WPM mode: let timer keep draining remaining words naturally.
            # (hide timer starting now gives 8 s for the last few words to appear)
            pass
        else:
            # No reveal active (TTS=none path with no chunks): flush immediately.
            self._full_text = " ".join(self._pending_words)
            self._rewrap()
            self.update()
        self._reveal_mode = False
        self._timestamp_mode = False
        self._hide_timer.start()

    def clear(self):
        """Hard reset — hide immediately."""
        self._hide_timer.stop()
        self._dot_timer.stop()
        self._reveal_timer.stop()
        self._reveal_mode = False
        self._timestamp_mode = False
        self._pending_words = []
        self._revealed_count = 0
        self._pre_audio_timestamps = []
        self._thinking = False
        self._full_text = ""
        self._lines = []
        self.hide()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tick_dots(self):
        self._dot_count = (self._dot_count % 3) + 1
        self.update()

    def _reveal_next_word(self):
        if self._revealed_count < len(self._pending_words):
            self._revealed_count += 1
            self._full_text = " ".join(self._pending_words[:self._revealed_count])
            self._rewrap()
            self.update()
        # Timer keeps running until finish() is called (which flushes remaining words)

    def _rewrap(self):
        """Word-wrap _full_text to _text_w; keep only the last BUBBLE_LINES lines."""
        words = self._full_text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            if self._fm.horizontalAdvance(test) <= self._text_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        self._lines = lines[-config.BUBBLE_LINES:]

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Build path: rounded rect body + tail triangle on the right
        path = QPainterPath()
        path.addRoundedRect(0, 0, self._bubble_w, self._bubble_h, _RADIUS, _RADIUS)
        mid_y = self._bubble_h // 2
        path.moveTo(self._bubble_w,            mid_y - _TAIL_H // 2)
        path.lineTo(self._bubble_w + _TAIL_W,  mid_y)
        path.lineTo(self._bubble_w,            mid_y + _TAIL_H // 2)
        path.closeSubpath()

        p.setBrush(QBrush(_BG))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)

        # Content
        p.setFont(self._font)
        if self._thinking:
            dots = "●" * self._dot_count + "○" * (3 - self._dot_count)
            p.setPen(QPen(_DOTS_COLOR))
            p.drawText(0, 0, self._bubble_w, self._bubble_h,
                       Qt.AlignmentFlag.AlignCenter, dots)
        else:
            p.setPen(QPen(_TEXT))
            y = _PAD
            for line in self._lines:
                p.drawText(_PAD, y, self._text_w, self._line_h,
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           line)
                y += self._line_h

        p.end()
