"""Compatibility alias for core.system.paths."""
import sys
from core.system import paths as _paths

sys.modules[__name__] = _paths
