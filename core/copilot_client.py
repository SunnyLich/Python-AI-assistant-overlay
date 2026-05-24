"""Compatibility alias for core.auth.copilot_client."""
import sys
from core.auth import copilot_client as _copilot_client

sys.modules[__name__] = _copilot_client
