"""
core/llm.py -” Cloud LLM client with streaming support.

Supports:
  - Groq (OpenAI-compatible, fast TTFT)
  - OpenAI
  - Anthropic Claude

Clients are module-level singletons so the TLS connection is reused across
calls, eliminating handshake overhead from every request.
"""
from __future__ import annotations
import config
from pathlib import Path
from core.tool_registry import ToolRegistry, ToolSpec
from core.system import macos_safety
from core.system.native_locks import native_init_lock, ssl_init_lock
from core.system import sdk_clients
from core.llm_clients.routes import (
    GOOGLE_OPENAI_BASE_URL as _GOOGLE_OPENAI_BASE_URL,
    DEEPSEEK_BASE_URL as _DEEPSEEK_BASE_URL,
    OPENROUTER_BASE_URL as _OPENROUTER_BASE_URL,
    MISTRAL_BASE_URL as _MISTRAL_BASE_URL,
    XAI_BASE_URL as _XAI_BASE_URL,
    TOGETHER_BASE_URL as _TOGETHER_BASE_URL,
    CEREBRAS_BASE_URL as _CEREBRAS_BASE_URL,
    OLLAMA_BASE_URL as _OLLAMA_BASE_URL,
    api_key_for as _api_key_for,
    credential_source_for_provider as _credential_source_for_provider,
    parse_model_fallbacks as _parse_model_fallbacks,
    route_candidates as _route_candidates,
    normalize_model_for_provider as _normalize_model_for_provider,
)
from typing import Generator

_TOOL_REGISTRY = ToolRegistry()


def _log_context(
    reason: str,
    text: str,
    max_line: int = 120,
    max_lines: int = 12,
    max_chars: int = 1200,
) -> None:
    """Print a compact preview of a context block for debugging."""
    import time

    ts = time.strftime("%H:%M:%S")

    def _trim(line: str) -> str:
        return line if len(line) <= max_line else line[:max_line] + "-¦"

    lines = [_trim(l) for l in text.splitlines() if l.strip()]
    truncated = False

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    body = "\n  ".join(lines) if lines else "[empty]"
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "-¦"
        truncated = True
    if truncated and body != "[empty]":
        body += "\n  [preview truncated]"

    print(f"[llm {ts}] Context -” {reason}:\n  {body}")


def _ambient_document_max_chars() -> int:
    return config.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS


def _tool_document_max_chars() -> int:
    return config.CONTEXT_TOOL_DOCUMENT_MAX_CHARS


def _normalize_pdf_text(s: str) -> str:
    """Collapse layout whitespace from PDF text.

    LiteParse pads its output with horizontal spacing used for visual
    layout, which carries no extra content but multiplies the token count
    sent to the LLM. Collapsing intra-line whitespace yields the same text
    pypdf would, at a fraction of the tokens.
    """
    import re
    lines = []
    for line in s.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _read_pdf_text(path: str, max_chars: int) -> str:
    """Extract PDF text, preferring LiteParse (fast, native) over pypdf."""
    parts: list[str] = []
    total = 0
    try:
        import liteparse  # type: ignore
    except ImportError:
        liteparse = None
    if liteparse is not None:
        try:
            # LiteParse parses every page up front, so cap pages to roughly
            # what max_chars can hold (with a buffer for sparse pages) instead
            # of parsing the whole document just to truncate the output.
            page_cap = max(8, max_chars // 500 + 5)
            lp = liteparse.LiteParse(ocr_enabled=False, quiet=True, max_pages=page_cap)
            result = lp.parse(path)
            for i in range(1, result.num_pages + 1):
                page = result.get_page(i)
                page_text = _normalize_pdf_text(page.text) if page and page.text else ""
                if page_text:
                    parts.append(f"[Page {i}]\n{page_text}")
                    total += len(page_text)
                    if total > max_chars:
                        break
            return "\n\n".join(parts)
        except Exception:
            parts.clear()
    # Fallback: pure-Python pypdf (slower, no native dependency).
    import pypdf  # type: ignore
    reader = pypdf.PdfReader(path)
    for i, page in enumerate(reader.pages, 1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            parts.append(f"[Page {i}]\n{page_text}")
            total += len(page_text)
            if total > max_chars:
                break
    return "\n\n".join(parts)


def _read_document_file(path: str, max_chars: int | None = None) -> str:
    """Read a local document file and return its plain text."""
    import os
    if max_chars is None:
        max_chars = _ambient_document_max_chars()
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            from docx import Document  # type: ignore
            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext in (".xlsx", ".xls"):
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets:
                parts.append(f"[Sheet: {sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        parts.append("\t".join(cells))
            text = "\n".join(parts)
        elif ext == ".pptx":
            from pptx import Presentation  # type: ignore
            prs = Presentation(path)
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                parts.append(f"[Slide {i}]")
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            line = para.text.strip()
                            if line:
                                parts.append(line)
            text = "\n".join(parts)
        elif ext == ".pdf":
            text = _read_pdf_text(path, max_chars)
        elif ext in (".odt", ".ods", ".odp"):
            from odf import text as odf_text, teletype  # type: ignore
            from odf.opendocument import load as odf_load  # type: ignore
            doc = odf_load(path)
            paragraphs = doc.getElementsByType(odf_text.P)
            text = "\n".join(
                teletype.extractText(p) for p in paragraphs
                if teletype.extractText(p).strip()
            )
        elif ext in (".txt", ".md", ".csv", ".py", ".js", ".ts",
                     ".json", ".xml", ".html", ".log"):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            return f"File type {ext!r} is not supported for reading."
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[-¦truncated]"
        # Redact sensitive data before the text reaches the LLM.
        from core.context_fetcher import _redact  # noqa: PLC0415
        text = _redact(text)
        _log_context(f"tool: read_document -” read {path!r}", text)
        return text
    except Exception as e:
        return f"Failed to read {path!r}: {e}"


def _read_document_paths(
    paths: list[str],
    max_chars_per_doc: int | None = None,
) -> str:
    """Read multiple local document files and join readable results."""
    import os

    if max_chars_per_doc is None:
        max_chars_per_doc = _ambient_document_max_chars()
    parts: list[str] = []
    for path in paths:
        text = _read_document_file(path, max_chars=max_chars_per_doc)
        if text and not text.startswith(("Could not", "File type", "Failed to")):
            parts.append(f"[{os.path.basename(path)}]\n{text}")
    return "\n\n".join(parts)


def _execute_get_context(inputs: dict) -> str:
    """Built-in context tool: fetch a browser page or open document text."""
    url = inputs.get("url", "").strip()
    if url:
        from core.context_fetcher import fetch_browser_content_for_tool
        result = fetch_browser_content_for_tool(url)
        _log_context(
            f"tool: get_context (browser) -” {url!r}",
            result or "",
        )
        return result or f"Could not fetch content from {url!r}."

    from core.context_fetcher import get_all_open_document_paths

    paths = get_all_open_document_paths()
    if not paths:
        return "Could not determine any open document paths from supported app windows."

    text = _read_document_paths(paths, max_chars_per_doc=_tool_document_max_chars())
    if text:
        return text
    return "Open document windows were detected, but none of their file types were readable."


def _execute_git_status(inputs: dict) -> str:
    cwd = inputs.get("cwd") or config.TOOL_GIT_ROOT
    return _run_read_only_command(["git", "status", "--short"], cwd=cwd)


def _execute_git_diff(inputs: dict) -> str:
    cwd = inputs.get("cwd") or config.TOOL_GIT_ROOT
    return _run_read_only_command(["git", "diff", "--", "."], cwd=cwd)


def _run_read_only_command(args: list[str], cwd: str) -> str:
    import subprocess
    from pathlib import Path

    root = Path(cwd or ".").expanduser().resolve()
    completed = subprocess.run(
        args,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    return output[:12000] or f"{' '.join(args)} returned no output."


def _execute_github_repo(inputs: dict) -> str:
    repo = str(inputs.get("repo") or "").strip()
    if not repo:
        return "Missing repo. Use owner/name."
    return _github_get_json(f"https://api.github.com/repos/{repo}")


def _execute_github_issue(inputs: dict) -> str:
    repo = str(inputs.get("repo") or "").strip()
    number = str(inputs.get("number") or "").strip()
    if not repo or not number:
        return "Missing repo or number."
    return _github_get_json(f"https://api.github.com/repos/{repo}/issues/{number}")


def _github_get_json(url: str) -> str:
    import json
    import urllib.request
    from core.auth import github as github_auth

    token = github_auth.get_valid_access_token()
    if not token:
        return "GitHub OAuth is not configured. Sign in from Settings."
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "python-ai-overlay",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return json.dumps(data, indent=2, ensure_ascii=False)[:12000]


def _register_builtin_tools() -> None:
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="web_search",
            description="Search the web for current information.",
            input_schema={"type": "object", "properties": {}, "required": []},
            server_schema={
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 2,
            },
        )
    )
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="get_context",
            description=(
                "Retrieve additional context the user can see. "
                "Pass a URL to fetch a web page; omit it to read open local "
                "documents from supported apps "
                "(Word, Excel, PowerPoint, PDF, LibreOffice, Notepad, etc.)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "A web page URL (http:// or https://) to fetch. "
                            "Omit this field to read open local documents instead."
                        ),
                    }
                },
                "required": [],
            },
            executor=_execute_get_context,
        )
    )
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="capture_screen",
            description=(
                "Take a screenshot of the user's primary monitor and see it. "
                "Use this only when the text context already provided is not "
                "enough to answer and you need to visually see what is on the "
                "user's screen (UI layout, a chart, an image, an error dialog, "
                "etc.)."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            # Handled directly in the Anthropic and OpenAI tool loops, which
            # deliver the screenshot as image content. The executor below is a
            # text fallback for any path that calls it without that handling.
            executor=lambda _inputs: (
                "Screen capture could not be returned in this context."
            ),
            opt_in=True,
        )
    )
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="git_status",
            description="Return read-only git status for the configured local repository.",
            input_schema={
                "type": "object",
                "properties": {"cwd": {"type": "string", "description": "Optional local repo path."}},
                "required": [],
            },
            executor=_execute_git_status,
        )
    )
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="git_diff",
            description="Return read-only git diff for the configured local repository.",
            input_schema={
                "type": "object",
                "properties": {"cwd": {"type": "string", "description": "Optional local repo path."}},
                "required": [],
            },
            executor=_execute_git_diff,
        )
    )
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="github_repo",
            description="Fetch GitHub repository metadata using the signed-in GitHub OAuth account.",
            input_schema={
                "type": "object",
                "properties": {"repo": {"type": "string", "description": "Repository in owner/name form."}},
                "required": ["repo"],
            },
            executor=_execute_github_repo,
        )
    )
    _TOOL_REGISTRY.register_builtin(
        ToolSpec(
            name="github_issue",
            description="Fetch a GitHub issue or pull request by repository and number.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository in owner/name form."},
                    "number": {"type": "integer", "description": "Issue or pull request number."},
                },
                "required": ["repo", "number"],
            },
            executor=_execute_github_issue,
        )
    )


# Appended to the system prompt only when the screenshot tool is actually
# offered, so the model knows it can see the screen instead of denying it.
_SCREENSHOT_TOOL_NOTE = (
    "You also have a capture_screen tool that takes a screenshot of the user's "
    "screen. When answering needs you to visually see what is on screen — a "
    "website, app UI, image, chart, or error — call capture_screen instead of "
    "saying you cannot see the screen."
)


def _with_screenshot_note(system: str, allow_screenshot_tool: bool) -> str:
    """Append the screenshot capability note when that tool is exposed."""
    if allow_screenshot_tool:
        return f"{system}\n\n{_SCREENSHOT_TOOL_NOTE}"
    return system


# Substrings of model names known to accept image input. Best-effort only, used
# for a settings-time heads-up; unknown models are treated as text-only so we err
# toward warning. This never gates runtime behavior.
_VISION_MODEL_HINTS = (
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "chatgpt-4o",
    "o1", "o3", "o4",
    "claude", "sonnet", "opus", "haiku",
    "gemini",
    "pixtral", "mistral-small-3", "mistral-medium",
    "llama-3.2-11b", "llama-3.2-90b", "llama-4", "scout", "maverick",
    "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
    "internvl", "phi-3.5-vision", "phi-4-multimodal",
    "grok-2-vision", "grok-4",
    "vision", "-vl", "multimodal",
)


def _model_accepts_images(model: str) -> bool:
    m = (model or "").strip().lower()
    return any(hint in m for hint in _VISION_MODEL_HINTS)


def screenshot_capability_warnings(
    screenshot_modes,
    *,
    llm_provider: str,
    llm_model: str,
    vision_provider: str,
    vision_model: str,
) -> list[str]:
    """Best-effort warnings if enabled screenshot modes likely can't be served.

    Advisory only — the caller should still honor the user's setting and just
    surface these. *screenshot_modes* is the per-caller list of "off"/"auto"/
    "model" values. Returns human-readable warning strings (possibly empty).
    """
    modes = {m for m in screenshot_modes if m and m != "off"}
    if not modes:
        return []

    llm_provider = (llm_provider or "").strip().lower()
    vision_provider = (vision_provider or "").strip().lower()
    vision_model = (vision_model or "").strip()
    warnings: list[str] = []

    if "auto" in modes:
        # Eager ("On") screenshots always route through the Vision LLM.
        if not vision_model:
            warnings.append(
                "Auto screenshot needs a Vision model, but none is set — "
                "configure one under Vision LLM or auto screenshots will fail."
            )
        elif vision_provider == "copilot":
            warnings.append(
                "The Copilot provider can't process images, so auto screenshots "
                "will fail. Use a Vision LLM that supports images."
            )
        elif not _model_accepts_images(vision_model):
            warnings.append(
                f"Your Vision model '{vision_model}' may not accept images, so "
                "auto screenshots may fail."
            )

    if "model" in modes:
        # On-demand ("Let model decide") screenshots run through the tool loop.
        if llm_provider in ("copilot", "chatgpt"):
            warnings.append(
                f"'Let model decide' screenshots aren't supported on the "
                f"'{llm_provider}' provider, so the model can't take one there."
            )
        elif llm_provider == "anthropic":
            pass  # Claude tool models accept images
        elif llm_provider in _OPENAI_COMPAT_PROVIDER_SET:
            # Answered by a same-provider Vision model if set, else the main model.
            target = (
                vision_model
                if (vision_provider == llm_provider and vision_model)
                else (llm_model or "").strip()
            )
            if not _model_accepts_images(target):
                warnings.append(
                    f"'Let model decide' screenshots on '{llm_provider}' will go "
                    f"to '{target or '(your model)'}', which may not accept images. "
                    f"Set a vision-capable Vision model on '{llm_provider}', or "
                    "screenshots may fail."
                )

    return warnings


def tool_capability_warnings(tools_enabled: bool, *, llm_provider: str) -> list[str]:
    """Best-effort warning if the context-tools setting won't behave as a user
    expects on the active provider. Advisory only."""
    if not tools_enabled:
        return []
    if (llm_provider or "").strip().lower() == "chatgpt":
        return [
            "On the ChatGPT provider, model tools (web search, GitHub, open "
            "documents) aren't run as live tool calls — available context is "
            "injected up front instead."
        ]
    return []


def _get_tool_schemas(
    prompt: str = "",
    *,
    include_general: bool = True,
    include_screenshot: bool = False,
) -> list[dict]:
    """Anthropic tool schemas for a query.

    capture_screen is opt-in (include_screenshot) and is never part of the
    general set, so it only appears when the caller explicitly allows the model
    to take screenshots. include_general gates every other built-in tool.
    """
    schemas: list[dict] = []
    if include_general:
        schemas = _TOOL_REGISTRY.filtered_schemas(prompt, include_server_tools=True)
    if include_screenshot:
        spec = _TOOL_REGISTRY.get_tool("capture_screen")
        if spec is not None:
            schemas.append(spec.anthropic_schema())
    return schemas


def _get_openai_tool_schemas(
    prompt: str = "",
    *,
    include_general: bool = True,
    include_screenshot: bool = False,
) -> list[dict]:
    """OpenAI/Groq function schemas for a query (mirror of _get_tool_schemas).

    capture_screen is opt-in, so it's added back explicitly only when
    include_screenshot is set.
    """
    schemas: list[dict] = []
    if include_general:
        schemas = _TOOL_REGISTRY.filtered_openai_schemas(prompt)
    if include_screenshot:
        spec = _TOOL_REGISTRY.get_tool("capture_screen")
        if spec is not None:
            schemas.append(spec.openai_schema())
    return schemas


def _execute_model_tool(name: str, inputs: dict) -> str:
    return _TOOL_REGISTRY.execute(name, inputs)


def _capture_screen_b64() -> str | None:
    """Grab the primary monitor and return it as a base64 PNG, or None on failure."""
    try:
        from core import capture
        return capture.image_to_base64(capture.get_screen_snippet())
    except Exception as exc:
        print(f"[llm] capture_screen failed: {exc}")
        return None


def _anthropic_vision_model(current_model: str) -> str:
    """Model to use after a screenshot enters the loop.

    Prefer the configured Anthropic vision model; otherwise keep the current
    tool model (Sonnet, which is itself vision-capable).
    """
    if config.VISION_LLM_PROVIDER.strip() == "anthropic" and config.VISION_LLM_MODEL.strip():
        return config.VISION_LLM_MODEL.strip()
    return current_model


def _openai_vision_model(provider: str, current_model: str) -> str:
    """Model to answer with after a screenshot enters an OpenAI/Groq loop.

    Prefer the configured vision model when it lives on the *same* provider
    (we can't swap the bound client mid-loop); otherwise keep the current
    model and hope it is vision-capable (e.g. gpt-4o).
    """
    if config.VISION_LLM_PROVIDER.strip() == provider and config.VISION_LLM_MODEL.strip():
        return config.VISION_LLM_MODEL.strip()
    return current_model


def _init_keyword_filters() -> None:
    from core.system.paths import TOOL_KEYWORDS_FILE
    _TOOL_REGISTRY.load_keyword_filters(TOOL_KEYWORDS_FILE)
    if not TOOL_KEYWORDS_FILE.exists():
        _TOOL_REGISTRY.save_keyword_filters(TOOL_KEYWORDS_FILE)


_register_builtin_tools()
_init_keyword_filters()


def get_tool_registry() -> ToolRegistry:
    """Return the shared model tool registry. Used by the mod manager."""
    return _TOOL_REGISTRY


def read_document_file(path: str, max_chars: int | None = None) -> str:
    """Read a single local document file and return its plain text (public API)."""
    return _read_document_file(path, max_chars=max_chars)


def read_active_document_for_context() -> str:
    """
    Read all open doc-app windows (foreground and background) and return their
    redacted plain text for proactive injection into the system prompt.
    Multiple documents are separated by per-file headers.
    Returns "" if no readable documents are found.
    """
    from core.context_fetcher import get_all_open_document_paths

    paths = get_all_open_document_paths()
    if not paths:
        return ""
    return _read_document_paths(paths)


# ------------------------------------------------------------------
# Singleton clients -” initialised once, reused across all requests
# ------------------------------------------------------------------
_openai_client = None
_anthropic_client = None
_chat_openai_client = None
_chat_anthropic_client = None
_vision_openai_client = None
_vision_anthropic_client = None
_codex_client = None
_chat_codex_client = None

_TEST_IMAGE_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII="

# How many times the OpenAI SDK may retry the *same* model inside one call.
_OPENAI_MAX_RETRIES = 1

# Quota circuit breaker: when a route returns 429, it is parked for this many
# seconds so subsequent turns skip straight to the fallback instead of
# re-probing an exhausted model on every reply. After it expires the route is
# tried again (some retries, not an infinite skip).
_ROUTE_COOLDOWN_SECONDS = 60.0
import threading as _threading
import time as _time
_route_cooldowns: dict[tuple[str, str], float] = {}
_route_cooldowns_lock = _threading.Lock()
_codex_client_lock = _threading.Lock()


def _route_key(provider: str, model: str) -> tuple[str, str]:
    return ((provider or "").lower(), model or "")


def _is_route_cooling(provider: str, model: str) -> bool:
    key = _route_key(provider, model)
    with _route_cooldowns_lock:
        until = _route_cooldowns.get(key)
        if until is None:
            return False
        if _time.time() >= until:
            _route_cooldowns.pop(key, None)
            return False
        return True


def _mark_route_cooling(provider: str, model: str, seconds: float = _ROUTE_COOLDOWN_SECONDS) -> None:
    with _route_cooldowns_lock:
        _route_cooldowns[_route_key(provider, model)] = _time.time() + seconds


def _is_quota_error(exc: Exception) -> bool:
    """True for 429 / rate-limit / quota-exhausted errors worth a cooldown."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    text = str(exc).lower()
    return "429" in text or "quota" in text or "rate limit" in text or "rate_limit" in text


def reset_clients() -> None:
    """Discard all cached API clients so they are rebuilt with the current config."""
    global _openai_client, _anthropic_client, _chat_openai_client, _chat_anthropic_client
    global _vision_openai_client, _vision_anthropic_client, _codex_client, _chat_codex_client
    global _dynamic_anthropic_client_cache
    _openai_client = _anthropic_client = None
    _chat_openai_client = _chat_anthropic_client = None
    _vision_openai_client = _vision_anthropic_client = None
    _codex_client = _chat_codex_client = None
    with _dynamic_client_lock:
        _dynamic_openai_clients.clear()
        _dynamic_anthropic_client_cache = None
    _TOOL_REGISTRY.plugin_dir = Path(config.TOOL_PLUGIN_DIR)
    _TOOL_REGISTRY.refresh()


# ------------------------------------------------------------------
# Codex (ChatGPT subscription) -” custom httpx transport + client
# ------------------------------------------------------------------

class _CodexTransport:
    """
    httpx-compatible BaseTransport that:
      -¢ strips the placeholder API-key authorization header
      -¢ injects ``Authorization: Bearer <access_token>``
      -¢ adds ``ChatGPT-Account-Id`` and ``originator`` headers
    Tokens are fetched (and refreshed) lazily on every request.
    """

    def __init__(self):
        import httpx
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request):
        import httpx
        from core.auth import chatgpt as chatgpt_auth

        token      = chatgpt_auth.get_valid_access_token()
        account_id = chatgpt_auth.get_account_id()

        # Rebuild raw headers, dropping the dummy API-key auth header
        raw: list[tuple[bytes, bytes]] = [
            (name, value)
            for name, value in request.headers.raw
            if name.lower() != b"authorization"
        ]
        if token:
            raw.append((b"authorization", f"Bearer {token}".encode()))
        if account_id:
            raw.append((b"chatgpt-account-id", account_id.encode()))
        raw.append((b"originator", b"opencode"))

        new_req = httpx.Request(
            method=request.method,
            url=request.url,
            headers=raw,
            content=request.content,
            extensions=request.extensions,
        )
        return self._inner.handle_request(new_req)

    def close(self):
        self._inner.close()


def _get_codex_client():
    global _codex_client
    if _codex_client is None:
        with _codex_client_lock:
            if _codex_client is None:
                with ssl_init_lock():
                    _codex_client = sdk_clients.openai_client(
                        api_key="chatgpt-oauth-dummy",
                        base_url="https://chatgpt.com/backend-api/codex",
                        http_client=sdk_clients.httpx_client(transport=_CodexTransport()),
                    )
    return _codex_client


def _get_chat_codex_client():
    """Returns the same singleton as _get_codex_client() -” same endpoint."""
    return _get_codex_client()


# ------------------------------------------------------------------
# Config sanity checks -” raise early with actionable messages
# ------------------------------------------------------------------

def _log_model_route(kind: str, provider: str, model: str, use_tools: bool = False) -> None:
    import time

    ts = time.strftime("%H:%M:%S")
    tool_note = " tools=on" if use_tools else ""
    print(
        f"[llm {ts}] Route ({kind}): provider={provider or '[unset]'} "
        f"model={model or '[unset]'} credential={_credential_source_for_provider(provider)}{tool_note}"
    )

# Ceiling used when a caller asks for "no app cap" (max_tokens == 0) on a
# provider that, unlike the OpenAI-compatible APIs, requires an explicit limit.
_ANTHROPIC_UNCAPPED_MAX_TOKENS = 8192

# All providers that go through the OpenAI-compatible chat completions API.
_OPENAI_COMPAT_PROVIDER_SET = frozenset({
    "groq", "openai", "google",
    "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "ollama",
    "custom",
})

_OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str, str]] = {
    # provider → (api_key_attr, base_url)
    "groq":       ("GROQ_API_KEY",       "https://api.groq.com/openai/v1"),
    "google":     ("GOOGLE_API_KEY",     _GOOGLE_OPENAI_BASE_URL),
    "deepseek":   ("DEEPSEEK_API_KEY",   _DEEPSEEK_BASE_URL),
    "openrouter": ("OPENROUTER_API_KEY", _OPENROUTER_BASE_URL),
    "mistral":    ("MISTRAL_API_KEY",    _MISTRAL_BASE_URL),
    "xai":        ("XAI_API_KEY",        _XAI_BASE_URL),
    "together":   ("TOGETHER_API_KEY",   _TOGETHER_BASE_URL),
    "cerebras":   ("CEREBRAS_API_KEY",   _CEREBRAS_BASE_URL),
    "ollama":     (None,                 _OLLAMA_BASE_URL),
}


def _openai_compat_base_url(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "custom":
        return config.CUSTOM_BASE_URL
    item = _OPENAI_COMPAT_PROVIDERS.get(provider)
    return item[1] if item else ""


def _openai_compat_api_key(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider == "openai":
        return config.OPENAI_API_KEY
    if provider == "custom":
        return config.CUSTOM_API_KEY or "no-key"
    item = _OPENAI_COMPAT_PROVIDERS.get(provider)
    if not item:
        return ""
    key_attr, _base_url = item
    return getattr(config, key_attr) if key_attr else "ollama"


def _use_macos_openai_compat_non_streaming(provider: str) -> bool:
    """Avoid macOS native crashes in OpenAI-compatible streaming paths."""
    return not macos_safety.openai_compat_streaming_enabled(provider)


def _openai_compat_message_text(response) -> str:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    if choice is None:
        return ""
    message = getattr(choice, "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or ""))
        return "".join(parts).strip()
    return str(content or "").strip()


def _openai_compat_stdlib_completion_text(provider: str, kwargs: dict) -> str:
    """Blocking OpenAI-compatible completion without importing provider SDKs."""
    import gzip
    import json as _json
    import urllib.error
    import urllib.request

    def _request_completion() -> str:
        base_url = _openai_compat_base_url(provider).rstrip("/")
        if not base_url:
            raise ValueError(f"No OpenAI-compatible base URL configured for {provider!r}")
        api_key = _openai_compat_api_key(provider)
        url = f"{base_url}/chat/completions"
        body = _json.dumps(kwargs).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key and provider != "ollama":
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=60) as response:
                raw = response.read()
                encoding = response.headers.get("Content-Encoding", "")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            encoding = exc.headers.get("Content-Encoding", "") if exc.headers else ""
            if "gzip" in encoding.lower() and raw:
                raw = gzip.decompress(raw)
            message = raw.decode("utf-8", errors="replace") if raw else str(exc)
            raise RuntimeError(f"Error code: {exc.code} - {message}") from exc
        if "gzip" in encoding.lower() and raw:
            raw = gzip.decompress(raw)
        payload = _json.loads(raw.decode("utf-8"))
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(str(getattr(item, "text", "") or ""))
            return "".join(parts).strip()
        return str(content).strip()

    with native_init_lock():
        return _request_completion()


# Per-provider cache for the route/fallback clients. Building these on every
# query rebuilt an SSL context each time; worse, the build raced with the TTS
# client's SSL build (separate thread, same query) and segfaulted on macOS.
# Cache + serialize construction so the SSL context is built once, never
# concurrently. Cleared by reset_clients() when settings change.
_dynamic_openai_clients: dict[str, object] = {}
_dynamic_anthropic_client_cache = None
_dynamic_client_lock = _threading.Lock()


def _build_dynamic_openai_client(provider: str):
    # max_retries=1: allow one quick same-model retry for a transient blip, but
    # not the SDK default (2) which slowly re-hammers an exhausted model every
    # turn with backoff before the fallback chain (which switches models) is
    # ever reached. Quota cooldown (see _stream_with_fallbacks) handles the rest.
    if provider in _OPENAI_COMPAT_PROVIDERS:
        key_attr, base_url = _OPENAI_COMPAT_PROVIDERS[provider]
        api_key = getattr(config, key_attr) if key_attr else "ollama"
        return sdk_clients.openai_client(api_key=api_key or "no-key", base_url=base_url, max_retries=_OPENAI_MAX_RETRIES)
    if provider == "custom":
        return sdk_clients.openai_client(api_key=config.CUSTOM_API_KEY or "no-key", base_url=config.CUSTOM_BASE_URL, max_retries=_OPENAI_MAX_RETRIES)
    return sdk_clients.openai_client(api_key=config.OPENAI_API_KEY, max_retries=_OPENAI_MAX_RETRIES)


def _dynamic_openai_client(provider: str):
    with _dynamic_client_lock:
        client = _dynamic_openai_clients.get(provider)
        if client is None:
            with ssl_init_lock():
                client = _build_dynamic_openai_client(provider)
            _dynamic_openai_clients[provider] = client
        return client


def _dynamic_anthropic_client():
    global _dynamic_anthropic_client_cache
    with _dynamic_client_lock:
        if _dynamic_anthropic_client_cache is None:
            with ssl_init_lock():
                _dynamic_anthropic_client_cache = sdk_clients.anthropic_client(api_key=config.ANTHROPIC_API_KEY)
        return _dynamic_anthropic_client_cache


def prewarm() -> None:
    """Build the primary query-route client once at startup.

    Runs sequentially with tts.prewarm() on the startup thread, so the LLM and
    TTS SSL contexts are built one after another here instead of racing on the
    first query (the macOS segfault). Also removes the handshake from the first
    query. Best-effort: a missing key or unreachable provider must not crash
    startup — the real query path handles those.
    """
    try:
        provider = (config.LLM_PROVIDER or "").strip().lower()
        if provider == "anthropic":
            _dynamic_anthropic_client()
        elif provider in _OPENAI_COMPAT_PROVIDER_SET:
            _dynamic_openai_client(provider)
        # chatgpt/copilot use the Codex transport, which builds no SSL context here.
    except Exception:
        pass


def list_models(provider: str, *, api_key: str = "", base_url: str = "") -> list[str]:
    """Return available model ids for *provider*, fetched live from its API.

    Raises on any failure — missing credential, network error, or a provider
    that has no public listing endpoint (chatgpt/copilot). Callers should treat
    a raise as "fall back to the curated list". Optional ``api_key``/``base_url``
    let the settings dialog fetch with not-yet-saved field values.
    """
    provider = (provider or "").lower()
    if provider in ("chatgpt", "copilot"):
        raise NotImplementedError(f"{provider} does not support model listing")

    if provider == "anthropic":
        key = api_key or config.ANTHROPIC_API_KEY
        if not key:
            raise ValueError("No Anthropic API key configured")
        with ssl_init_lock():
            client = sdk_clients.anthropic_client(api_key=key)
        resp = client.models.list(limit=1000)
        ids = [m.id for m in resp.data]
    else:
        if provider == "custom":
            url = base_url or config.CUSTOM_BASE_URL
            if not url:
                raise ValueError("No custom base URL configured")
            with ssl_init_lock():
                client = sdk_clients.openai_client(api_key=api_key or config.CUSTOM_API_KEY or "no-key", base_url=url)
        elif provider in _OPENAI_COMPAT_PROVIDERS:
            key_attr, default_base = _OPENAI_COMPAT_PROVIDERS[provider]
            key = api_key or (getattr(config, key_attr) if key_attr else "ollama")
            if key_attr and not key:
                raise ValueError(f"No API key configured for {provider}")
            with ssl_init_lock():
                client = sdk_clients.openai_client(api_key=key or "no-key", base_url=base_url or default_base)
        elif provider == "openai":
            key = api_key or config.OPENAI_API_KEY
            if not key:
                raise ValueError("No OpenAI API key configured")
            with ssl_init_lock():
                client = sdk_clients.openai_client(api_key=key)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        resp = client.models.list()
        ids = [_normalize_model_for_provider(provider, m.id) for m in resp.data]

    if not ids:
        raise ValueError("Provider returned no models")
    return sorted(ids)


_CHATGPT_SUPPORTED_MODELS = {
    "gpt-5.5", "gpt-5.4", "gpt-5.4-mini",
    "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2",
}


def _check_llm_config() -> None:
    if not config.LLM_MODEL:
        raise ValueError(
            "LLM_MODEL is not set in .env. "
            "Add LLM_MODEL=<model> (e.g. llama3-8b-8192 for Groq)."
        )
    if config.LLM_PROVIDER.lower() == "chatgpt":
        from core.auth import chatgpt as chatgpt_auth
        if not chatgpt_auth.get_tokens():
            raise ValueError(
                "LLM_PROVIDER is set to 'chatgpt' but you are not logged in. "
                "Open Settings -> LLM and sign in with your ChatGPT account."
            )
        if config.LLM_MODEL.lower() not in _CHATGPT_SUPPORTED_MODELS:
            raise ValueError(
                f"Model '{config.LLM_MODEL}' is not supported by the ChatGPT Codex endpoint. "
                f"Use one of: {', '.join(sorted(_CHATGPT_SUPPORTED_MODELS))}"
            )
        return
    if config.LLM_PROVIDER.lower() == "copilot":
        from core.auth import copilot_auth
        if not copilot_auth.get_token():
            raise ValueError(
                "LLM_PROVIDER is set to 'copilot' but no GitHub Copilot token is stored. "
                "Open Settings -> LLM and save a GitHub Copilot token."
            )
        return
    if config.LLM_PROVIDER.lower() == "custom" and not config.CUSTOM_BASE_URL:
        raise ValueError(
            "LLM_PROVIDER is set to 'custom' but CUSTOM_BASE_URL is not configured. "
            "Add the base URL in Settings → LLM → Custom provider."
        )
    if config.LLM_PROVIDER.lower() == "ollama":
        return
    if not _api_key_for(config.LLM_PROVIDER):
        raise ValueError(
            f"API key for LLM_PROVIDER='{config.LLM_PROVIDER}' is not configured. "
            "Add the matching key in Settings so it can be stored in the OS keychain."
        )


def _check_chat_llm_config() -> None:
    if not config.CHAT_LLM_MODEL:
        raise ValueError(
            "CHAT_LLM_MODEL is not set in .env. "
            "Add CHAT_LLM_MODEL=<model> or leave unset to inherit LLM_MODEL."
        )
    if config.CHAT_LLM_PROVIDER.lower() == "chatgpt":
        from core.auth import chatgpt as chatgpt_auth
        if not chatgpt_auth.get_tokens():
            raise ValueError(
                "CHAT_LLM_PROVIDER is set to 'chatgpt' but you are not logged in. "
                "Open Settings -> LLM and sign in with your ChatGPT account."
            )
        if config.CHAT_LLM_MODEL.lower() not in _CHATGPT_SUPPORTED_MODELS:
            raise ValueError(
                f"Model '{config.CHAT_LLM_MODEL}' is not supported by the ChatGPT Codex endpoint. "
                f"Use one of: {', '.join(sorted(_CHATGPT_SUPPORTED_MODELS))}"
            )
        return
    if config.CHAT_LLM_PROVIDER.lower() == "copilot":
        from core.auth import copilot_auth
        if not copilot_auth.get_token():
            raise ValueError(
                "CHAT_LLM_PROVIDER is set to 'copilot' but no GitHub Copilot token is stored. "
                "Open Settings -> LLM and save a GitHub Copilot token."
            )
        return
    if config.CHAT_LLM_PROVIDER.lower() == "custom" and not config.CUSTOM_BASE_URL:
        raise ValueError(
            "CHAT_LLM_PROVIDER is set to 'custom' but CUSTOM_BASE_URL is not configured. "
            "Add the base URL in Settings → LLM → Custom provider."
        )
    if config.CHAT_LLM_PROVIDER.lower() == "ollama":
        return
    if not _api_key_for(config.CHAT_LLM_PROVIDER):
        raise ValueError(
            f"API key for CHAT_LLM_PROVIDER='{config.CHAT_LLM_PROVIDER}' is not configured. "
            "Add the matching key in Settings so it can be stored in the OS keychain."
        )


def _check_vision_config() -> None:
    if not config.VISION_LLM_MODEL:
        raise ValueError(
            "VISION_LLM_MODEL is not set in .env. "
            "Screen-snip queries require a vision-capable model. "
            "Example: VISION_LLM_PROVIDER=anthropic  VISION_LLM_MODEL=claude-opus-4-5"
        )
    if not config.VISION_LLM_PROVIDER:
        raise ValueError(
            "VISION_LLM_PROVIDER is not set in .env. "
            "Add VISION_LLM_PROVIDER=anthropic (or openai or chatgpt) alongside VISION_LLM_MODEL."
        )
    if config.VISION_LLM_PROVIDER.lower() == "chatgpt":
        from core.auth import chatgpt as chatgpt_auth
        if not chatgpt_auth.get_tokens():
            raise ValueError(
                "VISION_LLM_PROVIDER is set to 'chatgpt' but you are not logged in. "
                "Open Settings -> LLM and sign in with your ChatGPT account."
            )
        return
    if config.VISION_LLM_PROVIDER.lower() == "custom" and not config.CUSTOM_BASE_URL:
        raise ValueError(
            "VISION_LLM_PROVIDER is set to 'custom' but CUSTOM_BASE_URL is not configured. "
            "Add the base URL in Settings → LLM → Custom provider."
        )
    if config.VISION_LLM_PROVIDER.lower() == "ollama":
        return
    if not _api_key_for(config.VISION_LLM_PROVIDER):
        raise ValueError(
            f"API key for VISION_LLM_PROVIDER='{config.VISION_LLM_PROVIDER}' is not configured. "
            "Add the matching key in Settings so it can be stored in the OS keychain."
        )


def _check_route_config(provider: str, model: str, route_name: str) -> None:
    if not provider or not model:
        raise ValueError(f"{route_name} route is missing provider or model.")
    if provider == "chatgpt":
        from core.auth import chatgpt as chatgpt_auth
        if not chatgpt_auth.get_tokens():
            raise ValueError(f"{route_name} route uses chatgpt but you are not logged in.")
        if model.lower() not in _CHATGPT_SUPPORTED_MODELS:
            raise ValueError(
                f"Model '{model}' is not supported by the ChatGPT Codex endpoint. "
                f"Use one of: {', '.join(sorted(_CHATGPT_SUPPORTED_MODELS))}"
            )
        return
    if provider == "copilot":
        from core.auth import copilot_auth
        if not copilot_auth.get_token():
            raise ValueError(f"{route_name} route uses copilot but no GitHub Copilot token is stored.")
        return
    if provider == "custom":
        if not config.CUSTOM_BASE_URL:
            raise ValueError(f"{route_name} route uses 'custom' but CUSTOM_BASE_URL is not set.")
        return
    if provider == "ollama":
        return   # local, no key required
    if not _api_key_for(provider):
        raise ValueError(f"{route_name} route uses {provider!r}, but its API key is not configured.")


def _check_route_config_with_credentials(
    provider: str,
    model: str,
    route_name: str,
    *,
    anthropic_api_key: str = "",
    custom_base_url: str = "",
    compat_keys: dict[str, str] | None = None,
) -> None:
    if not provider or not model:
        raise ValueError(f"{route_name} route is missing provider or model.")
    if provider == "chatgpt":
        from core.auth import chatgpt as chatgpt_auth
        if not chatgpt_auth.get_tokens():
            raise ValueError(f"{route_name} route uses chatgpt but you are not logged in.")
        if model.lower() not in _CHATGPT_SUPPORTED_MODELS:
            raise ValueError(
                f"Model '{model}' is not supported by the ChatGPT Codex endpoint. "
                f"Use one of: {', '.join(sorted(_CHATGPT_SUPPORTED_MODELS))}"
            )
        return
    if provider == "copilot":
        from core.auth import copilot_auth
        if not copilot_auth.get_token():
            raise ValueError(f"{route_name} route uses copilot but no GitHub Copilot token is stored.")
        return
    if provider == "custom":
        if not (custom_base_url or config.CUSTOM_BASE_URL):
            raise ValueError(f"{route_name} route uses 'custom' but CUSTOM_BASE_URL is not set.")
        return
    if provider == "ollama":
        return   # no key required
    if provider == "anthropic":
        if not anthropic_api_key:
            raise ValueError(f"{route_name} route uses anthropic, but its API key is not configured.")
        return
    if provider in _OPENAI_COMPAT_PROVIDER_SET:
        if compat_keys and not compat_keys.get(provider, ""):
            raise ValueError(f"{route_name} route uses {provider!r}, but its API key is not configured.")
        return
    if not _api_key_for(provider):
        raise ValueError(f"{route_name} route uses {provider!r}, but its API key is not configured.")


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        with ssl_init_lock():
            if config.LLM_PROVIDER.lower() == "groq":
                _openai_client = sdk_clients.openai_client(
                    api_key=config.GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                )
            else:
                _openai_client = sdk_clients.openai_client(api_key=config.OPENAI_API_KEY)
    return _openai_client


def _get_chat_openai_client():
    """Returns the same singleton as _get_openai_client() when providers match."""
    if config.CHAT_LLM_PROVIDER.lower() == config.LLM_PROVIDER.lower():
        return _get_openai_client()
    global _chat_openai_client
    if _chat_openai_client is None:
        with ssl_init_lock():
            if config.CHAT_LLM_PROVIDER.lower() == "groq":
                _chat_openai_client = sdk_clients.openai_client(
                    api_key=config.GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                )
            else:
                _chat_openai_client = sdk_clients.openai_client(api_key=config.OPENAI_API_KEY)
    return _chat_openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        with ssl_init_lock():
            _anthropic_client = sdk_clients.anthropic_client(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_chat_anthropic_client():
    if config.CHAT_LLM_PROVIDER.lower() == config.LLM_PROVIDER.lower():
        return _get_anthropic_client()
    global _chat_anthropic_client
    if _chat_anthropic_client is None:
        with ssl_init_lock():
            _chat_anthropic_client = sdk_clients.anthropic_client(api_key=config.ANTHROPIC_API_KEY)
    return _chat_anthropic_client


def _get_vision_openai_client():
    global _vision_openai_client
    if _vision_openai_client is None:
        with ssl_init_lock():
            if config.VISION_LLM_PROVIDER.lower() == "groq":
                _vision_openai_client = sdk_clients.openai_client(
                    api_key=config.GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                )
            elif config.VISION_LLM_PROVIDER.lower() == "google":
                _vision_openai_client = sdk_clients.openai_client(
                    api_key=config.GOOGLE_API_KEY,
                    base_url=_GOOGLE_OPENAI_BASE_URL,
                )
            else:
                _vision_openai_client = sdk_clients.openai_client(api_key=config.OPENAI_API_KEY)
    return _vision_openai_client


def _get_vision_anthropic_client():
    global _vision_anthropic_client
    if _vision_anthropic_client is None:
        with ssl_init_lock():
            _vision_anthropic_client = sdk_clients.anthropic_client(api_key=config.ANTHROPIC_API_KEY)
    return _vision_anthropic_client


def _run_openai_compat_probe(client, *, provider: str, model: str, messages: list) -> None:
    kwargs = {
        "model": model,
        "messages": messages,
        "stream": not _use_macos_openai_compat_non_streaming(provider),
        "max_tokens": 8,
    }
    if not kwargs["stream"]:
        client.chat.completions.create(**kwargs)
        return
    with client.chat.completions.create(**kwargs) as stream:
        for _chunk in stream:
            break


def _probe_openai_compat_route(provider: str, model: str, image_base64: str | None = None) -> None:
    client = _dynamic_openai_client(provider)
    _run_openai_compat_probe(
        client,
        provider=provider,
        model=_normalize_model_for_provider(provider, model),
        messages=_build_openai_messages("Reply with OK.", image_base64),
    )


def _probe_openai_compat_route_with_credentials(
    provider: str,
    model: str,
    *,
    api_key: str,
    base_url: str = "",
    image_base64: str | None = None,
) -> None:
    with ssl_init_lock():
        if provider == "groq":
            client = sdk_clients.openai_client(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        elif provider == "google":
            client = sdk_clients.openai_client(api_key=api_key, base_url=_GOOGLE_OPENAI_BASE_URL)
        elif provider == "custom":
            client = sdk_clients.openai_client(api_key=api_key or "no-key", base_url=base_url or config.CUSTOM_BASE_URL)
        else:
            client = sdk_clients.openai_client(api_key=api_key)
    _run_openai_compat_probe(
        client,
        provider=provider,
        model=_normalize_model_for_provider(provider, model),
        messages=_build_openai_messages("Reply with OK.", image_base64),
    )


def _probe_anthropic_route(model: str, image_base64: str | None = None) -> None:
    client = _dynamic_anthropic_client()
    if image_base64:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64,
                },
            },
            {"type": "text", "text": "Reply with OK."},
        ]
    else:
        content = "Reply with OK."
    client.messages.create(
        model=model,
        max_tokens=8,
        messages=[{"role": "user", "content": content}],
    )


def _probe_anthropic_route_with_api_key(model: str, api_key: str, image_base64: str | None = None) -> None:
    with ssl_init_lock():
        client = sdk_clients.anthropic_client(api_key=api_key)
    if image_base64:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64,
                },
            },
            {"type": "text", "text": "Reply with OK."},
        ]
    else:
        content = "Reply with OK."
    client.messages.create(
        model=model,
        max_tokens=8,
        messages=[{"role": "user", "content": content}],
    )


def _probe_chatgpt_route(model: str, image_base64: str | None = None) -> None:
    client = _get_codex_client()
    if image_base64:
        content = [
            {"type": "input_text", "text": "Reply with OK."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{image_base64}"},
        ]
    else:
        content = [{"type": "input_text", "text": "Reply with OK."}]
    client.responses.create(
        model=model,
        input=[{"type": "message", "role": "user", "content": content}],
        instructions="Return exactly OK.",
        store=False,
        max_output_tokens=8,
    )


def _probe_copilot_route(model: str) -> None:
    from core.auth import copilot_client

    text = copilot_client.ask(
        "Reply with OK.",
        model,
        system="Return exactly OK.",
        allow_tools=False,
    )
    if not text.strip():
        raise RuntimeError("Copilot returned an empty response.")


def test_route_connection(
    provider: str,
    model: str,
    route_name: str = "LLM",
    *,
    image: bool = False,
    anthropic_api_key: str | None = None,
    custom_base_url: str | None = None,
    compat_keys: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Test a provider/model route.

    compat_keys: map of provider name → API key for OpenAI-compat providers,
                 used when the dialog passes freshly-typed (not-yet-saved) keys.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    try:
        explicit_credentials = bool(compat_keys or anthropic_api_key is not None or custom_base_url is not None)
        if explicit_credentials:
            _check_route_config_with_credentials(
                provider,
                model,
                route_name,
                anthropic_api_key=anthropic_api_key or "",
                custom_base_url=custom_base_url or "",
                compat_keys=compat_keys or {},
            )
        else:
            _check_route_config(provider, model, route_name)

        def _probe_compat(image_b64=None):
            if explicit_credentials and compat_keys and provider in compat_keys:
                _probe_openai_compat_route_with_credentials(
                    provider, model,
                    api_key=compat_keys.get(provider, ""),
                    base_url=custom_base_url or "",
                    image_base64=image_b64,
                )
            else:
                _probe_openai_compat_route(provider, model, image_base64=image_b64)

        if image:
            image_base64 = _TEST_IMAGE_BASE64
            if provider in _OPENAI_COMPAT_PROVIDER_SET:
                _probe_compat(image_base64)
            elif provider == "anthropic":
                if explicit_credentials:
                    _probe_anthropic_route_with_api_key(model, anthropic_api_key or "", image_base64=image_base64)
                else:
                    _probe_anthropic_route(model, image_base64=image_base64)
            elif provider == "chatgpt":
                _probe_chatgpt_route(model, image_base64=image_base64)
            else:
                raise ValueError(f"Unknown vision provider: {provider}")
            return True, f"{route_name} vision route OK: {provider} / {model}"

        if provider in _OPENAI_COMPAT_PROVIDER_SET:
            _probe_compat()
        elif provider == "anthropic":
            if explicit_credentials:
                _probe_anthropic_route_with_api_key(model, anthropic_api_key or "")
            else:
                _probe_anthropic_route(model)
        elif provider == "chatgpt":
            _probe_chatgpt_route(model)
        elif provider == "copilot":
            _probe_copilot_route(model)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")
        return True, f"{route_name} route OK: {provider} / {model}"
    except Exception as exc:
        return False, f"{route_name} test failed: {exc}"


def stream_response(
    user_message: str,
    image_base64: str | None = None,
    ambient_context: str = "",
    memory_context: str = "",
    use_tools: bool = False,
    allow_screenshot_tool: bool = False,
    route_provider: str | None = None,
    route_model: str | None = None,
    route_fallbacks: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    json_mode: bool = False,
) -> Generator[str, None, None]:
    """
    Stream a response from the configured LLM.

    When image_base64 is provided, uses VISION_LLM_PROVIDER/MODEL.
    Otherwise uses LLM_PROVIDER/MODEL.

    Args:
        user_message:     The user's query text.
        image_base64:     Optional base64-encoded PNG for vision input.
        ambient_context:  Plain-text context block (active window, clipboard,
                          focused element) -” injected into system prompt.
        memory_context:   Pre-formatted LTM facts + STM session summary from
                          core.memory -” injected into system prompt before the
                          ambient context block.
        use_tools:        If True and provider is Anthropic, expose
                  web_search + get_context tools so Claude can
                  pull extra context when it decides to. The model
                  must use the actual tool call interface rather than
                  describing or simulating tool calls in text.
                  Ignored for Groq/OpenAI providers and vision calls.
        allow_screenshot_tool: If True (Anthropic, non-vision), additionally
                  expose the capture_screen tool so the model can grab a
                  screenshot on demand. Independent of use_tools.
        route_provider:   Optional provider override for non-vision calls.
        route_model:      Optional model override for non-vision calls.
        route_fallbacks:  Optional provider:model fallback string for overrides.
        max_tokens:       Optional response budget override for callers that
                          need structured or longer output.
        temperature:      Optional sampling override for callers that need
                          deterministic structured output.

    Yields:
        Text chunks as they arrive from the API.
    """
    import time
    ts = time.strftime("%H:%M:%S")
    if image_base64:
        print(f"[llm {ts}] User message (vision): {user_message!r}")
    else:
        print(f"[llm {ts}] User message: {user_message!r}")

    if ambient_context:
        _log_context(
            "ambient snapshot -” captured at hotkey/voice trigger, injected into system prompt",
            ambient_context,
        )

    if image_base64:
        candidates = _route_candidates(
            config.VISION_LLM_PROVIDER,
            config.VISION_LLM_MODEL,
            config.VISION_LLM_FALLBACKS,
        )
        yield from _stream_with_fallbacks(
            "vision",
            candidates,
            lambda provider, model: _stream_single_response_route(
                provider,
                model,
                user_message,
                image_base64,
                ambient_context,
                memory_context,
                use_tools=False,
                route_name="VISION_LLM",
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )
    else:
        provider = (route_provider or config.LLM_PROVIDER).strip()
        model = (route_model or config.LLM_MODEL).strip()
        fallback_raw = config.LLM_FALLBACKS if route_fallbacks is None else route_fallbacks
        candidates = _route_candidates(provider, model, fallback_raw)
        yield from _stream_with_fallbacks(
            "query",
            candidates,
            lambda provider, model: _stream_single_response_route(
                provider,
                model,
                user_message,
                None,
                ambient_context,
                memory_context,
                use_tools=use_tools,
                allow_screenshot_tool=allow_screenshot_tool,
                route_name="LLM",
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=json_mode,
            ),
        )


def _stream_with_fallbacks(
    kind: str,
    candidates: list[tuple[str, str]],
    factory,
) -> Generator[str, None, None]:
    import time
    # Try routes not in quota cooldown first; keep cooling ones as last resort so
    # a fully-throttled chain still attempts something rather than failing cold.
    ready = [c for c in candidates if not _is_route_cooling(*c)]
    cooling = [c for c in candidates if _is_route_cooling(*c)]
    ordered = ready + cooling
    if not ordered:
        ordered = candidates
    last_exc: Exception | None = None
    for idx, (provider, model) in enumerate(ordered):
        emitted = False
        try:
            for chunk in factory(provider, model):
                emitted = True
                yield chunk
            if emitted:
                return
            # Model returned HTTP 200 but zero content chunks — treat as failure.
            ts = time.strftime("%H:%M:%S")
            last_exc = ValueError(f"Route ({kind}) {provider}/{model} returned no content")
            if idx < len(ordered) - 1:
                print(f"[llm {ts}] Route ({kind}) returned no content; trying fallback")
                continue
            print(f"[llm {ts}] Route ({kind}) returned no content; no fallback left")
        except Exception as exc:
            last_exc = exc
            ts = time.strftime("%H:%M:%S")
            if emitted:
                print(f"[llm {ts}] Route ({kind}) failed after streaming; not falling back: {exc}")
                raise
            if not _is_route_cooling(provider, model) and _is_quota_error(exc):
                _mark_route_cooling(provider, model)
                print(f"[llm {ts}] Route ({kind}) {provider}/{model} hit quota; cooling down {_ROUTE_COOLDOWN_SECONDS:.0f}s and using fallback")
            if idx < len(ordered) - 1:
                print(f"[llm {ts}] Route ({kind}) failed before output; trying fallback: {exc}")
                continue
            print(f"[llm {ts}] Route ({kind}) failed; no fallback left: {exc}")
    if last_exc:
        raise last_exc
    raise ValueError(f"No {kind} model routes configured.")


def _stream_single_response_route(
    provider: str,
    model: str,
    user_message: str,
    image_base64: str | None,
    ambient_context: str,
    memory_context: str,
    use_tools: bool,
    route_name: str,
    allow_screenshot_tool: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
    json_mode: bool = False,
) -> Generator[str, None, None]:
    _check_route_config(provider, model, route_name)
    model = _normalize_model_for_provider(provider, model)
    # capture_screen also needs the Anthropic tool loop, so it counts as "tools".
    anthropic_tools = use_tools or allow_screenshot_tool
    # Use the user's model for tool calls. TOOL_LLM_MODEL is an optional override
    # (empty by default) for users who want a different model when tools are active
    # — we no longer force a hardcoded model.
    effective_model = (
        config.TOOL_LLM_MODEL
        if (provider == "anthropic" and anthropic_tools and config.TOOL_LLM_MODEL.strip())
        else model
    )
    tools_for_log = anthropic_tools and not image_base64
    if provider in _OPENAI_COMPAT_PROVIDER_SET and not macos_safety.openai_compat_tools_enabled():
        tools_for_log = False
    _log_model_route("vision" if image_base64 else "query", provider, effective_model, use_tools=tools_for_log)
    if image_base64:
        if provider in _OPENAI_COMPAT_PROVIDER_SET:
            client = None if _use_macos_openai_compat_non_streaming(provider) else _dynamic_openai_client(provider)
            yield from _stream_openai_compat(
                user_message,
                image_base64,
                model,
                client,
                provider=provider,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        elif provider == "anthropic":
            yield from _stream_anthropic(
                user_message,
                image_base64,
                model,
                _dynamic_anthropic_client(),
                max_tokens=max_tokens,
                temperature=temperature,
            )
        elif provider == "chatgpt":
            yield from _stream_codex_vision(user_message, image_base64, model, _get_codex_client())
        else:
            raise ValueError(f"Unknown vision provider: {provider}")
        return
    if provider in _OPENAI_COMPAT_PROVIDER_SET:
        client = None if _use_macos_openai_compat_non_streaming(provider) else _dynamic_openai_client(provider)
        yield from _stream_openai_compat(
            user_message,
            None,
            model,
            client,
            ambient_context,
            memory_context,
            use_tools=use_tools,
            allow_screenshot_tool=allow_screenshot_tool,
            provider=provider,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
    elif provider == "anthropic":
        yield from _stream_anthropic(
            user_message,
            None,
            effective_model,
            _dynamic_anthropic_client(),
            ambient_context,
            memory_context,
            use_tools,
            allow_screenshot_tool=allow_screenshot_tool,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    elif provider == "chatgpt":
        yield from _stream_codex(user_message, model, _get_codex_client(), ambient_context, memory_context, use_tools)
    elif provider == "copilot":
        yield from _stream_copilot(user_message, model, ambient_context, memory_context, use_tools)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def _stream_copilot(
    user_message: str,
    model: str,
    ambient_context: str = "",
    memory_context: str = "",
    use_tools: bool = False,
) -> Generator[str, None, None]:
    from core.auth import copilot_client

    parts = []
    if memory_context:
        parts.append(memory_context)
    if ambient_context:
        parts.append("Context:\n" + ambient_context)
    system = "\n\n".join(parts)
    yield from copilot_client.stream(
        user_message,
        model,
        system=system,
        allow_tools=use_tools,
    )


# ------------------------------------------------------------------
# OpenAI / Groq (OpenAI-compatible)
# ------------------------------------------------------------------

def _stream_openai_compat(
    user_message: str,
    image_base64: str | None,
    model: str,
    client,
    ambient_context: str = "",
    memory_context: str = "",
    use_tools: bool = False,
    allow_screenshot_tool: bool = False,
    provider: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
    json_mode: bool = False,
) -> Generator[str, None, None]:
    import json as _json

    messages = _build_openai_messages(user_message, image_base64, ambient_context, memory_context)
    tools_requested = (use_tools or allow_screenshot_tool) and not image_base64 and not json_mode
    tools_allowed = macos_safety.openai_compat_tools_enabled()
    if tools_requested and not tools_allowed:
        print("[llm] macOS safe mode: OpenAI-compatible live tools disabled", flush=True)
    expose_screenshot = tools_allowed and allow_screenshot_tool and not image_base64 and not json_mode
    if expose_screenshot and messages and messages[0].get("role") == "system":
        messages[0]["content"] = _with_screenshot_note(messages[0]["content"], True)
    # JSON mode (used by the agent protocol) forces a single JSON object as the
    # whole response. Native context tools would let the model emit a function
    # call instead of that JSON, so they are mutually exclusive: the agent embeds
    # its own tool calls inside the protocol JSON and does not need them here.
    tools = (
        _get_openai_tool_schemas(
            user_message,
            include_general=use_tools,
            include_screenshot=allow_screenshot_tool,
        )
        if tools_requested and tools_allowed
        else None
    )

    kwargs: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.5 if temperature is None else temperature,
    }
    # max_tokens == 0 means "no app-imposed cap": omit the field so the provider
    # uses its own per-model maximum. None means "unspecified" -> a safe default.
    if max_tokens != 0:
        kwargs["max_tokens"] = max_tokens or 2048
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    if _use_macos_openai_compat_non_streaming(provider):
        # OpenAI-compatible streaming was the crash source in macOS logs for a
        # plain "hi" prompt. Keep the same UI path by yielding the final text as
        # one chunk, and keep live tool loops opt-in while this path is validated.
        kwargs["stream"] = False
        kwargs.pop("tools", None)
        print("[llm] macOS OpenAI-compatible route using non-streaming safe mode", flush=True)
        text = _openai_compat_stdlib_completion_text(provider, kwargs)
        if text:
            yield text
        return

    # Stream first round — yield text, accumulate any tool-call deltas.
    tool_calls_acc: dict[int, dict] = {}  # index → {id, name, arguments}
    finish_reason = None

    with client.chat.completions.create(**kwargs) as stream:
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            if delta.content:
                yield delta.content
            for tc in (delta.tool_calls or []):
                entry = tool_calls_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    entry["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        entry["name"] += tc.function.name
                    if tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

    if finish_reason != "tool_calls" or not tool_calls_acc:
        return

    # Build the assistant tool-call turn and append it to messages.
    assistant_tool_calls = [
        {"id": tc["id"], "type": "function",
         "function": {"name": tc["name"], "arguments": tc["arguments"]}}
        for tc in tool_calls_acc.values()
    ]
    messages.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})

    # Tool-call loop (up to 3 rounds, non-streaming for follow-ups).
    current_model = model
    vision_mode = False
    for _round in range(3):
        pending_image_b64: str | None = None
        for tc in tool_calls_acc.values():
            try:
                inputs = _json.loads(tc["arguments"] or "{}")
            except Exception:
                inputs = {}
            if tc["name"] == "capture_screen":
                # OpenAI tool messages can't carry an image, so acknowledge the
                # call with text and deliver the screenshot as a user message
                # below, then answer on a vision-capable model.
                pending_image_b64 = _capture_screen_b64()
                ack = (
                    "Screenshot captured; it is attached in the next message."
                    if pending_image_b64
                    else "Screen capture failed; no image is available."
                )
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": ack})
                _log_context("tool: capture_screen", "<screenshot>" if pending_image_b64 else "<failed>")
            else:
                result = _execute_model_tool(tc["name"], inputs)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        if pending_image_b64:
            current_model = _openai_vision_model(provider, current_model)
            vision_mode = True  # answer with the image; drop tools for follow-ups
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here is the screenshot you requested."},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{pending_image_b64}"}},
                ],
            })

        follow_up_kwargs = {
            "model": current_model,
            "messages": messages,
            "temperature": 0.5 if temperature is None else temperature,
            "stream": False,
        }
        if max_tokens != 0:
            follow_up_kwargs["max_tokens"] = max_tokens or 2048
        if tools and not vision_mode:
            follow_up_kwargs["tools"] = tools
        follow_up = client.chat.completions.create(**follow_up_kwargs)
        choice = follow_up.choices[0]
        text = choice.message.content or ""
        if text:
            yield text
        if choice.finish_reason != "tool_calls":
            return
        # Another tool round — update accumulator and append assistant turn.
        tool_calls_acc = {}
        if choice.message.tool_calls:
            for i, tc in enumerate(choice.message.tool_calls):
                tool_calls_acc[i] = {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "",
                }
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls_acc.values()
                ],
            })


# ------------------------------------------------------------------
# Codex (ChatGPT subscription) -” Responses API streaming
# ------------------------------------------------------------------

def _build_codex_text(user_message: str, ambient_context: str = "", memory_context: str = "") -> str:
    """Prepend context into a single text block for the Codex endpoint."""
    parts = []
    if memory_context:
        parts.append(memory_context)
    if ambient_context:
        parts.append(f"---\n{ambient_context}")
    parts.append(user_message)
    return "\n\n".join(parts)


def _stream_codex(
    user_message: str,
    model: str,
    client,
    ambient_context: str = "",
    memory_context: str = "",
    use_tools: bool = False,
) -> Generator[str, None, None]:
    """Stream a response via the Codex endpoint using the Responses API."""
    # The Responses API tool-call loop (previous_response_id chaining) is not
    # implemented here.  When tools are requested we eagerly inject context that
    # Claude would otherwise fetch on demand: open supported documents and clipboard.
    if use_tools:
        doc_text = read_active_document_for_context()
        if doc_text:
            doc_block = f"[Open documents]\n{doc_text}"
            ambient_context = f"{ambient_context}\n\n---\n{doc_block}".strip() if ambient_context else doc_block
    text = _build_codex_text(user_message, ambient_context, memory_context)
    with client.responses.stream(
        model=model,
        input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}],
        instructions=config.get_system_prompt(),
        store=False,
    ) as stream:
        for event in stream:
            if getattr(event, 'type', '') == 'response.output_text.delta':
                delta = getattr(event, 'delta', '')
                if delta:
                    yield delta


def _stream_codex_vision(
    user_message: str,
    image_base64: str,
    model: str,
    client,
) -> Generator[str, None, None]:
    """Stream a vision response via the Codex endpoint (Responses API with image input)."""
    input_content = [
        {"type": "input_text",  "text": user_message},
        {"type": "input_image", "image_url": f"data:image/png;base64,{image_base64}"},
    ]
    with client.responses.stream(
        model=model,
        input=[{"type": "message", "role": "user", "content": input_content}],
        instructions=config.get_system_prompt(),
        store=False,
    ) as stream:
        for event in stream:
            if getattr(event, 'type', '') == 'response.output_text.delta':
                delta = getattr(event, 'delta', '')
                if delta:
                    yield delta


def _build_openai_messages(
    user_message: str,
    image_base64: str | None,
    ambient_context: str = "",
    memory_context: str = "",
) -> list:
    system = config.get_system_prompt()
    if memory_context:
        system += f"\n\n{memory_context}"
    if ambient_context:
        system += f"\n\n---\n{ambient_context}"
    if image_base64:
        content = [
            {"type": "text", "text": user_message},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
    else:
        content = user_message

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


# ------------------------------------------------------------------
# Anthropic Claude  -”  shared tool-loop helper
# ------------------------------------------------------------------

def _run_anthropic_tool_loop(
    client,
    messages: list,
    first_response,
    model: str,
    system: str,
    max_tokens: int,
    prompt: str = "",
    include_general: bool = True,
    include_screenshot: bool = False,
) -> Generator[str, None, None]:
    """
    Execute Anthropic tool calls and yield text from subsequent rounds.
    *messages* is mutated in place (assistant + tool-result turns are appended).
    """
    messages.append({"role": "assistant", "content": first_response.content})
    final = first_response
    current_model = model
    for _round in range(3):
        tool_results = []
        for block in final.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            if block.name == "capture_screen":
                # Return the screenshot as an image block; upgrade the loop to a
                # vision-capable model for the rounds that follow.
                b64 = _capture_screen_b64()
                if b64:
                    current_model = _anthropic_vision_model(current_model)
                    content = [{
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    }]
                else:
                    content = "Screen capture failed; no image is available."
                _log_context("tool: capture_screen", "<screenshot>" if b64 else "<failed>")
            else:
                content = _execute_model_tool(block.name, block.input or {})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content,
            })
        if not tool_results:
            return
        messages.append({"role": "user", "content": tool_results})
        response = client.messages.create(
            model=current_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=_get_tool_schemas(
                prompt,
                include_general=include_general,
                include_screenshot=include_screenshot,
            ),
        )
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text = getattr(block, "text", "")
                if text:
                    yield text
        if response.stop_reason != "tool_use":
            return
        final = response
        messages.append({"role": "assistant", "content": response.content})


def _stream_anthropic(
    user_message: str,
    image_base64: str | None,
    model: str,
    client,
    ambient_context: str = "",
    memory_context: str = "",
    use_tools: bool = False,
    allow_screenshot_tool: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> Generator[str, None, None]:
    system = _with_screenshot_note(config.get_system_prompt(), allow_screenshot_tool)
    if memory_context:
        system += f"\n\n{memory_context}"
    if ambient_context:
        system += f"\n\n---\n{ambient_context}"

    tools_active = use_tools or allow_screenshot_tool

    # Anthropic requires an explicit max_tokens, so "no app cap" (0) maps to a
    # generous per-request ceiling rather than being truly unlimited.
    anthropic_max_tokens = _ANTHROPIC_UNCAPPED_MAX_TOKENS if max_tokens == 0 else (max_tokens or 2048)
    anthropic_tool_max_tokens = _ANTHROPIC_UNCAPPED_MAX_TOKENS if max_tokens == 0 else (max_tokens or 512)

    if image_base64:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64,
                },
            },
            {"type": "text", "text": user_message},
        ]
    else:
        content = user_message

    # --- No tools: original streaming path (lowest latency) ---
    if not tools_active:
        request = {
            "model": model,
            "max_tokens": anthropic_max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        if temperature is not None:
            request["temperature"] = temperature
        with client.messages.stream(**request) as stream:
            for text in stream.text_stream:
                yield text
        return

    # --- Tool-enabled path: stream first round for fast first-token ---
    # If no tool is called (common case), text streams immediately.
    # Only falls back to blocking create() if Claude actually invokes a tool.
    messages: list[dict] = [{"role": "user", "content": content}]

    request = {
        "model": model,
        "max_tokens": anthropic_max_tokens,
        "system": system,
        "messages": messages,
        "tools": _get_tool_schemas(
            user_message,
            include_general=use_tools,
            include_screenshot=allow_screenshot_tool,
        ),
    }
    if temperature is not None:
        request["temperature"] = temperature
    with client.messages.stream(**request) as stream:
        for text in stream.text_stream:
            yield text
        final = stream.get_final_message()

    if final.stop_reason != "tool_use":
        return

    # A tool was called -” execute it and do followup round(s) non-streaming.
    yield from _run_anthropic_tool_loop(
        client,
        messages,
        final,
        model,
        system,
        max_tokens=anthropic_tool_max_tokens,
        prompt=user_message,
        include_general=use_tools,
        include_screenshot=allow_screenshot_tool,
    )


# ------------------------------------------------------------------
# Inline rewrite / fix  (Ctrl+Shift+Q)
# ------------------------------------------------------------------

_REWRITE_SYSTEM_PROMPT = (
    "You are a text editor assistant. "
    "Rewrite or fix the provided text. "
    "Output ONLY the corrected/rewritten text -” no explanation, "
    "no markdown, no commentary, no code fences. "
    "Your entire response will be pasted directly as a replacement for the original text."
)


def stream_rewrite(selected_text: str, intent_prompt: str = "Rewrite or fix the following text") -> Generator[str, None, None]:
    """
    Stream a rewrite/fix of the selected text using the primary LLM.

    The system prompt instructs the model to output raw replacement text only -”
    no prose, no markdown, no explanation.  The result is pasted back directly.

    Args:
        selected_text:  The text to rewrite.
        intent_prompt:  The instruction (e.g. "Fix the grammar and spelling").
                        Taken from the caller's chosen intent row.
    """
    import time
    ts = time.strftime("%H:%M:%S")
    print(f"[llm {ts}] Rewrite request ({len(selected_text)} chars) -” {intent_prompt[:60]!r}")
    user_message = f"{intent_prompt}:\n\n{selected_text}"
    candidates = _route_candidates(config.LLM_PROVIDER, config.LLM_MODEL, config.LLM_FALLBACKS)
    yield from _stream_with_fallbacks(
        "rewrite",
        candidates,
        lambda provider, model: _stream_single_rewrite_route(provider, model, user_message),
    )


def _stream_single_rewrite_route(provider: str, model: str, user_message: str) -> Generator[str, None, None]:
    _check_route_config(provider, model, "LLM")
    _log_model_route("rewrite", provider, model, use_tools=False)
    if provider in _OPENAI_COMPAT_PROVIDER_SET:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            "stream": not _use_macos_openai_compat_non_streaming(provider),
            "max_tokens": 1024,
            "temperature": 0.3,
        }
        if not kwargs["stream"]:
            print("[llm] macOS OpenAI-compatible rewrite using non-streaming safe mode", flush=True)
            text = _openai_compat_stdlib_completion_text(provider, kwargs)
            if text:
                yield text
            return
        client = _dynamic_openai_client(provider)
        with client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
    elif provider == "anthropic":
        client = _dynamic_anthropic_client()
        with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=_REWRITE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            temperature=0.3,
        ) as stream:
            for text in stream.text_stream:
                yield text
    elif provider == "copilot":
        from core.auth import copilot_client
        yield from copilot_client.stream(
            user_message,
            model,
            system=_REWRITE_SYSTEM_PROMPT,
        )
    elif provider == "chatgpt":
        with _get_codex_client().responses.stream(
            model=model,
            input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": user_message}]}],
            instructions=_REWRITE_SYSTEM_PROMPT,
            store=False,
        ) as stream:
            for event in stream:
                if getattr(event, 'type', '') == 'response.output_text.delta':
                    delta = getattr(event, 'delta', '')
                    if delta:
                        yield delta
    else:
        raise ValueError(f"Unknown rewrite provider: {provider}")


# ------------------------------------------------------------------
# Multi-turn (chat window)
# ------------------------------------------------------------------

def stream_response_with_history(
    messages: list,
    memory_context: str = "",
) -> Generator[str, None, None]:
    """
    Stream a response given a pre-built messages list including history.
    Uses CHAT_LLM_PROVIDER / CHAT_LLM_MODEL (defaults to LLM_PROVIDER / LLM_MODEL).

    Args:
        messages:        [{{"role": "system"|"user"|"assistant", "content": str}}, ...]
        memory_context:  Pre-formatted LTM facts from core.memory -” appended to
                         the system message so the model is aware of user facts.
    """
    # Inject memory context into the system message (or prepend one)
    if memory_context:
        sys_idx = next(
            (i for i, m in enumerate(messages) if m["role"] == "system"), None
        )
        if sys_idx is not None:
            messages = list(messages)   # shallow copy -” don't mutate the caller's list
            messages[sys_idx] = {
                **messages[sys_idx],
                "content": messages[sys_idx]["content"] + f"\n\n{memory_context}",
            }
        else:
            messages = [{"role": "system", "content": memory_context}] + list(messages)
    candidates = _route_candidates(
        config.CHAT_LLM_PROVIDER,
        config.CHAT_LLM_MODEL,
        config.CHAT_LLM_FALLBACKS,
    )
    yield from _stream_with_fallbacks(
        "chat",
        candidates,
        lambda provider, model: _stream_single_history_route(provider, model, messages),
    )


def _stream_single_history_route(provider: str, model: str, messages: list) -> Generator[str, None, None]:
    _check_route_config(provider, model, "CHAT_LLM")
    _log_model_route("chat", provider, model, use_tools=(provider == "anthropic"))
    if provider in _OPENAI_COMPAT_PROVIDER_SET:
        kwargs = {
            "model": model,
            "messages": messages,
            "stream": not _use_macos_openai_compat_non_streaming(provider),
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        if not kwargs["stream"]:
            print("[llm] macOS OpenAI-compatible chat using non-streaming safe mode", flush=True)
            text = _openai_compat_stdlib_completion_text(provider, kwargs)
            if text:
                yield text
            return
        client = _dynamic_openai_client(provider)
        with client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
    elif provider == "anthropic":
        client = _dynamic_anthropic_client()
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        _VALID_KEYS = {"role", "content"}
        turns = [
            {k: v for k, v in m.items() if k in _VALID_KEYS}
            for m in messages if m["role"] != "system"
        ]
        # Tool-enabled loop -” stream first round, block only if a tool is actually called.
        _last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if isinstance(_last_user, list):
            _last_user = " ".join(p.get("text", "") for p in _last_user if isinstance(p, dict))
        with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system,
            messages=turns,
            tools=_get_tool_schemas(_last_user),
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()

        if final.stop_reason != "tool_use":
            return

        yield from _run_anthropic_tool_loop(client, turns, final, model, system, max_tokens=1024)
    elif provider == "chatgpt":
        # For the chat history path, extract the last user message and use Responses API.
        # The full history is packed into a single user turn with prior turns prefixed.
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        turns = [m for m in messages if m["role"] != "system"]
        # Build a text representation of prior context for the model
        last_user = turns[-1]["content"] if turns else ""
        history_prefix = ""
        for m in turns[:-1]:
            label = "User" if m["role"] == "user" else "Assistant"
            history_prefix += f"{label}: {m['content']}\n"
        full_input = (history_prefix + last_user) if history_prefix else last_user
        with _get_chat_codex_client().responses.stream(
            model=model,
            input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": full_input}]}],
            instructions=system_msg,
            store=False,
        ) as stream:
            for event in stream:
                if getattr(event, 'type', '') == 'response.output_text.delta':
                    delta = getattr(event, 'delta', '')
                    if delta:
                        yield delta
    elif provider == "copilot":
        from core.auth import copilot_client

        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        turns = [m for m in messages if m["role"] != "system"]
        full_input = ""
        for m in turns:
            label = "User" if m["role"] == "user" else "Assistant"
            full_input += f"{label}: {m['content']}\n"
        yield from copilot_client.stream(
            full_input.strip(),
            model,
            system=system_msg,
            session_id="wisp-chat",
        )
    else:
        raise ValueError(f"Unknown chat LLM provider: {provider}")

