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

# --- Chat / elaborate LLM (defaults to same as above) ---
CHAT_LLM_PROVIDER = os.getenv("CHAT_LLM_PROVIDER", LLM_PROVIDER)
CHAT_LLM_MODEL    = os.getenv("CHAT_LLM_MODEL",    LLM_MODEL)

# --- Vision LLM (for screen-snip queries — must support image input) ---
# Leave empty to get a helpful error when snip is used without configuration.
VISION_LLM_PROVIDER = os.getenv("VISION_LLM_PROVIDER", "")   # e.g. anthropic | openai
VISION_LLM_MODEL    = os.getenv("VISION_LLM_MODEL",    "")   # e.g. claude-opus-4-5

# --- TTS ---
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "cartesia")    # cartesia | elevenlabs | none
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

# --- App behaviour ---
DOLL_AUTO_HIDE = os.getenv("DOLL_AUTO_HIDE", "true").lower() == "true"  # hide doll when idle
CHAT_AUTO_ELABORATE = os.getenv("CHAT_AUTO_ELABORATE", "true").lower() == "true"  # auto-send elaborate prompt on chat open
CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")

# --- Hotkeys ---
HOTKEY_INVOKE            = os.getenv("HOTKEY_INVOKE",            "ctrl+q")
HOTKEY_CUSTOM_PROMPT_KEY = os.getenv("HOTKEY_CUSTOM_PROMPT_KEY", "s")           # single key in picker → free-text input
HOTKEY_ADD_CONTEXT       = os.getenv("HOTKEY_ADD_CONTEXT",       "alt+q")       # add selected text to context buffer
HOTKEY_CLEAR_CONTEXT     = os.getenv("HOTKEY_CLEAR_CONTEXT",     "alt+w")       # clear context buffer
HOTKEY_SNIP              = os.getenv("HOTKEY_SNIP",              "ctrl+alt+q")  # draw screen region → intent picker

# Dynamic intent rows — shown in the overlay picker after invoke.
# Stored as INTENT_COUNT + INTENT_N_KEY / INTENT_N_LABEL / INTENT_N_PROMPT (1-indexed).
_INTENT_DEFAULTS = [
    ("w", "What is this?",      "What is this? Give me a clear, plain-English explanation in 2-3 sentences."),
    ("a", "Explain simply",     "Explain this as simply as possible. Assume I have no technical background whatsoever."),
    ("d", "How do I fix this?", "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now."),
]

INTENT_COUNT = int(os.getenv("INTENT_COUNT", str(len(_INTENT_DEFAULTS))))
INTENT_ROWS: list[dict] = []
for _i in range(INTENT_COUNT):
    _dk, _dl, _dp = _INTENT_DEFAULTS[_i] if _i < len(_INTENT_DEFAULTS) else ("", f"Intent {_i + 1}", "")
    INTENT_ROWS.append({
        "key":    os.getenv(f"INTENT_{_i + 1}_KEY",    _dk),
        "label":  os.getenv(f"INTENT_{_i + 1}_LABEL",  _dl),
        "prompt": os.getenv(f"INTENT_{_i + 1}_PROMPT", _dp),
    })

# --- UI sizes ---
BUBBLE_WIDTH      = int(os.getenv("BUBBLE_WIDTH",      "340"))  # px wide (not including tail)
BUBBLE_LINES      = int(os.getenv("BUBBLE_LINES",      "2"))    # max lines shown at once
DOLL_SIZE         = int(os.getenv("DOLL_SIZE",         "80"))   # doll icon size px (square)
BUBBLE_REVEAL_WPM = int(os.getenv("BUBBLE_REVEAL_WPM", "170")) # word-reveal speed (WPM fallback mode)

# --- Audio ---
FILLER_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "assets", "filler")
FILLER_MAX_DURATION_MS = 1000   # filler clips must be < 1s

# --- Latency targets (ms) ---
LATENCY_TARGET_MS = 1500
LATENCY_CEILING_MS = 3000

# --- System prompt ---
SYSTEM_PROMPT_UTILITY = os.getenv(
    "SYSTEM_PROMPT_UTILITY",
    "You are a concise desktop assistant. "
    "Answer in 1-3 short sentences. Be direct and plain. No markdown."
)

def get_system_prompt() -> str:
    return SYSTEM_PROMPT_UTILITY


def reload() -> None:
    """Re-read .env and update every module-level variable in-place.

    Call this after writing a new .env so changes take effect without a restart.
    Note: UI size constants (BUBBLE_WIDTH, DOLL_SIZE, …) require widget recreation
    and only fully apply after a restart; everything else is live.
    """
    global GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
    global CARTESIA_API_KEY, ELEVENLABS_API_KEY
    global LLM_PROVIDER, LLM_MODEL, CHAT_LLM_PROVIDER, CHAT_LLM_MODEL
    global VISION_LLM_PROVIDER, VISION_LLM_MODEL
    global TTS_PROVIDER, CARTESIA_VOICE_ID
    global DOLL_AUTO_HIDE, CHAT_AUTO_ELABORATE, CHAT_ELABORATE_PROMPT
    global HOTKEY_INVOKE, HOTKEY_CUSTOM_PROMPT_KEY, HOTKEY_ADD_CONTEXT, HOTKEY_CLEAR_CONTEXT, HOTKEY_SNIP
    global INTENT_COUNT, INTENT_ROWS
    global BUBBLE_WIDTH, BUBBLE_LINES, DOLL_SIZE, BUBBLE_REVEAL_WPM
    global SYSTEM_PROMPT_UTILITY

    load_dotenv(override=True)   # push .env values back into os.environ

    GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
    OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    CARTESIA_API_KEY  = os.getenv("CARTESIA_API_KEY", "")
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
    LLM_MODEL    = os.getenv("LLM_MODEL", "llama3-8b-8192")
    CHAT_LLM_PROVIDER = os.getenv("CHAT_LLM_PROVIDER", LLM_PROVIDER)
    CHAT_LLM_MODEL    = os.getenv("CHAT_LLM_MODEL",    LLM_MODEL)
    VISION_LLM_PROVIDER = os.getenv("VISION_LLM_PROVIDER", "")
    VISION_LLM_MODEL    = os.getenv("VISION_LLM_MODEL",    "")

    TTS_PROVIDER      = os.getenv("TTS_PROVIDER", "cartesia")
    CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

    DOLL_AUTO_HIDE        = os.getenv("DOLL_AUTO_HIDE", "true").lower() == "true"
    CHAT_AUTO_ELABORATE   = os.getenv("CHAT_AUTO_ELABORATE", "true").lower() == "true"
    CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")

    HOTKEY_INVOKE            = os.getenv("HOTKEY_INVOKE",            "ctrl+q")
    HOTKEY_CUSTOM_PROMPT_KEY = os.getenv("HOTKEY_CUSTOM_PROMPT_KEY", "s")
    HOTKEY_ADD_CONTEXT       = os.getenv("HOTKEY_ADD_CONTEXT",       "alt+q")
    HOTKEY_CLEAR_CONTEXT     = os.getenv("HOTKEY_CLEAR_CONTEXT",     "alt+w")
    HOTKEY_SNIP              = os.getenv("HOTKEY_SNIP",              "ctrl+alt+q")

    INTENT_COUNT = int(os.getenv("INTENT_COUNT", str(len(_INTENT_DEFAULTS))))
    INTENT_ROWS.clear()
    for _i in range(INTENT_COUNT):
        _dk, _dl, _dp = _INTENT_DEFAULTS[_i] if _i < len(_INTENT_DEFAULTS) else ("", f"Intent {_i + 1}", "")
        INTENT_ROWS.append({
            "key":    os.getenv(f"INTENT_{_i + 1}_KEY",    _dk),
            "label":  os.getenv(f"INTENT_{_i + 1}_LABEL",  _dl),
            "prompt": os.getenv(f"INTENT_{_i + 1}_PROMPT", _dp),
        })

    BUBBLE_WIDTH      = int(os.getenv("BUBBLE_WIDTH",      "340"))
    BUBBLE_LINES      = int(os.getenv("BUBBLE_LINES",      "2"))
    DOLL_SIZE         = int(os.getenv("DOLL_SIZE",         "80"))
    BUBBLE_REVEAL_WPM = int(os.getenv("BUBBLE_REVEAL_WPM", "170"))

    SYSTEM_PROMPT_UTILITY = os.getenv(
        "SYSTEM_PROMPT_UTILITY",
        "You are a concise desktop assistant. "
        "Answer in 1-3 short sentences. Be direct and plain. No markdown."
    )
