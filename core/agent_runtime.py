"""Compatibility alias for core.agent.runtime."""
import sys
from core.agent import runtime as _runtime

sys.modules[__name__] = _runtime
