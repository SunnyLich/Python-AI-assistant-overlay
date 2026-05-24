"""Compatibility alias for core.auth.chatgpt."""
import sys
from core.auth import chatgpt as _chatgpt

sys.modules[__name__] = _chatgpt
