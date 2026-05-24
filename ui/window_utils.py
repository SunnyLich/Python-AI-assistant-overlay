"""Compatibility alias for ui.shared.window_utils."""
import sys
from ui.shared import window_utils as _window_utils

sys.modules[__name__] = _window_utils
