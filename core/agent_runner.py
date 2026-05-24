"""Compatibility alias for core.agent.runner."""
import sys
from core.agent import runner as _runner

sys.modules[__name__] = _runner
