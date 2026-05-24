"""Compatibility alias for core.agent.task_spec."""
import sys
from core.agent import task_spec as _task_spec

sys.modules[__name__] = _task_spec
