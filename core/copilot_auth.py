"""Compatibility alias for core.auth.copilot_auth."""
import sys
from core.auth import copilot_auth as _copilot_auth

sys.modules[__name__] = _copilot_auth
