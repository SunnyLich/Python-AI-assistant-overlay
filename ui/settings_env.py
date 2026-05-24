"""Compatibility alias for ui.settings_panel.env."""
import sys
from ui.settings_panel import env as _env

sys.modules[__name__] = _env
