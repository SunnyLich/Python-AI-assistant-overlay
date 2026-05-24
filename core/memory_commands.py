"""Compatibility alias for core.memory_store.commands."""
import sys
from core.memory_store import commands as _commands

sys.modules[__name__] = _commands
