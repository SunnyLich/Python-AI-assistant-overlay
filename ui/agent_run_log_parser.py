"""Compatibility alias for ui.agent.log_parser."""
import sys
from ui.agent import log_parser as _log_parser

sys.modules[__name__] = _log_parser
