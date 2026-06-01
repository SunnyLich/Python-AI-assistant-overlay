"""
ui/bubble.py — Live speech bubble next to the doll icon.

Shows the last 2 word-wrapped lines of streaming LLM text.
Positioned to the left of the doll, tail points right toward it.
Auto-hides a few seconds after the response finishes.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QTimer, QElapsedTimer
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics,
    QBrush, QPen, QPainterPath,
)
import config

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
_HIDE_DELAY    = 3_500   # fallback ms after finish() before hiding


def _color(value: str, fallback: QColor) -> QColor:
    raw = (value or "").strip()
    if raw.startswith("#") and len(raw) == 9:
        try:
            return QColor(
                int(raw[1:3], 16),
                int(raw[3:5], 16),
                int(raw[5:7], 16),
                int(raw[7:9], 16),
            )
        except ValueError:
            return QColor(fallback)
    qcolor = QColor(raw)
    return qcolor if qcolor.isValid() else QColor(fallback)


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
        self._bold_font = QFont("Segoe UI", _FONT_SIZE)
        self._bold_font.setBold(True)
        self._fm = QFontMetrics(self._font)
        self._bold_fm = QFontMetrics(self._bold_font)
        self._space_w = self._fm.horizontalAdvance(" ")
        self._line_h = self._fm.height() + _LINE_GAP
        self._bubble_color = _color(config.BUBBLE_COLOR, QColor(28, 28, 36, 220))
        self._text_color = _color(config.BUBBLE_TEXT_COLOR, QColor(230, 230, 230))
        self._read_word_color = _color(config.BUBBLE_READ_WORD_COLOR, QColor(77, 163, 255))
        self._thought_color = QColor(150, 150, 165)

        self._full_text = ""
        self._thought_text = ""
        self._lines: list[str] = []
        self._line_segments: list[list[tuple[str, bool, int | None, bool]]] = []
        self._thinking = False
        self._dot_count = 1
        self._last_chunk_ended_with_space = True  # guards mid-word chunk merging

        # Read-position mode (syncs highlighting to audio playback speed)
        self._reveal_mode = False
        self._finishing = False   # True after finish() while WPM timer still draining words
        self._pending_words: list[str] = []
        self._revealed_count = 0
        self._timestamp_mode = False      # True = driven by Cartesia timestamps
        self._audio_started = False       # True after first PCM chunk reaches playback
        self._audio_elapsed = QElapsedTimer()  # measures ms since audio start
        self._pre_audio_timestamps: list[tuple] = []  # batches that arrived before audio start
        self._speed_boosting = False
        self._highlight_generation = 0

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
        self._hide_timer.setInterval(self._hide_delay_ms())
        self._hide_timer.timeout.connect(self.hide)

        # Word-reveal timer
        self._reveal_timer = QTimer(self)
        self._apply_reveal_speed()
        self._reveal_timer.timeout.connect(self._reveal_next_word)

        # Drag support
        self._drag_offset = None          # QPoint while dragging
        self._companion_callback = None   # called with new QPoint after each drag move
        self._hide_callback = None        # called when this widget hides (for icon sync)
        self._speed_callback = None       # called with True while hold-to-speed is active

    # ------------------------------------------------------------------
    # Drag API
    # ------------------------------------------------------------------

    def set_companion_callback(self, fn):
        """Register a callback(new_bubble_pos: QPoint) called while dragging."""
        self._companion_callback = fn

    def set_hide_callback(self, fn):
        """Register a zero-argument callback called whenever this widget hides."""
        self._hide_callback = fn

    def set_speed_callback(self, fn):
        """Register callback(enabled: bool) for hold-to-speed state."""
        self._speed_callback = fn

    def apply_config(self):
        """Apply live bubble size/line/speed settings after config.reload()."""
        self._bubble_w = config.BUBBLE_WIDTH
        self._text_w = self._bubble_w - _PAD * 2
        self._bubble_h = _PAD * 2 + self._line_h * config.BUBBLE_LINES - _LINE_GAP
        self.setFixedSize(self._bubble_w + _TAIL_W, self._bubble_h)
        self._bubble_color = _color(config.BUBBLE_COLOR, QColor(28, 28, 36, 220))
        self._text_color = _color(config.BUBBLE_TEXT_COLOR, QColor(230, 230, 230))
        self._read_word_color = _color(config.BUBBLE_READ_WORD_COLOR, QColor(77, 163, 255))
        self._hide_timer.setInterval(self._hide_delay_ms())
        self._apply_reveal_speed()
        if self._full_text:
            self._rewrap()
        self.update()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        if self._hide_callback:
            self._hide_callback()

    def doll_pos_for_bubble(self, bubble_pos, doll_size: int):
        """Given this bubble's top-left position, return where the doll icon should sit."""
        from PySide6.QtCore import QPoint
        doll_x = bubble_pos.x() + self._bubble_w + _TAIL_W + 6
        doll_y = bubble_pos.y() - (doll_size - self._bubble_h) // 2
        return QPoint(doll_x, doll_y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_speed_boost(True)
            self._drag_offset = self.pos() - event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() + self._drag_offset
            self.move(new_pos)
            if self._companion_callback:
                self._companion_callback(new_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_speed_boost(False)
            self._drag_offset = None
        super().mouseReleaseEvent(event)

    def show_listening(self):
        """Show a static mic indicator while the user holds F9."""
        self._full_text = ""
        self._thought_text = ""
        self._highlight_generation += 1
        self._lines = ["\u25cf Recording — release to send"]
        self._line_segments = [[("Recording - release to send", False, None, False)]]
        self._thinking = False
        self._pending_words = []
        self._revealed_count = 0
        self._reveal_mode = False
        self._audio_started = False
        self._pre_audio_timestamps = []
        self._reveal_timer.stop()
        self._dot_timer.stop()
        self._hide_timer.stop()
        self.show()
        self.raise_()
        self.update()

    def start_thinking(self):
        """Show animated dots while waiting for the first LLM token."""
        self._full_text = ""
        self._thought_text = ""
        self._highlight_generation += 1
        self._lines = []
        self._line_segments = []
        self._thinking = True
        self._dot_count = 1
        self._last_chunk_ended_with_space = True
        self._pending_words = []
        self._revealed_count = 0
        self._reveal_mode = False
        self._audio_started = False
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
        self._audio_started = True
        self._highlight_generation += 1
        self._audio_elapsed.start()   # t=0 for timestamp scheduling
        if not self._reveal_mode:
            # Reveal not yet started (e.g. very fast first chunk) — start fresh.
            self._reveal_mode = True
            self._timestamp_mode = False
            self._revealed_count = 0
            self._full_text = " ".join(self._pending_words)
            self._lines = []
            self._line_segments = []
            self._apply_reveal_speed()
            self._reveal_timer.start()
        # else: WPM already running from append_chunk — keep going, elapsed timer updated above.
        # Drain any timestamp batches that arrived before audio started
        for words, start_ms in self._pre_audio_timestamps:
            self.schedule_words(words, start_ms)
        self._pre_audio_timestamps = []
        self._rewrap()
        self.show()
        self.update()

    def schedule_words(self, words: list, start_ms: list):
        """
        Cartesia timestamp-driven reveal. Cancels the WPM fallback timer and
        schedules each word to appear at its exact spoken time.
        """
        if not self._audio_started:
            # Timestamps can arrive before the first PCM chunk is audible.
            # Buffer them so word styling is anchored to playback, not TTS generation.
            self._pre_audio_timestamps.append((words, start_ms))
            return
        if not self._timestamp_mode:
            # First timestamp batch: switch modes and cancel the fallback timer.
            self._reveal_timer.stop()
            self._timestamp_mode = True
            self._revealed_count = 0
            self._lines = []
        elapsed = self._audio_elapsed.elapsed()
        playback_rate = self._current_tts_rate()
        generation = self._highlight_generation
        for word, t_ms in zip(words, start_ms):
            delay = max(0, int(t_ms / playback_rate - elapsed))
            QTimer.singleShot(delay, lambda w=word, g=generation: self._advance_highlight(w, g))

    def _advance_highlight(self, word: str | None = None, generation: int | None = None):
        if generation is not None and generation != self._highlight_generation:
            return
        if word and self._revealed_count >= len(self._pending_words):
            self._pending_words.append(word)
            self._full_text = " ".join(self._pending_words)
        if self._revealed_count < len(self._pending_words):
            self._revealed_count += 1
        self._rewrap()
        self.update()

    def append_chunk(self, chunk: str, is_thought: bool = False):
        """Buffer incoming LLM chunk. Starts WPM reveal on first token if not already active."""
        if not chunk:
            return
        if self._thinking:
            self._thinking = False
            self._dot_timer.stop()
        if is_thought:
            self._thought_text += chunk
            self._rewrap()
            self.show()
            self.raise_()
            self.update()
            return
        new_words = chunk.split()
        if new_words:
            # If this chunk starts mid-word (no leading space) and the previous
            # chunk also ended mid-word (no trailing space), merge into last word.
            if (self._pending_words
                    and not chunk[0].isspace()
                    and not self._last_chunk_ended_with_space):
                self._pending_words[-1] += new_words[0]
                new_words = new_words[1:]
            self._pending_words.extend(new_words)
            self._full_text = " ".join(self._pending_words)
        self._last_chunk_ended_with_space = chunk[-1].isspace()
        # Kick off WPM reveal on first token so words always appear gradually,
        # even when TTS=none.  start_word_reveal() / schedule_words() will take
        # over once audio actually starts.
        if not self._reveal_mode and not self._timestamp_mode:
            self._reveal_mode = True
            self._apply_reveal_speed()
            self._reveal_timer.start()
        self._rewrap()
        self.show()
        self.raise_()
        self.update()

    def finish(self):
        """Called when TTS playback finishes; reveals remaining words then hides."""
        self._dot_timer.stop()
        if self._timestamp_mode:
            # Timestamp mode: all words already scheduled via QTimer.singleShot.
            self._reveal_timer.stop()
            self._reveal_mode = False
            self._timestamp_mode = False
            self._hide_timer.start()
        elif self._revealed_count < len(self._pending_words):
            # WPM timer still has words to show — let it finish naturally, then hide.
            self._finishing = True
            self._reveal_mode = False
            self._timestamp_mode = False
        else:
            # All words already revealed.
            self._reveal_timer.stop()
            self._reveal_mode = False
            self._timestamp_mode = False
            self._hide_timer.start()

    def clear(self):
        """Hard reset — hide immediately."""
        self._hide_timer.stop()
        self._dot_timer.stop()
        self._reveal_timer.stop()
        self._reveal_mode = False
        self._finishing = False
        self._timestamp_mode = False
        self._audio_started = False
        self._last_chunk_ended_with_space = True
        self._speed_boosting = False
        self._highlight_generation += 1
        self._pending_words = []
        self._revealed_count = 0
        self._pre_audio_timestamps = []
        self._thinking = False
        self._full_text = ""
        self._thought_text = ""
        self._lines = []
        self._line_segments = []
        self.hide()

    def show_notice(self, text: str, *, timeout_ms: int = 12000):
        """Show a compact non-streaming notice next to the doll icon."""
        self._hide_timer.stop()
        self._dot_timer.stop()
        self._reveal_timer.stop()
        self._reveal_mode = False
        self._finishing = False
        self._timestamp_mode = False
        self._audio_started = False
        self._thinking = False
        self._thought_text = ""
        self._pending_words = text.split()
        self._revealed_count = len(self._pending_words)
        self._full_text = " ".join(self._pending_words)
        self._rewrap()
        self.show()
        self.raise_()
        self.update()
        if timeout_ms > 0:
            self._hide_timer.setInterval(timeout_ms)
            self._hide_timer.start()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tick_dots(self):
        self._dot_count = (self._dot_count % 3) + 1
        self.update()

    def _current_reveal_wpm(self) -> int:
        if self._speed_boosting:
            return max(1, int(getattr(config, "BUBBLE_HOLD_REVEAL_WPM", 480)))
        return max(1, int(getattr(config, "BUBBLE_REVEAL_WPM", 170)))

    @staticmethod
    def _hide_delay_ms() -> int:
        return max(500, int(getattr(config, "BUBBLE_HIDE_DELAY_MS", _HIDE_DELAY)))

    def _current_tts_rate(self) -> float:
        if self._speed_boosting:
            rate = getattr(config, "TTS_HOLD_PLAYBACK_RATE", 1.35)
        else:
            rate = getattr(config, "TTS_PLAYBACK_RATE", 1.0)
        return max(0.25, min(4.0, float(rate)))

    def _apply_reveal_speed(self):
        self._reveal_timer.setInterval(max(1, int(60_000 / self._current_reveal_wpm())))

    def _set_speed_boost(self, enabled: bool):
        if self._speed_boosting == enabled:
            return
        self._speed_boosting = enabled
        self._apply_reveal_speed()
        if self._speed_callback:
            self._speed_callback(enabled)

    def _reveal_next_word(self):
        if self._revealed_count < len(self._pending_words):
            self._advance_highlight()
        if self._finishing and self._revealed_count >= len(self._pending_words):
            self._reveal_timer.stop()
            self._finishing = False
            self._hide_timer.start()
        # Timer keeps running until finish() is called (which sets _finishing)

    def _rewrap(self):
        """Word-wrap _full_text and scroll the visible window to the read position."""
        thought_words = self._markdown_words(self._thought_text)
        reply_words = self._markdown_words(self._full_text)
        words: list[tuple[str, bool, int | None, bool]] = []
        for word, bold in thought_words:
            words.append((word, bold, None, True))
        for reply_idx, (word, bold) in enumerate(reply_words):
            words.append((word, bold, reply_idx, False))
        lines: list[list[tuple[str, bool, int | None, bool]]] = []
        current: list[tuple[str, bool, int | None, bool]] = []
        current_w = 0
        for word, bold, reply_idx, is_thought in words:
            fm = self._bold_fm if bold else self._fm
            word_w = fm.horizontalAdvance(word)
            extra_space = self._space_w if current else 0
            if current and current_w + extra_space + word_w > self._text_w:
                lines.append(current)
                current = [(word, bold, reply_idx, is_thought)]
                current_w = word_w
            else:
                current.append((word, bold, reply_idx, is_thought))
                current_w += extra_space + word_w
        if current:
            lines.append(current)
        visible_lines = max(1, config.BUBBLE_LINES)
        if not lines:
            visible = []
        elif self._revealed_count <= 0:
            visible = lines[max(0, len(lines) - visible_lines):]
        else:
            target_idx = min(self._revealed_count - 1, len(reply_words) - 1)
            target_line = 0
            for line_idx, line in enumerate(lines):
                if any(reply_idx == target_idx for _word, _bold, reply_idx, _is_thought in line):
                    target_line = line_idx
                    break
            start_line = max(0, target_line - visible_lines + 1)
            visible = lines[start_line:start_line + visible_lines]
        self._line_segments = visible
        self._lines = [" ".join(word for word, _bold, _idx, _is_thought in line) for line in visible]

    @staticmethod
    def _markdown_words(text: str) -> list[tuple[str, bool]]:
        words: list[tuple[str, bool]] = []
        bold = False
        buf = ""
        i = 0
        while i < len(text):
            if text.startswith("**", i) or text.startswith("__", i):
                if buf:
                    words.extend((part, bold) for part in buf.split())
                    buf = ""
                bold = not bold
                i += 2
                continue
            buf += text[i]
            i += 1
        if buf:
            words.extend((part, bold) for part in buf.split())
        return words

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

        p.setBrush(QBrush(self._bubble_color))
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
            p.setPen(QPen(self._text_color))
            y = _PAD
            if not self._line_segments and self._lines:
                self._line_segments = [[(line, False, None, False)] for line in self._lines]
            for line in self._line_segments:
                x = _PAD
                for idx, (word, bold, word_idx, is_thought) in enumerate(line):
                    if idx:
                        x += self._space_w
                    is_read = (not is_thought) and word_idx is not None and word_idx < self._revealed_count
                    font = self._bold_font if (bold and not is_thought) else self._font
                    fm = self._bold_fm if (bold and not is_thought) else self._fm
                    word_w = fm.horizontalAdvance(word)
                    p.setFont(font)
                    if is_thought:
                        pen = self._thought_color
                    else:
                        pen = self._read_word_color if is_read else self._text_color
                    p.setPen(QPen(pen))
                    p.drawText(x, y, self._bubble_w - x, self._line_h,
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                               word)
                    x += word_w
                p.setFont(self._font)
                y += self._line_h

        p.end()
