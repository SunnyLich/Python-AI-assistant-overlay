"""Compatibility alias for ui.settings_panel.dialog."""
import sys
from ui.settings_panel import dialog as _dialog

sys.modules[__name__] = _dialog
