"""Compatibility alias for core.auth.github."""
import sys
from core.auth import github as _github

sys.modules[__name__] = _github
