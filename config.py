"""
config.py — Central configuration loaded from .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# --- LLM ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")       # groq | openai | anthropic
LLM_MODEL = os.getenv("LLM_MODEL", "llama3-8b-8192")

# --- TTS ---
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "cartesia")    # cartesia | elevenlabs | none
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

# --- Hotkeys ---
HOTKEY_INVOKE = os.getenv("HOTKEY_INVOKE", "ctrl+e")

# Intent shortcut mapping: ctrl+u then arrow key
# `label`  — shown on the on-screen picker overlay
# `prompt` — the actual instruction sent to the LLM (can be more detailed)
INTENT_SHORTCUTS = {
    "up": {
        "label":  "What is this?",
        "prompt": "What is this? Give me a clear, plain-English explanation in 2-3 sentences.",
    },
    "down": {
        "label":  "Custom prompt",
        "prompt": "",   # unused — user types their own
    },
    "left": {
        "label":  "Explain simply",
        "prompt": "Explain this as simply as possible. Assume I have no technical background whatsoever.",
    },
    "right": {
        "label":  "How do I fix this?",
        "prompt": "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now.",
   },
}

# --- Audio ---
FILLER_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "assets", "filler")
FILLER_MAX_DURATION_MS = 1000   # filler clips must be < 1s

# --- Latency targets (ms) ---
LATENCY_TARGET_MS = 1500
LATENCY_CEILING_MS = 3000

# --- Personality ---
CUTE_MODE = False  # opt-in personality toggle

# --- System prompt ---
SYSTEM_PROMPT_UTILITY = (
    "You are a concise desktop assistant. "
    "Answer in 1-3 short sentences. Be direct and plain. No markdown."
    ""
)
SYSTEM_PROMPT_CUTE = (
    "You are a cheerful, friendly desktop companion. "
    "Answer in 1-3 short sentences. Be warm but concise. No markdown."
)

def get_system_prompt() -> str:
    return SYSTEM_PROMPT_CUTE if CUTE_MODE else SYSTEM_PROMPT_UTILITY
