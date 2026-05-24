"""Canonical filesystem locations for the app."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
DOLL_ASSETS_DIR = ASSETS_DIR / "doll"
FILLER_AUDIO_DIR = ASSETS_DIR / "filler"
MEMORY_DIR = REPO_ROOT / "memory"
AGENT_RUNS_DIR = MEMORY_DIR / "agent_runs"
TOOLS_INSTALLED_DIR = REPO_ROOT / "tools" / "installed"
