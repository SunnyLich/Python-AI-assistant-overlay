"""Compatibility alias for core.llm_clients.client."""
import sys
from core.llm_clients import client as _client

sys.modules[__name__] = _client
