"""Compatibility alias for core.memory_store.store."""
import sys
from core.memory_store import store as _store

sys.modules[__name__] = _store
