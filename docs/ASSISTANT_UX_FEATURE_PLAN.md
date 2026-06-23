# Assistant UX Feature Plan

This plan covers the next user-trust and recoverability work for Wisp. It is
intentionally scoped to features 1, 2, 3, 6, 7, and 8 from the product notes.

## Current Build Status

Implemented in the current build:

- Settings exposes a lightweight setup check that avoids importing provider
  SDKs, audio stacks, or STT.
- The tray/menu no longer exposes the old live health status path.
- Voice and dictation can show transcript candidates when confirmation is
  enabled.
- Privacy reports show redaction counts and safe category summaries.
- Error recommendations are shared by setup/status surfaces.
- Setup, privacy, voice, and warning strings are covered by the Qt
  locale catalogs, including Traditional Chinese.

Still planned:

- A fuller first-run calibration wizard with persisted completion state.
- Shared context preview chips for intent overlay and chat.
- Optional richer STT alternatives beyond the current cheap transcript cleanup
  candidates.

## Goals

- Make setup confidence visible instead of implicit.
- Make runtime health and failures understandable from the overlay.
- Make model-bound context inspectable before sending.
- Make voice input correctable before it changes a field or fires a query.
- Make privacy redaction visible as "detected and censored" evidence.
- Keep every new user-visible string covered by all Qt locale files:
  `ui/locales/qt/wisp_es.ts`, `ui/locales/qt/wisp_fr.ts`,
  `ui/locales/qt/wisp_zh.ts`, and `ui/locales/qt/wisp_zh-Hant.ts`.

## 1. First-Run Calibration

Entry point:

- Settings button exposes a reusable setup-check entry point.
- The old right-click menu item is not the setup-check entry point.
- Current build: the entry point opens a lightweight setup/status dialog.
- Future calibration work can add a fuller wizard and store completion state
  with a setting such as `WISP_SETUP_COMPLETED=true` so Wisp can suggest the
  check once while still keeping it available.

Checks:

- Current setup check verifies the configured LLM route, optional TTS/STT
  configuration, hotkeys, and privacy redaction without live SDK/audio imports.
- Health status separately probes whether the LLM primary route responds.
- Health status checks whether STT is ready or will warm up on first use.
- Health status checks TTS only when a TTS provider is enabled.
- Global hotkeys are registered.
- Context capture permissions work.
- Screenshot/snip permissions work.

Tests:

- Settings exposes the setup check button without importing audio/STT stacks.
- Setup check reports pass, fail, and optional-off states with recommendations.
- New labels and statuses are present in every locale file.

## 2. Status and Health Panel

Entry point:

- Settings exposes a lightweight "Run setup check" action.
- Opening it shows a compact setup window.
- Important problems can also show as a small dismissible notice near the Wisp icon.

Setup window content:

- Hotkeys active/inactive.
- Current LLM provider and model.
- TTS provider status.
- STT configured, skipped, or failed.
- Context sources enabled.
- Configuration problems and recommendations.

Near-icon notices:

- No LLM provider configured.
- Hotkeys failed or conflict.
- Microphone permission missing.
- Screenshot permission missing.
- TTS returned no audio.

Dismissal behavior:

- Dismissed notices stay quiet until the status changes or the app restarts.

Tests:

- Right-click menu does not expose the setup check.
- Setup window renders degraded states without crashing.
- Near-icon notices are dismissible and reappear when status changes.
- All setup strings are translated.

## 3. Context Preview

Surfaces:

- Implement in the intent overlay.
- Implement in the chat window.
- Use a shared context-preview data model so both surfaces agree.

Preview content:

- Selected text detected.
- Clipboard text detected when enabled.
- Browser/page context detected.
- Active document or dropped document context.
- Screenshot context.
- Memory count.
- Addon/tool context.
- Privacy redaction count when available.

User controls:

- Show a compact expandable preview before send.
- Allow removing context items where the current flow can safely support it.
- After response, optionally show a footer like "Used selection + browser tab +
  2 memories".

Tests:

- Intent overlay requests and renders context preview chips.
- Chat window requests and renders the same context preview shape.
- Disabled sources do not leak into prompts.
- Redaction counts appear when privacy redaction changes context.
- New preview strings are translated.

## 6. Voice Transcript Correction

Behavior:

- After voice capture, show a small candidate window near the typed target when
  native focus geometry is available.
- Fall back to placing the window near the Wisp icon.
- For assistant voice input, show the candidate window near the overlay or
  reply bubble.

Candidate actions:

- Use the top result with Enter.
- Click an alternate candidate.
- Edit manually.
- Cancel.

Candidate generation:

- Faster-whisper does not cheaply expose true top-N complete transcript
  alternatives in the current path.
- Extra STT alternatives would likely require slower extra decoding passes, so
  that should be an optional later setting.
- First implementation should show the raw transcript plus cheap cleanup
  variants, such as punctuated text and a command-shaped version when LLM
  cleanup mode is enabled.

Tests:

- Dictation does not paste until a candidate is accepted in confirm mode.
- Assistant voice query does not fire until a candidate is accepted in confirm
  mode.
- Cancel leaves the focused field unchanged.
- Candidate fallback placement works when native geometry is unavailable.
- Candidate popup strings are translated.

## 7. Privacy Detection and Censoring Report

Behavior:

- Privacy mode should show what Wisp detected and censored, not only that
  privacy is enabled.
- Redaction must happen before text is sent to a model.

Report content:

- Detected categories such as API keys, bearer tokens, passwords, emails, phone
  numbers, file paths, and other configured sensitive patterns.
- Redacted previews that do not reveal the full secret, such as
  `sk-...abcd`.
- Count of redacted items per context source.

Surfaces:

- Context preview shows a badge such as "3 items redacted".
- Clicking the badge opens the privacy report.
- Health/status can include privacy configuration warnings if redaction is off.

Settings:

- Enable or disable redaction.
- Strict mode.
- Category toggles where practical.

Tests:

- Prompt-bound context contains redacted values, not raw secrets.
- Privacy report shows detected categories and safe previews.
- Context preview surfaces the redaction count.
- Turning privacy off is explicit and visible.
- New privacy strings are translated.

## 8. Error Recommendations

Behavior:

- User-facing errors should include the recommendation directly after the message.
- Keep technical detail available for diagnostics, but do not make it the first
  thing users see.

Error shape:

- Message.
- Recommendation.
- Technical detail.
- Optional action button.

Initial recommendation coverage:

- Missing API key.
- Invalid provider or model.
- Provider timeout or network failure.
- TTS provider returned no audio.
- STT import, model download, or microphone failure.
- Hotkey registration conflict.
- Screenshot or screen-recording permission failure.
- Addon crash.

Tests:

- Common errors format as message plus recommendation.
- Health panel and near-icon notices use the same recommendation text.
- Secrets are redacted from technical details.
- New error strings and action labels are translated.

## Implementation Order

1. Shared context preview for intent overlay and chat window.
2. Full first-run calibration wizard and setup-completed state.
3. Optional richer STT alternative generation for transcript correction.

## Workflow Test Gate

The app workflow runner should keep this plan visible in its run summary, and
the workflow test suite should include a contract test that checks every planned
feature remains documented with tests and translation coverage.
