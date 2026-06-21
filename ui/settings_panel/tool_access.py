"""Per-caller "Allowed tools" dialog.

Each prompt method (caller hotkey, voice) chooses which model-callable tools
it exposes. Every tool gets an Off / On / Let-model-decide selector:

  Off              — never offered to the model for this caller
  On               — always offered
  Let model decide — offered when the prompt matches the tool's keywords
                     (Settings → Tools); tools without keywords are always
                     offered, same as On

Context tools (web search, document/page fetch, git/GitHub, memory search,
screenshot) default to following the caller's context dropdowns; changing one
here stores a per-tool override that wins over the dropdown for that tool
only. Automatic context gathering ("On" dropdowns: page/document/git text
injected up front) stays governed by the dropdowns.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QComboBox,
)

from core.system.env_utils import TOOL_OVERRIDE_MODES
from ui.i18n import t
from ui.shared.window_utils import enable_standard_window_controls

# Tool name → the context dropdown label(s) that govern it ("a / b" when two).
# Their selectors default to the dropdown-derived state instead of Off.
CONTEXT_GOVERNED_TOOLS: dict[str, str] = {
    "web_search":     "Browser/Web",
    "get_context":    "Open docs / Browser/Web",
    "retrieve_website": "Browser/Web",
    "git_status":     "Git/GitHub",
    "git_diff":       "Git/GitHub",
    "github_repo":    "Git/GitHub",
    "github_issue":   "Git/GitHub",
    "memory_search":  "Memory",
    "capture_screen": "Screenshot",
}

LOCAL_FILE_TOOL_ROWS: tuple[tuple[str, str], ...] = (
    ("list_files", "List configured file roots."),
    ("read_file", "Read files from configured file roots."),
    ("create_file", "Create new files in configured file roots."),
    ("edit_file", "Patch files in configured file roots."),
    ("write_file", "Create or overwrite files in configured file roots."),
)
LOCAL_FILE_TOOL_NAMES = {name for name, _note in LOCAL_FILE_TOOL_ROWS}

_MODE_LABELS = [("Off", "off"), ("On", "on"), ("Let model decide", "model")]
_MODE_DISPLAY = {"off": "Off", "auto": "On", "on": "On", "model": "Let model decide"}


def _mode_tooltip() -> str:
    """Handle mode tooltip for UI settings panel tool access."""
    return (
        f"{t('Off')} - {t('never offered to the model for this hotkey.')}\n"
        f"{t('On')} - {t('always offered.')}\n"
        + t("Let model decide")
        + " - "
        + t("offered when the prompt matches the tool's keywords (Settings > Tools).")
    )

_MODE_TOOLTIP = (
    "Off — never offered to the model for this hotkey.\n"
    "On — always offered.\n"
    "Let model decide — offered when the prompt matches the tool's keywords "
    "(Settings → Tools)."
)


def list_extra_tools() -> list:
    """Discovered tools that are NOT governed by a context dropdown.

    Refreshes the registry first so script tools installed since launch appear.
    """
    from core.llm_clients.client import get_tool_registry

    registry = get_tool_registry()
    try:
        registry.refresh()
    except Exception:
        pass
    return sorted(
        (
            s for s in registry.list_tools()
            if s.name not in CONTEXT_GOVERNED_TOOLS and s.name not in LOCAL_FILE_TOOL_NAMES
        ),
        key=lambda s: s.name,
    )


def _governed_default(governs: str, governed_modes: dict[str, str]) -> str:
    """Tool exposure implied by the governing dropdown(s): model when any of
    them is "Let model decide", otherwise off (auto = frontload, not a tool)."""
    for part in governs.split(" / "):
        if str(governed_modes.get(part, "")).strip().lower() == "model":
            return "model"
    return "off"


def _file_tool_default(name: str, file_mode: str) -> str:
    """Tool exposure implied by the caller's Local files dropdown."""
    mode = str(file_mode or "off").strip().lower()
    if mode == "read":
        return "on" if name in {"list_files", "read_file"} else "off"
    if mode in {"ask", "auto"}:
        return "on"
    return "off"


class ToolAccessDialog(QDialog):
    """Pick per-tool access (off/on/model) for one prompt method."""

    def __init__(
        self,
        parent=None,
        *,
        method_label: str = "this hotkey",
        overrides: dict[str, str] | None = None,
        governed_modes: dict[str, str] | None = None,
    ):
        """Initialize the tool access dialog instance."""
        super().__init__(parent)
        self.setWindowTitle(f"{t('Allowed tools')} — {t(method_label)}")
        self.setModal(True)
        self.setMinimumWidth(480)
        enable_standard_window_controls(self)
        # ~25% larger text than the rest of the settings dialog (10pt inputs,
        # ~9pt labels there). <small> notes scale down relative to this base.
        self.setStyleSheet(
            "QLabel { font-size: 11pt; } "
            "QComboBox { font-size: 12pt; } "
            "QPushButton { font-size: 12pt; }"
        )
        self._combos: dict[str, QComboBox] = {}
        # Per-tool default: what the selector means when the user has not
        # overridden it. selected_overrides() only stores deviations, so an
        # untouched context tool keeps following its dropdown.
        self._defaults: dict[str, str] = {}
        overrides = overrides or {}
        governed_modes = governed_modes or {}

        root = QVBoxLayout(self)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── Context tools (default: follow the dropdowns) ─────────────────
        ctx_hdr = QLabel(t("CONTEXT TOOLS"))
        ctx_hdr.setObjectName("sectionHeader")
        layout.addWidget(ctx_hdr)
        ctx_note = QLabel(
            f"<small>{t('These default to the context dropdowns on the hotkey - changing one here overrides the dropdown for that tool only. Automatic context (dropdowns set to On) is unaffected.')}</small>"
        )
        ctx_note.setWordWrap(True)
        layout.addWidget(ctx_note)
        for name, governs in CONTEXT_GOVERNED_TOOLS.items():
            default = _governed_default(governs, governed_modes)
            states = [
                f"{t(part)}: {t(_MODE_DISPLAY.get(str(governed_modes[part]).strip().lower(), governed_modes[part]))}"
                for part in governs.split(" / ")
                if part in governed_modes
            ]
            note = " · ".join(states) if states else t(governs)
            self._add_tool_row(layout, name, note, overrides.get(name, default), default)

        layout.addWidget(_separator())

        # ── Local file tools (default: follow Local files dropdown) ───────
        file_hdr = QLabel(t("LOCAL FILE TOOLS"))
        file_hdr.setObjectName("sectionHeader")
        layout.addWidget(file_hdr)
        file_mode = str(governed_modes.get("Files", "off") or "off").strip().lower()
        file_note = QLabel(
            f"<small>{t('These default to the Local files dropdown. Writes still follow the configured file roots and approval mode.')}</small>"
        )
        file_note.setWordWrap(True)
        layout.addWidget(file_note)
        for name, note in LOCAL_FILE_TOOL_ROWS:
            default = _file_tool_default(name, file_mode)
            self._add_tool_row(layout, name, t(note), overrides.get(name, default), default)

        layout.addWidget(_separator())

        # ── Installed + addon tools (default: off) ───────────────────────
        extra_hdr = QLabel(t("INSTALLED + PLUGIN TOOLS"))
        extra_hdr.setObjectName("sectionHeader")
        layout.addWidget(extra_hdr)

        extra = list_extra_tools()
        if not extra:
            empty = QLabel(
                f"<small>{t('No extra tools found. Enable addons that add model tools.')}</small>"
            )
            empty.setWordWrap(True)
            layout.addWidget(empty)
        for spec in extra:
            desc = (spec.description or "").strip()[:160]
            self._add_tool_row(layout, spec.name, desc, overrides.get(spec.name, "off"), "off")

        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("Cancel"))
        ok_btn = QPushButton(t("OK"))
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)
        self.resize(560, 540)

    def _add_tool_row(
        self,
        layout: QVBoxLayout,
        name: str,
        note: str,
        mode: str,
        default: str,
    ) -> None:
        """Add tool row."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        text_col = QWidget()
        tv = QVBoxLayout(text_col)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(1)
        tv.addWidget(QLabel(f"<b>{t(name)}</b>"))
        if note:
            note_lbl = QLabel(f"<small>{note}</small>")
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet("color: palette(placeholder-text);")
            tv.addWidget(note_lbl)
        combo = QComboBox()
        for label, data in _MODE_LABELS:
            combo.addItem(t(label), data)
        mode = str(mode or "off").strip().lower()
        idx = combo.findData(mode if mode in TOOL_OVERRIDE_MODES else "off")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.setToolTip(_mode_tooltip())
        self._combos[name] = combo
        self._defaults[name] = default
        h.addWidget(text_col, 1)
        h.addWidget(combo)
        layout.addWidget(row)

    def selected_overrides(self) -> dict[str, str]:
        """Per-tool modes that deviate from each tool's default.

        Context tools left matching their dropdown-derived state store nothing
        (they keep following the dropdown); installed/addon tools left Off
        store nothing. Everything else round-trips via format_tool_modes().
        """
        result: dict[str, str] = {}
        for name, combo in self._combos.items():
            mode = str(combo.currentData() or "off")
            if mode in TOOL_OVERRIDE_MODES and mode != self._defaults.get(name, "off"):
                result[name] = mode
        return result


def _separator() -> QFrame:
    """Handle separator for UI settings panel tool access."""
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("max-height: 1px; background: #55555f; margin: 4px 0;")
    return sep
