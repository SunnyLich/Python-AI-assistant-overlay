"""Compatibility alias for core.llm_clients.routes."""
import sys
from core.llm_clients import routes as _routes

sys.modules[__name__] = _routes
