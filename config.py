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

# --- Tool-capable LLM (used when web_search / get_context tools are active) ---
# Haiku does not invoke web_search_20250305; Sonnet does. Defaults to Sonnet so tools
# work out of the box even if LLM_MODEL is set to Haiku.
TOOL_LLM_MODEL = os.getenv("TOOL_LLM_MODEL", "claude-sonnet-4-5")

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
HOTKEY_ADD_CONTEXT   = os.getenv("HOTKEY_ADD_CONTEXT",   "alt+q")       # add selected text to context buffer
HOTKEY_CLEAR_CONTEXT = os.getenv("HOTKEY_CLEAR_CONTEXT", "alt+w")       # clear context buffer
HOTKEY_SNIP          = os.getenv("HOTKEY_SNIP",          "ctrl+alt+q")  # draw screen region → intent picker
HOTKEY_VOICE         = os.getenv("HOTKEY_VOICE",         "f9")          # push-to-talk voice input

# --- STT (Speech-to-Text) ---
STT_MODEL        = os.getenv("STT_MODEL",        "base")   # tiny | base | small | medium | large-v3
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")   # int8 | float16 | float32
STT_LANGUAGE     = os.getenv("STT_LANGUAGE",     "en")     # ISO 639-1 code, or "" for auto-detect

# --- Caller rows ---
# Each "caller" is a hotkey that shows the WASD intent picker.
# Stored as CALLER_COUNT + CALLER_N_* env vars (1-indexed).
_CALLER_DEFAULTS: list[dict] = [
    {
        "hotkey": "ctrl+q",
        "label": "General",
        "paste_back": False,
        "custom_key": "s",
        "intents": [
            {"key": "w", "label": "What is this?",      "prompt": "What is this? Give me a clear, plain-English explanation in 2-3 sentences."},
            {"key": "a", "label": "Explain simply",     "prompt": "Explain this as simply as possible. Assume I have no technical background whatsoever."},
            {"key": "d", "label": "How do I fix this?", "prompt": "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now."},
        ],
    },
    {
        "hotkey": "ctrl+shift+q",
        "label": "Rewrite & Paste",
        "paste_back": True,
        "custom_key": "s",
        "intents": [
            {"key": "w", "label": "Fix grammar",   "prompt": "Fix the grammar and spelling of the following text. Output ONLY the corrected text."},
            {"key": "a", "label": "Simplify",      "prompt": "Simplify the following text for a general audience. Output ONLY the simplified text."},
            {"key": "d", "label": "Improve tone",  "prompt": "Rewrite the following text to sound more professional and polished. Output ONLY the rewritten text."},
        ],
    },
]


def _load_caller_rows() -> list[dict]:
    """Read CALLER_COUNT + CALLER_N_* env vars, fall back to _CALLER_DEFAULTS."""
    count = int(os.getenv("CALLER_COUNT", str(len(_CALLER_DEFAULTS))))
    rows: list[dict] = []
    for i in range(count):
        n = i + 1
        default = _CALLER_DEFAULTS[i] if i < len(_CALLER_DEFAULTS) else {}
        intent_count = int(os.getenv(f"CALLER_{n}_INTENT_COUNT", str(len(default.get("intents", [])))))
        intents = []
        for j in range(intent_count):
            m = j + 1
            di = default.get("intents", [])
            d_intent = di[j] if j < len(di) else {}
            intents.append({
                "key":    os.getenv(f"CALLER_{n}_INTENT_{m}_KEY",    d_intent.get("key", "")),
                "label":  os.getenv(f"CALLER_{n}_INTENT_{m}_LABEL",  d_intent.get("label", "")),
                "prompt": os.getenv(f"CALLER_{n}_INTENT_{m}_PROMPT", d_intent.get("prompt", "")),
            })
        rows.append({
            "hotkey":     os.getenv(f"CALLER_{n}_HOTKEY",     default.get("hotkey", "")),
            "label":      os.getenv(f"CALLER_{n}_LABEL",      default.get("label", "")),
            "paste_back": os.getenv(f"CALLER_{n}_PASTE_BACK", str(default.get("paste_back", False))).lower() == "true",
            "custom_key": os.getenv(f"CALLER_{n}_CUSTOM_KEY", default.get("custom_key", "s")),
            "intents":    intents,
        })
    return rows


CALLER_ROWS: list[dict] = _load_caller_rows()

# --- UI sizes ---
BUBBLE_WIDTH      = int(os.getenv("BUBBLE_WIDTH",      "340"))  # px wide (not including tail)
BUBBLE_LINES      = int(os.getenv("BUBBLE_LINES",      "2"))    # max lines shown at once
BUBBLE_COLOR      = os.getenv("BUBBLE_COLOR",      "#1c1c24dc") # bubble fill, #RRGGBB or #RRGGBBAA
BUBBLE_TEXT_COLOR = os.getenv("BUBBLE_TEXT_COLOR", "#e6e6e6")   # unread/default text color
BUBBLE_READ_WORD_COLOR = os.getenv("BUBBLE_READ_WORD_COLOR", "#4da3ff") # read/current word color
DOLL_SIZE         = int(os.getenv("DOLL_SIZE",         "80"))   # doll icon size px (square, sprite fallback)
VRM_WIDTH         = int(os.getenv("VRM_WIDTH",         "200"))  # VRM overlay width px
VRM_HEIGHT        = int(os.getenv("VRM_HEIGHT",        "300"))  # VRM overlay height px
BUBBLE_REVEAL_WPM = int(os.getenv("BUBBLE_REVEAL_WPM", "170")) # word-reveal speed (WPM fallback mode)
BUBBLE_HOLD_REVEAL_WPM = int(os.getenv("BUBBLE_HOLD_REVEAL_WPM", "480")) # hold bubble to speed up reveal
TTS_PLAYBACK_RATE = float(os.getenv("TTS_PLAYBACK_RATE", "1.0")) # normal TTS playback speed
TTS_HOLD_PLAYBACK_RATE = float(os.getenv("TTS_HOLD_PLAYBACK_RATE", "1.35")) # hold bubble to speed up TTS

# --- Memory ---
# LLM used for consolidation / compression (defaults to chat LLM).
MEMORY_LLM_PROVIDER             = os.getenv("MEMORY_LLM_PROVIDER",             CHAT_LLM_PROVIDER)
MEMORY_LLM_MODEL                = os.getenv("MEMORY_LLM_MODEL",                CHAT_LLM_MODEL)
# How often (minutes) to extract facts from the session and write them to LTM.
MEMORY_CONSOLIDATION_INTERVAL   = int(os.getenv("MEMORY_CONSOLIDATION_INTERVAL",   "15"))
# How many LTM facts to retrieve per LLM call (semantic top-k).
MEMORY_TOP_K                    = int(os.getenv("MEMORY_TOP_K",                    "3"))
# Approximate token budget for raw STM turns before mid-session compression kicks in.
MEMORY_STM_TOKEN_BUDGET         = int(os.getenv("MEMORY_STM_TOKEN_BUDGET",         "4000"))

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
    "Answer in 1-3 short sentences. Be direct and plain. No markdown. "
    "You have access to a web_search tool and a get_context tool. "
    "Use web_search for current information and use get_context with a URL "
    "when the user asks about a specific page. Never print or simulate tool "
    "calls in the reply."
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
    global LLM_PROVIDER, LLM_MODEL, CHAT_LLM_PROVIDER, CHAT_LLM_MODEL, TOOL_LLM_MODEL
    global VISION_LLM_PROVIDER, VISION_LLM_MODEL
    global TTS_PROVIDER, CARTESIA_VOICE_ID
    global DOLL_AUTO_HIDE, CHAT_AUTO_ELABORATE, CHAT_ELABORATE_PROMPT
    global HOTKEY_ADD_CONTEXT, HOTKEY_CLEAR_CONTEXT, HOTKEY_SNIP
    global HOTKEY_VOICE, STT_MODEL, STT_COMPUTE_TYPE, STT_LANGUAGE
    global CALLER_ROWS
    global BUBBLE_WIDTH, BUBBLE_LINES, BUBBLE_COLOR, BUBBLE_TEXT_COLOR, BUBBLE_READ_WORD_COLOR
    global DOLL_SIZE, BUBBLE_REVEAL_WPM, BUBBLE_HOLD_REVEAL_WPM
    global TTS_PLAYBACK_RATE, TTS_HOLD_PLAYBACK_RATE
    global SYSTEM_PROMPT_UTILITY
    global MEMORY_LLM_PROVIDER, MEMORY_LLM_MODEL
    global MEMORY_CONSOLIDATION_INTERVAL, MEMORY_TOP_K, MEMORY_STM_TOKEN_BUDGET

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
    TOOL_LLM_MODEL    = os.getenv("TOOL_LLM_MODEL",    "claude-sonnet-4-5")
    VISION_LLM_PROVIDER = os.getenv("VISION_LLM_PROVIDER", "")
    VISION_LLM_MODEL    = os.getenv("VISION_LLM_MODEL",    "")

    TTS_PROVIDER      = os.getenv("TTS_PROVIDER", "cartesia")
    CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

    DOLL_AUTO_HIDE        = os.getenv("DOLL_AUTO_HIDE", "true").lower() == "true"
    CHAT_AUTO_ELABORATE   = os.getenv("CHAT_AUTO_ELABORATE", "true").lower() == "true"
    CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")

    HOTKEY_ADD_CONTEXT       = os.getenv("HOTKEY_ADD_CONTEXT",   "alt+q")
    HOTKEY_CLEAR_CONTEXT     = os.getenv("HOTKEY_CLEAR_CONTEXT", "alt+w")
    HOTKEY_SNIP              = os.getenv("HOTKEY_SNIP",          "ctrl+alt+q")
    HOTKEY_VOICE             = os.getenv("HOTKEY_VOICE",         "f9")
    STT_MODEL        = os.getenv("STT_MODEL",        "base")
    STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
    STT_LANGUAGE     = os.getenv("STT_LANGUAGE",     "en")

    CALLER_ROWS.clear()
    CALLER_ROWS.extend(_load_caller_rows())

    BUBBLE_WIDTH      = int(os.getenv("BUBBLE_WIDTH",      "340"))
    BUBBLE_LINES      = int(os.getenv("BUBBLE_LINES",      "2"))
    BUBBLE_COLOR      = os.getenv("BUBBLE_COLOR",      "#1c1c24dc")
    BUBBLE_TEXT_COLOR = os.getenv("BUBBLE_TEXT_COLOR", "#e6e6e6")
    BUBBLE_READ_WORD_COLOR = os.getenv("BUBBLE_READ_WORD_COLOR", "#4da3ff")
    DOLL_SIZE         = int(os.getenv("DOLL_SIZE",         "80"))
    VRM_WIDTH         = int(os.getenv("VRM_WIDTH",         "200"))
    VRM_HEIGHT        = int(os.getenv("VRM_HEIGHT",        "300"))
    BUBBLE_REVEAL_WPM = int(os.getenv("BUBBLE_REVEAL_WPM", "170"))
    BUBBLE_HOLD_REVEAL_WPM = int(os.getenv("BUBBLE_HOLD_REVEAL_WPM", "480"))
    TTS_PLAYBACK_RATE = float(os.getenv("TTS_PLAYBACK_RATE", "1.0"))
    TTS_HOLD_PLAYBACK_RATE = float(os.getenv("TTS_HOLD_PLAYBACK_RATE", "1.35"))

    MEMORY_LLM_PROVIDER           = os.getenv("MEMORY_LLM_PROVIDER",           CHAT_LLM_PROVIDER)
    MEMORY_LLM_MODEL              = os.getenv("MEMORY_LLM_MODEL",              CHAT_LLM_MODEL)
    MEMORY_CONSOLIDATION_INTERVAL = int(os.getenv("MEMORY_CONSOLIDATION_INTERVAL", "15"))
    MEMORY_TOP_K                  = int(os.getenv("MEMORY_TOP_K",                  "3"))
    MEMORY_STM_TOKEN_BUDGET       = int(os.getenv("MEMORY_STM_TOKEN_BUDGET",       "4000"))

    SYSTEM_PROMPT_UTILITY = os.getenv(
        "SYSTEM_PROMPT_UTILITY",
        "You are a concise desktop assistant. "
        "Answer in 1-3 short sentences. Be direct and plain. No markdown. "
        "You have access to a web_search tool and a get_context tool. "
        "Use web_search for current information and use get_context with a URL "
        "when the user asks about a specific page. Never print or simulate tool "
        "calls in the reply."
    )
