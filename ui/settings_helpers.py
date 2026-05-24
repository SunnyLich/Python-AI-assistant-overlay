"""Compatibility alias for ui.settings_panel.helpers."""
import sys
from ui.settings_panel import helpers as _helpers

sys.modules[__name__] = _helpers
