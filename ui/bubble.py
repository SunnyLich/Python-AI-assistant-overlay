"""
ui/bubble.py — Live speech bubble next to the icon.

Shows the last 2 word-wrapped lines of streaming LLM text.
Positioned to the left of the icon, tail points right toward it.
Auto-hides a few seconds after the response finishes.
"""
from __future__ import annotations
from collections.abc import Callable
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QRect
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics,
    QBrush, QPen, QPainterPath,
)
import config
from ui.i18n import t

_DOTS_COLOR   = QColor(140, 140, 165)
_PAD          = 12
_LINE_GAP     = 5
_TAIL_W       = 12
_TAIL_H       = 14
_RADIUS       = 10
_DEFAULT_FONT_SIZE = 10
_ICON_W       = 80
_ICON_H       = 80
_ICON_MARGIN  = 20
_HIDE_DELAY    = 3_500   # fallback ms after finish() before hiding
_CLOSE_SIZE    = 18
_CLOSE_MARGIN  = 6
_ACTION_H      = 26
_ACTION_GAP    = 8
_ACTION_ROW_H  = 36


def _color(value: str, fallback: QColor) -> QColor:
    """Handle color for UI bubble."""
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
    """Compact always-on-top widget that streams LLM text next to the icon."""

    def __init__(self):
        """Initialize the speech bubble instance."""
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self._font = QFont("Segoe UI", _DEFAULT_FONT_SIZE)
        self._bold_font = QFont("Segoe UI", _DEFAULT_FONT_SIZE)
        self._fm = QFontMetrics(self._font)
        self._bold_fm = QFontMetrics(self._bold_font)
        self._space_w = 0
        self._line_h = 0
        self._apply_font()
        self._bubble_color = _color(config.BUBBLE_COLOR, QColor(28, 28, 36, 220))
        self._text_color = _color(config.BUBBLE_TEXT_COLOR, QColor(230, 230, 230))
        self._read_word_color = _color(config.BUBBLE_READ_WORD_COLOR, QColor(77, 163, 255))
        self._thought_color = QColor(150, 150, 165)

        self._full_text = ""
        self._thought_text = ""
        self._lines: list[str] = []
        self._all_line_segments: list[list[tuple[str, bool, int | None, bool, bool]]] = []
        self._line_segments: list[list[tuple[str, bool, int | None, bool, bool]]] = []
        self._thinking = False
        self._transcript_preview = False
        self._dot_count = 1
        self._last_chunk_ended_with_space = True  # guards mid-word chunk merging
        self._manual_scroll_start: int | None = None
        self._reply_chunk_count = 0

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
        self._text_w = self._bubble_w - _PAD * 2 - _CLOSE_SIZE
        self._bubble_h = _PAD * 2 + self._line_h * config.BUBBLE_LINES - _LINE_GAP
        self.setFixedSize(self._bubble_w + _TAIL_W, self._bubble_h)

        # Position: left of icon, vertically centered with it
        icon_sz = config.ICON_SIZE
        icon_x = screen.x() + screen.width()  - icon_sz - _ICON_MARGIN
        icon_y = screen.y() + screen.height() - icon_sz - _ICON_MARGIN
        bx = icon_x - self._bubble_w - _TAIL_W - 6
        by = icon_y + (config.ICON_SIZE - self._bubble_h) // 2
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

        # Manual wheel scrolling while speech is active; snaps back to the
        # current highlight after a short configurable delay.
        self._scroll_snap_timer = QTimer(self)
        self._scroll_snap_timer.setSingleShot(True)
        self._scroll_snap_timer.timeout.connect(self._snap_scroll_to_highlight)

        # Drag support
        self._drag_offset = None          # QPoint while dragging
        self._press_pos = None            # global press position (click vs drag)
        self._press_timer = QElapsedTimer()  # measures press duration (click vs hold-to-speed)
        self._dragged = False             # True once the press moved past the click threshold
        self._close_hover = False
        self._close_pressed = False
        self._notice_actions: list[tuple[str, Callable[[], None]]] = []
        self._action_rects: list[QRect] = []
        self._action_hover = -1
        self._action_pressed = -1
        self._companion_callback = None   # called with new QPoint after each drag move
        self._hide_callback = None        # called when this widget hides (for icon sync)
        self._speed_callback = None       # called with True while hold-to-speed is active
        self._click_callback = None       # called on a click (no drag) — opens the chat window
        self._highlight_callback = None   # called(reply_text, revealed_count, finished)
        self._stop_callback = None        # called when the user clicks the close/stop affordance

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

    def set_click_callback(self, fn):
        """Register a zero-argument callback fired on a click (press+release without drag)."""
        self._click_callback = fn

    def set_highlight_callback(self, fn):
        """Register callback(reply_text, revealed_count, finished) reporting read-position.

        Fires whenever the TTS-synced highlight advances so other surfaces (e.g. the
        chat window) can mirror the same read position.
        """
        self._highlight_callback = fn

    def set_stop_callback(self, fn):
        """Register callback fired when the user stops the visible bubble reply."""
        self._stop_callback = fn

    def apply_config(self):
        """Apply live bubble size/line/speed settings after config.reload()."""
        self._apply_font()
        self._bubble_w = config.BUBBLE_WIDTH
        self._text_w = self._bubble_w - _PAD * 2 - _CLOSE_SIZE
        self._bubble_h = _PAD * 2 + self._line_h * config.BUBBLE_LINES - _LINE_GAP
        self.setFixedSize(self._bubble_w + _TAIL_W, self._bubble_h)
        self._bubble_color = _color(config.BUBBLE_COLOR, QColor(28, 28, 36, 220))
        self._text_color = _color(config.BUBBLE_TEXT_COLOR, QColor(230, 230, 230))
        self._read_word_color = _color(config.BUBBLE_READ_WORD_COLOR, QColor(77, 163, 255))
        self._hide_timer.setInterval(self._hide_delay_ms())
        self._apply_reveal_speed()
        if not self._bubble_scroll_enabled():
            self._manual_scroll_start = None
            self._scroll_snap_timer.stop()
        if self._full_text:
            self._rewrap()
        self.update()

    def _apply_font(self) -> None:
        """Refresh fonts and metrics from the current bubble text-size setting."""
        try:
            raw_size = int(getattr(config, "BUBBLE_FONT_SIZE", _DEFAULT_FONT_SIZE))
        except (TypeError, ValueError):
            raw_size = _DEFAULT_FONT_SIZE
        size = max(6, min(raw_size, 32))
        self._font = QFont("Segoe UI", size)
        self._bold_font = QFont("Segoe UI", size)
        self._bold_font.setBold(True)
        self._fm = QFontMetrics(self._font)
        self._bold_fm = QFontMetrics(self._bold_font)
        self._space_w = self._fm.horizontalAdvance(" ")
        self._line_h = self._fm.height() + _LINE_GAP

    def hideEvent(self, event):  # noqa: N802
        """Hide event."""
        super().hideEvent(event)
        if self._hide_callback:
            self._hide_callback()

    def icon_pos_for_bubble(self, bubble_pos, icon_size: int):
        """Given this bubble's top-left position, return where the icon should sit."""
        from PySide6.QtCore import QPoint
        icon_x = bubble_pos.x() + self._bubble_w + _TAIL_W + 6
        icon_y = bubble_pos.y() - (icon_size - self._bubble_h) // 2
        return QPoint(icon_x, icon_y)

    def mousePressEvent(self, event):
        """Handle mouse press event for speech bubble."""
        if event.button() == Qt.MouseButton.LeftButton:
            action_idx = self._action_index_at(event.position().toPoint())
            if action_idx >= 0:
                self._action_pressed = action_idx
                event.accept()
                self.update()
                return
            if self._close_rect().contains(event.position().toPoint()):
                self._close_pressed = True
                event.accept()
                self.update()
                return
            self._set_speed_boost(True)
            self._press_pos = event.globalPosition().toPoint()
            self._press_timer.restart()
            self._dragged = False
            self._drag_offset = self.pos() - self._press_pos
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move event for speech bubble."""
        action_hover = self._action_index_at(event.position().toPoint())
        if action_hover != self._action_hover:
            self._action_hover = action_hover
            self.update()
        hovering_close = self._close_rect().contains(event.position().toPoint())
        if hovering_close != self._close_hover:
            self._close_hover = hovering_close
            self.update()
        if self._close_pressed:
            event.accept()
            return
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint()
            if (self._press_pos is not None
                    and (point - self._press_pos).manhattanLength() > 6):
                self._dragged = True
            new_pos = point + self._drag_offset
            self.move(new_pos)
            if self._companion_callback:
                self._companion_callback(new_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release event for speech bubble."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._action_pressed >= 0:
                action_idx = self._action_pressed
                should_trigger = action_idx == self._action_index_at(event.position().toPoint())
                self._action_pressed = -1
                if should_trigger and action_idx < len(self._notice_actions):
                    callback = self._notice_actions[action_idx][1]
                    self.clear()
                    callback()
                event.accept()
                self.update()
                return
            if self._close_pressed:
                should_stop = self._close_rect().contains(event.position().toPoint())
                self._close_pressed = False
                if should_stop:
                    if self._stop_callback:
                        self._stop_callback()
                    self.clear()
                event.accept()
                self.update()
                return
            self._set_speed_boost(False)
            # A click = pressed, didn't drag, and released quickly. A longer press
            # is the hold-to-speed gesture and must not open the chat window.
            was_click = (
                self._drag_offset is not None
                and not self._dragged
                and self._press_timer.elapsed() < 350
            )
            self._drag_offset = None
            self._press_pos = None
            if was_click and self._click_callback:
                self._click_callback()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        """Clear hover state when the pointer leaves the bubble."""
        self._close_hover = False
        self._action_hover = -1
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        """Scroll the bubble text without requiring focus."""
        if not self._bubble_scroll_enabled() or not self._all_line_segments:
            super().wheelEvent(event)
            return

        visible_lines = self._visible_line_count()
        max_start = max(0, len(self._all_line_segments) - visible_lines)
        if max_start <= 0:
            event.accept()
            return

        delta_y = event.angleDelta().y()
        if delta_y == 0:
            delta_y = event.pixelDelta().y()
        if delta_y == 0:
            event.accept()
            return

        steps = max(1, abs(delta_y) // 120) if abs(delta_y) >= 120 else 1
        current = self._manual_scroll_start
        if current is None:
            current = self._highlight_start_line(self._all_line_segments)
        if delta_y > 0:
            current -= steps
        else:
            current += steps
        self._manual_scroll_start = max(0, min(max_start, current))
        self._apply_visible_lines()
        self._schedule_scroll_snap()
        event.accept()

    def show_listening(self, text: str | None = None):
        """Show a static status indicator while the app waits for user input."""
        message = str(text or "Recording - release to send").strip()
        self._full_text = ""
        self._thought_text = ""
        self._highlight_generation += 1
        self._lines = [f"\u25cf {message}"]
        self._all_line_segments = []
        self._line_segments = [[(message, False, None, False, False)]]
        self._thinking = False
        self._transcript_preview = False
        self._pending_words = []
        self._revealed_count = 0
        self._manual_scroll_start = None
        self._reveal_mode = False
        self._audio_started = False
        self._pre_audio_timestamps = []
        self._scroll_snap_timer.stop()
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
        self._all_line_segments = []
        self._line_segments = []
        self._thinking = True
        self._transcript_preview = False
        self._dot_count = 1
        self._last_chunk_ended_with_space = True
        self._pending_words = []
        self._revealed_count = 0
        self._manual_scroll_start = None
        self._finishing = False
        self._reveal_mode = False
        self._audio_started = False
        self._pre_audio_timestamps = []
        self._scroll_snap_timer.stop()
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
        first_audio_start = not self._audio_started
        self._audio_started = True
        self._highlight_generation += 1
        self._audio_elapsed.start()   # t=0 for timestamp scheduling
        if first_audio_start and not self._timestamp_mode:
            # The text may have streamed before audio was audible. Re-anchor the
            # fallback reveal to playback start so it does not run ahead of TTS.
            self._reveal_mode = True
            self._timestamp_mode = False
            self._revealed_count = 0
            self._full_text = self._join_reveal_units(self._pending_words)
            self._lines = []
            self._all_line_segments = []
            self._line_segments = []
            self._apply_reveal_speed()
            self._reveal_timer.start()
        elif not self._reveal_mode:
            self._reveal_mode = True
            self._timestamp_mode = False
            self._apply_reveal_speed()
            self._reveal_timer.start()
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
        schedule = list(zip(words, start_ms))
        for i, (word, t_ms) in enumerate(schedule):
            units = self._reveal_units(str(word))
            if not units:
                continue
            base_delay = max(0, int(t_ms / playback_rate - elapsed))
            if i + 1 < len(schedule):
                next_delay = max(0, int(schedule[i + 1][1] / playback_rate - elapsed))
            else:
                next_delay = base_delay + max(220, 80 * len(units))
            step_ms = max(45, min(160, int((next_delay - base_delay) / max(1, len(units)))))
            for unit_idx, unit in enumerate(units):
                delay = base_delay + unit_idx * step_ms
                QTimer.singleShot(delay, lambda w=unit, g=generation: self._advance_highlight(w, g))

    def _advance_highlight(self, word: str | None = None, generation: int | None = None):
        """Handle advance highlight for speech bubble."""
        if generation is not None and generation != self._highlight_generation:
            return
        if word and self._revealed_count >= len(self._pending_words):
            self._pending_words.extend(self._reveal_units(word))
            self._full_text = self._join_reveal_units(self._pending_words)
        if self._revealed_count < len(self._pending_words):
            self._revealed_count += 1
        self._rewrap()
        self.update()
        # If finish() is waiting on the reveal to drain (WPM or timestamp mode),
        # start the hide countdown only once the final word has been highlighted.
        if self._finishing and self._revealed_count >= len(self._pending_words):
            self._reveal_timer.stop()
            self._finishing = False
            self._reveal_mode = False
            self._timestamp_mode = False
            self._start_hide_timer()
            self._emit_highlight(finished=True)
        else:
            self._emit_highlight(finished=False)

    def append_chunk(self, chunk: str, is_thought: bool = False):
        """Buffer incoming LLM chunk. Starts WPM reveal on first token if not already active."""
        if not chunk:
            return
        if self._transcript_preview:
            self._transcript_preview = False
            self._pending_words = []
            self._full_text = ""
            self._revealed_count = 0
            self._manual_scroll_start = None
            self._reply_chunk_count = 0
            self._lines = []
            self._all_line_segments = []
            self._line_segments = []
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
        self._reply_chunk_count += 1
        new_words = self._reveal_units(chunk)
        if new_words:
            # If this chunk starts mid-word (no leading space) and the previous
            # chunk also ended mid-word (no trailing space), merge into last word.
            if (self._pending_words
                    and not chunk[0].isspace()
                    and not self._last_chunk_ended_with_space
                    and not self._is_cjk(self._pending_words[-1][-1])
                    and not self._is_cjk(new_words[0][0])):
                self._pending_words[-1] += new_words[0]
                new_words = new_words[1:]
            self._pending_words.extend(new_words)
        self._full_text += chunk
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

    def finish(self, *, flush_remaining: bool = False):
        """Called when TTS playback finishes; reveals remaining words then hides."""
        self._dot_timer.stop()
        if flush_remaining:
            self._revealed_count = len(self._pending_words)
            self._rewrap()
            self.update()
            self._reveal_timer.stop()
            self._finishing = False
            self._reveal_mode = False
            self._timestamp_mode = False
            self._start_hide_timer()
            self._emit_highlight(finished=True)
            return
        if self._timestamp_mode:
            # Timestamp mode: words are highlighted by per-word QTimer.singleShot
            # callbacks that may still be pending when playback reports done. Hold
            # the bubble (and the icon, hidden in lockstep) until the last word has
            # actually been highlighted, then start the hide countdown — otherwise
            # a fixed countdown could fire mid-highlight and the icon would vanish
            # before the spoken text finished.
            self._reveal_timer.stop()
            if self._revealed_count < len(self._pending_words):
                self._finishing = True   # _advance_highlight starts the timer when drained
            else:
                self._reveal_mode = False
                self._timestamp_mode = False
                self._start_hide_timer()
                self._emit_highlight(finished=True)
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
            self._start_hide_timer()
            self._emit_highlight(finished=True)

    def clear(self):
        """Hard reset — hide immediately."""
        self._hide_timer.stop()
        self._dot_timer.stop()
        self._reveal_timer.stop()
        self._scroll_snap_timer.stop()
        self._reveal_mode = False
        self._finishing = False
        self._timestamp_mode = False
        self._audio_started = False
        self._last_chunk_ended_with_space = True
        self._reply_chunk_count = 0
        self._speed_boosting = False
        self._highlight_generation += 1
        self._pending_words = []
        self._revealed_count = 0
        self._manual_scroll_start = None
        self._pre_audio_timestamps = []
        self._thinking = False
        self._transcript_preview = False
        self._full_text = ""
        self._thought_text = ""
        self._lines = []
        self._all_line_segments = []
        self._line_segments = []
        self._clear_notice_actions()
        self._restore_base_size()
        self.hide()

    def show_notice(
        self,
        text: str,
        *,
        timeout_ms: int = 12000,
        actions: list[tuple[str, Callable[[], None]]] | None = None,
    ):
        """Show a compact non-streaming notice next to the icon."""
        self._show_static_text(text, timeout_ms=timeout_ms, actions=actions)

    def show_transcript(self, text: str):
        """Show what push-to-talk transcription heard before the answer starts."""
        text = (text or "").strip()
        if not text:
            return
        self._show_static_text(f"{t('Heard')}: {text}", timeout_ms=0)
        self._transcript_preview = True

    def _show_static_text(
        self,
        text: str,
        *,
        timeout_ms: int = 12000,
        actions: list[tuple[str, Callable[[], None]]] | None = None,
    ):
        """Show static text."""
        self._hide_timer.stop()
        self._dot_timer.stop()
        self._reveal_timer.stop()
        self._scroll_snap_timer.stop()
        self._reveal_mode = False
        self._finishing = False
        self._timestamp_mode = False
        self._audio_started = False
        self._thinking = False
        self._transcript_preview = False
        self._manual_scroll_start = None
        self._reply_chunk_count = 0
        self._thought_text = ""
        self._notice_actions = list(actions or [])
        self._action_hover = -1
        self._action_pressed = -1
        if self._notice_actions:
            self._set_notice_action_size()
        else:
            self._restore_base_size()
        self._pending_words = self._reveal_units(text)
        self._revealed_count = len(self._pending_words)
        self._full_text = self._join_reveal_units(self._pending_words)
        self._layout_action_buttons()
        self._rewrap()
        self.show()
        self.raise_()
        self.update()
        if timeout_ms > 0:
            self._start_hide_timer(timeout_ms)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tick_dots(self):
        """Handle tick dots for speech bubble."""
        self._dot_count = (self._dot_count % 3) + 1
        self.update()

    def _emit_highlight(self, finished: bool = False):
        """Emit highlight."""
        if self._highlight_callback:
            self._highlight_callback(self._full_text, self._revealed_count, finished)

    def _close_rect(self) -> QRect:
        """Return the top-right close/stop hit target inside the bubble body."""
        return QRect(
            self._bubble_w - _CLOSE_MARGIN - _CLOSE_SIZE,
            _CLOSE_MARGIN,
            _CLOSE_SIZE,
            _CLOSE_SIZE,
        )

    def _action_index_at(self, point) -> int:
        """Return the notice action index under point, or -1."""
        for idx, rect in enumerate(self._action_rects):
            if rect.contains(point):
                return idx
        return -1

    def _clear_notice_actions(self) -> None:
        """Clear action buttons from a static notice."""
        self._notice_actions = []
        self._action_rects = []
        self._action_hover = -1
        self._action_pressed = -1

    def _base_bubble_h(self) -> int:
        """Return the configured bubble body height without notice actions."""
        return _PAD * 2 + self._line_h * config.BUBBLE_LINES - _LINE_GAP

    def _restore_base_size(self) -> None:
        """Restore normal speech-bubble dimensions."""
        self._bubble_h = self._base_bubble_h()
        self.setFixedSize(self._bubble_w + _TAIL_W, self._bubble_h)

    def _set_notice_action_size(self) -> None:
        """Make room for notice action buttons."""
        self._bubble_h = self._base_bubble_h() + _ACTION_ROW_H
        self.setFixedSize(self._bubble_w + _TAIL_W, self._bubble_h)

    def _layout_action_buttons(self) -> None:
        """Lay out static notice action buttons."""
        self._action_rects = []
        if not self._notice_actions:
            return
        labels = [label for label, _callback in self._notice_actions]
        widths = [max(70, self._fm.horizontalAdvance(label) + 24) for label in labels]
        total_w = sum(widths) + _ACTION_GAP * max(0, len(widths) - 1)
        x = max(_PAD, self._bubble_w - _PAD - total_w)
        y = self._bubble_h - _PAD - _ACTION_H
        for width in widths:
            self._action_rects.append(QRect(x, y, width, _ACTION_H))
            x += width + _ACTION_GAP

    def _current_reveal_wpm(self) -> int:
        """Handle current reveal wpm for speech bubble."""
        if self._speed_boosting:
            return max(1, int(getattr(config, "BUBBLE_HOLD_REVEAL_WPM", 480)))
        return max(1, int(getattr(config, "BUBBLE_REVEAL_WPM", 170)))

    @staticmethod
    def _hide_delay_ms() -> int:
        """Hide delay ms."""
        return max(500, int(getattr(config, "BUBBLE_HIDE_DELAY_MS", _HIDE_DELAY)))

    @staticmethod
    def _bubble_scroll_enabled() -> bool:
        """Whether wheel scrolling inside the bubble is enabled."""
        return bool(getattr(config, "BUBBLE_SCROLL_ENABLED", True))

    @staticmethod
    def _bubble_scroll_snap_enabled() -> bool:
        """Whether manual bubble scroll snaps back to the live highlight."""
        return bool(getattr(config, "BUBBLE_SCROLL_SNAP_ENABLED", True))

    @staticmethod
    def _bubble_scroll_snap_delay_ms() -> int:
        """Delay before snapping manual scroll back to the current highlight."""
        return max(0, int(getattr(config, "BUBBLE_SCROLL_SNAP_DELAY_MS", 2500)))

    def _current_tts_rate(self) -> float:
        """Handle current TTS rate for speech bubble."""
        if self._speed_boosting:
            rate = getattr(config, "TTS_HOLD_PLAYBACK_RATE", 1.35)
        else:
            rate = getattr(config, "TTS_PLAYBACK_RATE", 1.0)
        return max(0.25, min(4.0, float(rate)))

    def _apply_reveal_speed(self):
        """Apply reveal speed."""
        self._reveal_timer.setInterval(max(1, int(60_000 / self._current_reveal_wpm())))

    def _start_hide_timer(self, delay_ms: int | None = None) -> None:
        """Start hide timer."""
        self._hide_timer.setInterval(
            max(1, int(self._hide_delay_ms() if delay_ms is None else delay_ms))
        )
        self._hide_timer.start()

    def _set_speed_boost(self, enabled: bool):
        """Set speed boost."""
        if self._speed_boosting == enabled:
            return
        self._speed_boosting = enabled
        self._apply_reveal_speed()
        if self._speed_callback:
            self._speed_callback(enabled)

    def _reveal_next_word(self):
        # WPM fallback tick. _advance_highlight() also starts the hide countdown
        # once the reveal drains while _finishing, so we don't duplicate it here.
        """Handle reveal next word for speech bubble."""
        if self._revealed_count < len(self._pending_words):
            self._advance_highlight()
        # Timer keeps running until finish() is called (which sets _finishing)

    def _rewrap(self):
        """Word-wrap _full_text and update the visible window."""
        thought_words = self._markdown_words(self._thought_text)
        reply_words = self._markdown_words(self._full_text)
        # Expand each whitespace word into breakable units. CJK text has no
        # spaces, so each CJK character is its own unit (Latin runs stay whole),
        # letting the wrap break mid-"word". space_before marks the first unit of
        # a source word so spacing and the read-highlight index stay correct.
        words: list[tuple[str, bool, int | None, bool, bool]] = []
        for word, bold in thought_words:
            for u_i, unit in enumerate(self._wrap_units(word)):
                words.append((unit, bold, None, True, u_i == 0))
        reply_idx = 0
        for word, bold in reply_words:
            reveal_units = self._reveal_units(word) or [word]
            for reveal_i, reveal_unit in enumerate(reveal_units):
                for u_i, unit in enumerate(self._wrap_units(reveal_unit)):
                    words.append((unit, bold, reply_idx, False, reveal_i == 0 and u_i == 0))
                reply_idx += 1
        lines: list[list[tuple[str, bool, int | None, bool, bool]]] = []
        current: list[tuple[str, bool, int | None, bool, bool]] = []
        current_w = 0
        prev_is_thought: bool | None = None
        for word, bold, reply_idx, is_thought, space_before in words:
            fm = self._bold_fm if bold else self._fm
            word_w = fm.horizontalAdvance(word)
            # Break onto a new line where the model's thinking ends and the
            # reply begins, so they're separated by a line, not just colour.
            force_break = bool(current) and prev_is_thought is True and not is_thought
            extra_space = self._space_w if (current and space_before) else 0
            if force_break or (current and current_w + extra_space + word_w > self._text_w):
                lines.append(current)
                current = [(word, bold, reply_idx, is_thought, space_before)]
                current_w = word_w
            else:
                current.append((word, bold, reply_idx, is_thought, space_before))
                current_w += extra_space + word_w
            prev_is_thought = is_thought
        if current:
            lines.append(current)
        self._all_line_segments = lines
        self._apply_visible_lines()

    def _visible_line_count(self) -> int:
        """Return the configured number of visible bubble lines."""
        return max(1, int(getattr(config, "BUBBLE_LINES", 1)))

    def _highlight_start_line(self, lines: list[list[tuple[str, bool, int | None, bool, bool]]]) -> int:
        """Return the line window start that follows the last highlighted word."""
        visible_lines = self._visible_line_count()
        if not lines:
            return 0
        if self._revealed_count <= 0:
            return 0

        reply_indexes = [
            reply_idx
            for line in lines
            for _word, _bold, reply_idx, is_thought, _sb in line
            if not is_thought and reply_idx is not None
        ]
        if not reply_indexes:
            return max(0, len(lines) - visible_lines)

        target_idx = min(self._revealed_count - 1, max(reply_indexes))
        target_line = 0
        for line_idx, line in enumerate(lines):
            if any(reply_idx == target_idx for _word, _bold, reply_idx, _is_thought, _sb in line):
                target_line = line_idx
                break
        return max(0, target_line - visible_lines + 1)

    def _apply_visible_lines(self) -> None:
        """Apply either manual scroll or highlight-follow to visible lines."""
        visible_lines = self._visible_line_count()
        lines = self._all_line_segments
        if not lines:
            visible: list[list[tuple[str, bool, int | None, bool, bool]]] = []
        else:
            max_start = max(0, len(lines) - visible_lines)
            if self._manual_scroll_start is None or not self._bubble_scroll_enabled():
                if not self._bubble_scroll_enabled():
                    self._manual_scroll_start = None
                if self._should_follow_latest_stream_text(lines):
                    start_line = max_start
                else:
                    start_line = self._highlight_start_line(lines)
            else:
                start_line = max(0, min(max_start, self._manual_scroll_start))
                self._manual_scroll_start = start_line
            visible = lines[start_line:start_line + visible_lines]
        self._line_segments = visible
        self._lines = [self._join_units(line) for line in visible]

    def _should_follow_latest_stream_text(self, lines: list) -> bool:
        """Follow streamed text only until a read-position highlight exists."""
        if (
            self._audio_started
            or self._timestamp_mode
            or self._revealed_count > 0
            or self._manual_scroll_start is not None
        ):
            return False
        if self._reply_chunk_count <= 1:
            return False
        return len(lines) > self._visible_line_count()

    def _schedule_scroll_snap(self) -> None:
        """Schedule snap-back after manual wheel scrolling."""
        self._scroll_snap_timer.stop()
        if not self._bubble_scroll_snap_enabled() or not self._is_speaking():
            return
        delay_ms = self._bubble_scroll_snap_delay_ms()
        if delay_ms <= 0:
            self._snap_scroll_to_highlight()
        else:
            self._scroll_snap_timer.start(delay_ms)

    def _snap_scroll_to_highlight(self) -> None:
        """Return the visible window to the current highlighted word."""
        if not self._is_speaking():
            return
        self._manual_scroll_start = None
        self._apply_visible_lines()
        self.update()

    def _is_speaking(self) -> bool:
        """True while the bubble is still advancing spoken/read highlights."""
        if self._thinking or self._transcript_preview:
            return False
        if self._revealed_count < len(self._pending_words):
            return True
        return self._finishing or self._reveal_mode or self._timestamp_mode

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        """True for characters that aren't separated by spaces (CJK / kana /
        fullwidth), so the wrapper can break between them."""
        if not ch:
            return False
        o = ord(ch[0])
        return (
            0x3000 <= o <= 0x303F   # CJK symbols & punctuation
            or 0x3040 <= o <= 0x30FF  # hiragana / katakana
            or 0x3400 <= o <= 0x4DBF  # CJK ext. A
            or 0x4E00 <= o <= 0x9FFF  # CJK unified ideographs
            or 0xF900 <= o <= 0xFAFF  # CJK compat ideographs
            or 0xFF00 <= o <= 0xFFEF  # fullwidth / halfwidth forms
        )

    @classmethod
    def _wrap_units(cls, word: str) -> list[str]:
        """Split a whitespace-delimited word into breakable units: each CJK
        character on its own, maximal Latin runs kept whole."""
        units: list[str] = []
        run = ""
        for ch in word:
            if cls._is_cjk(ch):
                if run:
                    units.extend(cls._latin_wrap_units(run))
                    run = ""
                units.append(ch)
            elif ch in "\\/":
                run += ch
                units.extend(cls._latin_wrap_units(run))
                run = ""
            else:
                run += ch
        if run:
            units.extend(cls._latin_wrap_units(run))
        return units or [word]

    @classmethod
    def _reveal_units(cls, text: str) -> list[str]:
        """Split stream text into units that should advance one highlight tick."""
        units: list[str] = []
        run = ""
        for ch in text:
            if ch.isspace():
                if run:
                    units.append(run)
                    run = ""
                continue
            if cls._is_cjk(ch):
                if run:
                    units.append(run)
                    run = ""
                units.append(ch)
            else:
                run += ch
        if run:
            units.append(run)
        return units

    @classmethod
    def _join_reveal_units(cls, units: list[str]) -> str:
        """Join reveal units without adding spaces inside CJK text."""
        out = ""
        prev = ""
        for unit in units:
            if not unit:
                continue
            if out and cls._space_between_reveal_units(prev, unit):
                out += " "
            out += unit
            prev = unit
        return out

    @classmethod
    def _space_between_reveal_units(cls, left: str, right: str) -> bool:
        if not left or not right:
            return False
        return not cls._is_cjk(left[-1]) and not cls._is_cjk(right[0])

    @staticmethod
    def _latin_wrap_units(text: str) -> list[str]:
        """Keep ordinary words intact, but make long paths breakable."""
        if len(text) <= 28:
            return [text]
        return [text[i:i + 28] for i in range(0, len(text), 28)]

    @staticmethod
    def _join_units(line) -> str:
        """Render a wrapped line back to text, inserting a space only where a
        source-word boundary was (never between CJK characters)."""
        out = ""
        for i, (word, _bold, _idx, _is_thought, space_before) in enumerate(line):
            if i and space_before:
                out += " "
            out += word
        return out

    @staticmethod
    def _markdown_words(text: str) -> list[tuple[str, bool]]:
        """Handle markdown words for speech bubble."""
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
        """Paint event."""
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

        close_rect = self._close_rect()
        if self._close_hover or self._close_pressed:
            hover_color = QColor(255, 255, 255, 24 if not self._close_pressed else 40)
            p.setBrush(QBrush(hover_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(close_rect, 6, 6)
        close_pen = QColor(205, 205, 220, 220 if self._close_hover else 150)
        p.setPen(QPen(close_pen, 1.7))
        pad = 5
        p.drawLine(
            close_rect.left() + pad,
            close_rect.top() + pad,
            close_rect.right() - pad,
            close_rect.bottom() - pad,
        )
        p.drawLine(
            close_rect.right() - pad,
            close_rect.top() + pad,
            close_rect.left() + pad,
            close_rect.bottom() - pad,
        )

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
                self._line_segments = [[(line, False, None, False, False)] for line in self._lines]
            for line in self._line_segments:
                x = _PAD
                for idx, (word, bold, word_idx, is_thought, space_before) in enumerate(line):
                    if idx and space_before:
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

            if self._notice_actions:
                p.setFont(self._font)
                for idx, (label, _callback) in enumerate(self._notice_actions):
                    if idx >= len(self._action_rects):
                        continue
                    rect = self._action_rects[idx]
                    pressed = idx == self._action_pressed
                    hovered = idx == self._action_hover
                    bg = QColor(77, 163, 255, 230 if not pressed else 255)
                    if hovered and not pressed:
                        bg = QColor(99, 179, 255, 240)
                    p.setBrush(QBrush(bg))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(rect, 6, 6)
                    p.setPen(QPen(QColor(255, 255, 255)))
                    p.drawText(
                        rect,
                        Qt.AlignmentFlag.AlignCenter,
                        label,
                    )

        p.end()
