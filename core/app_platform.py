"""Compatibility alias for core.system.app_platform."""
import sys
from core.system import app_platform as _app_platform

sys.modules[__name__] = _app_platform
