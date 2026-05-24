"""Compatibility alias for ui.settings_panel.hotkey_capture."""
import sys
from ui.settings_panel import hotkey_capture as _hotkey_capture

sys.modules[__name__] = _hotkey_capture
