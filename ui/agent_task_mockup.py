"""Compatibility alias for ui.agent.task_window."""
import sys
from ui.agent import task_window as _task_window

sys.modules[__name__] = _task_window
