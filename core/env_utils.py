"""Compatibility alias for core.system.env_utils."""
import sys
from core.system import env_utils as _env_utils

sys.modules[__name__] = _env_utils
