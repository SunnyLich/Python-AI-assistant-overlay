# App User Function Test Plan

This plan uses "function" in the product sense: the user-visible capabilities,
workflows, settings, and failure states of the app. It intentionally does not
inventory Python functions or methods.

The purpose of this plan is to build workflow tests that catch bugs a user would
notice: missing context, stale UI state, confusing model lists, settings that do
not persist, memory scoped to the wrong project, disabled options still taking
effect, and platform-specific behavior that only breaks on one OS.

## Starter Priorities

The first workflow coverage must focus on three areas:

1. Context in the intent overlay.
2. Context in the chat window.
3. Memory through a real conversation, project, and chat history path.
4. Broad settings coverage across all settings tabs and most individual
   controls.

These should not be tested only through helper functions. A passing test should
prove the user can see and operate the UI surface, and that the resulting
request uses the same state the UI showed.

## Test Harness Requirements

Use fakes only at external boundaries. Keep app state real where bugs commonly
hide.

- Use a real temporary conversation store for projects, conversations, message
  history, attachments, and active project selection.
- Use a real temporary memory store for add/search/list/update/delete and
  project scoping.
- Use fake model providers so tests are deterministic and do not call the
  network.
- Use fake native adapters for selected text, clipboard, active app, browser
  page, document text, screenshot metadata, paste, microphone, and OS identity.
- Use Qt offscreen for widget tests.
- Use an event recorder that captures visible labels, chip text, token labels,
  tooltips, logs, notifications, outgoing model payloads, and persisted state.
- Patch app paths to temporary directories for chats, attachments, projects,
  memory, add-ons, tool keyword files, and env/config files.
- Make all workflow tests independent of the real host OS permissions.

Recommended fixtures:

- `qapp_offscreen`: creates a QApplication in offscreen mode.
- `tmp_app_state`: temp roots for config, chats, projects, attachments, memory,
  add-ons, tools, and agent runs.
- `fake_native_context`: selected text, clipboard, active app/window,
  document text, browser URL/content, screen size, screenshot image, permission
  errors.
- `fake_brain`: deterministic streaming chunks, final answers, route/auth
  failures, memory tool calls, screenshot-aware replies.
- `fake_audio`: recording/transcription success and failure.
- `fake_addons`: contributed intents, tray actions, tools, hotkeys, settings,
  notifications, and permission failures.
- `event_recorder`: collects UI calls and user-visible state transitions.

## User Function Catalog

### 1. App Launch And Lifecycle

- Launch the app from the platform entry point.
- Acquire the single-instance lock or hand off to an existing instance.
- Start UI, hotkey, context, audio, memory, add-on, and model-routing workers.
- Show the tray/floating icon in the expected initial state.
- Recover gracefully when credentials, optional dependencies, or permissions are
  missing.
- Log startup, runtime failures, and shutdown in a user-debuggable way.
- Quit cleanly from the tray/menu.

### 2. Tray, Icon, And Windows

- Show, hide, drag, and reposition the floating icon.
- Open chat, settings, memory viewer, add-on manager, and auto-agent task
  window.
- Show response bubble state: idle, thinking, streaming, speaking, done, error.
- Auto-hide and restore the icon when configured.
- Keep window on-top behavior consistent with platform expectations.

### 3. Intent Overlay

- Open from caller hotkey.
- Display project and conversation selector.
- Create/select a project.
- Start a new conversation or continue an existing one.
- Display intent rows from settings and add-ons.
- Enter a custom prompt.
- Show context chips for App, Browser/Web, Selection, Clipboard, Screenshot,
  Memory, and Files.
- Cycle context chips by mouse and configured numeric keys.
- Show token estimates and warnings before submit.
- Cancel without sending.
- Submit selected intent/custom prompt with the chosen context policy.

### 4. Chat Window

- Open from tray/icon or after a hotkey query.
- Create, select, rename, delete, pin, branch, and rewind conversations where
  supported.
- Create/select projects and filter conversation choices by project.
- Attach files, text, and images by picker or drag/drop.
- Display pending attachment labels and persisted attachment summaries.
- Display per-conversation context chips for App, Browser/Web, Selection,
  Clipboard, Screenshot, Memory, and Files.
- Refresh context preview/token estimates before send.
- Persist context policy per conversation.
- Send a message and stream a response.
- Preserve history, hidden context, file context, tool context, project id, and
  attachment references across reload.

### 5. Context Capture

- Capture active app/window metadata.
- Capture selected text.
- Capture clipboard text.
- Capture open document text.
- Capture browser URL and page text.
- Capture local git/GitHub context when enabled.
- Capture screenshot at hotkey time.
- Offer screenshot capture as a model tool when in model/auto mode.
- Capture buffered context added by hotkey.
- Capture dropped text/files/images.
- Explain unavailable, permission-denied, oversized, or deferred context.
- Keep token estimates visible even when a source is fetched later.

### 6. Model Response And Bubble

- Route to the configured model.
- Stream assistant text.
- Separate progress/thought text from final answer text.
- Render markdown/plain text safely.
- Keep bubble size independent from bubble text/font size.
- Stop, close, dismiss, and auto-hide the bubble.
- Play TTS when enabled and stop when requested.
- Save the exchange to chat/history where configured.
- Show route/auth/config errors in the UI.

### 7. Rewrite And Paste

- Capture selected text.
- Apply the chosen rewrite intent.
- Paste into the original focused app.
- Fall back to clipboard when direct paste is unavailable.
- Detect focus changes before paste.
- Restore clipboard where configured.
- Show clear failure states for missing selection or non-editable target.

### 8. Screenshot And Snipping

- Open snipping overlay.
- Select a region.
- Cancel selection.
- Attach the region to the prompt.
- Estimate image token cost.
- Route image input to the vision route.
- Handle permission denied, empty capture, and multi-monitor geometry.

### 9. Voice And Dictation

- Hold voice hotkey to record.
- Show recording state.
- Stop on release.
- Transcribe through configured STT.
- Send voice prompt through normal query flow.
- Hold dictation hotkey to type transcript into focused field.
- Apply raw or LLM-cleanup dictation mode.
- Handle too-short recording, mic failure, and transcription failure.

### 10. Memory

- Save explicit user memories.
- Let the model save memory through the memory tool.
- Retrieve memory automatically when memory context is on.
- Offer memory search/save tools when memory is in model/auto mode.
- Scope memories to the active conversation project by default.
- Promote memories to general scope only when requested.
- Search/list/add/update/delete memory in the memory viewer.
- Reject low-value or private facts.
- Preserve project isolation across chat and intent overlay workflows.

### 11. Settings

- Configure app appearance, language, privacy, icon, chat elaborate behavior,
  bubble geometry, bubble font/text size, scrolling, colors, and theme.
- Configure model credentials, provider rows, model rows, fallbacks, custom
  endpoint, auth status, and test buttons.
- Configure TTS provider and provider-specific voice/API fields.
- Configure STT model, device, compute type, language, and beam size.
- Configure voice and dictation hotkeys.
- Configure caller hotkeys, caller labels, paste-back behavior, context modes,
  file access, allowed tools, intent rows, and custom prompt rows.
- Configure other hotkeys, intent context toggle keys, overlay timeout, and
  snip context.
- Configure system prompt.
- Configure tool keyword filters.
- Configure context limits, model file roots, blocked private globs, memory
  tuning, bubble timing, and TTS speed.
- Apply, reset page, reset all, search settings, show dirty state, validate
  conflicts, and persist reload.

### 12. Add-Ons

- Discover installed add-ons.
- Enable/disable add-ons.
- Show add-on settings.
- Run contributed tray actions.
- Show contributed intents.
- Use contributed tools.
- Register/unregister contributed hotkeys.
- Show contributed notifications.
- Enforce add-on permissions.
- Log add-on failures without breaking host workflows.

### 13. Auto-Agent

- Open task window.
- Enter task and workspace/scope.
- Choose primary and fallback models using the same model catalog as settings.
- Start/cancel/pause/resume/nudge a run where supported.
- Show active agent, messages, tool calls, run logs, approvals, and final result.
- Handle tool call errors and verification commands.
- Handle non-git workspaces.
- Stop once on permanent route/auth failure.
- Preserve run history and support retry/continue where supported.

### 14. OS And Native Integration

- Register/unregister global hotkeys on Windows, macOS, and Linux.
- Request/report accessibility, automation, screen capture, microphone, and
  clipboard permissions.
- Capture selected text, clipboard, active app/window, browser/document context,
  screenshot, and paste on each OS through fake adapters.
- Restart/report helper process failure.

## Core Workflow Tests To Add First

### Real GPT 5.5 Integration Test

Some tests should use fakes so local and CI runs are fast, deterministic, and
free. But at least one opt-in test must call the real provider with the user's
current API key, because provider behavior, auth, streaming, unsupported
parameters, and model-specific quirks cannot be proven with fakes.

Real-provider test file:

- `tests/test_real_gpt55_integration.py`

Run command:

- `WISP_RUN_REAL_GPT55_TESTS=1 python -m pytest tests/test_real_gpt55_integration.py -q`
- PowerShell/Codex sandbox form:
  `$env:WISP_RUN_REAL_GPT55_TESTS='1'; python -m pytest tests\test_real_gpt55_integration.py -q -s -p no:cacheprovider --basetemp .tmp_pytest\real_gpt55_live`

Required behavior:

- Prefer provider `openai` when `OPENAI_API_KEY` is visible.
- Fall back to provider `chatgpt` when ChatGPT OAuth is the real credential
  visible to the app.
- Allow forcing either route with `WISP_REAL_GPT55_PROVIDER`.
- Use model `gpt-5.5` by default.
- Allow override with `WISP_REAL_GPT55_MODEL`.
- Read credentials from the existing app secret store, environment, or ChatGPT
  auth store.
- Patch memory and conversation stores to temp directories so the test does not
  mutate the user's real chats or memories.
- Use a real temporary project, conversation, and memory fact.
- Call the normal `brain.chat` streaming handler.
- Assert the real model answer proves project-scoped memory reached the prompt.
- Keep the token budget low enough for routine manual runs.
- Stay opt-in so normal `pytest` does not spend money.

### `test_intent_overlay_context_chips_drive_query_request`

This is the first intent-overlay context test. It must start from the user entry
point, not from a low-level context helper.

Scenario:

1. Create two projects and three conversations in the real temp conversation
   store.
2. Configure caller row 1 with context modes:
   - App/open docs on.
   - Browser/Web on.
   - Memory on.
   - Screenshot model/auto.
   - Files ask-before-write.
   - Selection and clipboard available.
3. Fake native context:
   - active app name/title/process,
   - selected text,
   - clipboard text,
   - active document text,
   - browser URL/content,
   - screen size and screenshot metadata.
4. Trigger the caller hotkey.
5. Open the intent overlay.
6. Assert the overlay shows project selector, conversation selector, intent
   rows, and seven context chips.
7. Toggle each chip with the configured `INTENT_CONTEXT_TOGGLE_KEYS`.
8. Select a non-default project.
9. Select "new chat", then select an existing conversation from that project.
10. Submit a preset intent and then a custom prompt in a separate subcase.

Assertions:

- Project selection filters conversation choices.
- `project_choice()` and `conversation_choice()` match the visible selector.
- `context_choices()` includes App, Browser/Web, Selection, Clipboard,
  Screenshot, Memory, and Files with the visible states.
- Token labels are visible before submit:
  - concrete labels for selected text, clipboard, browser text, document/app
    text, and screenshot size,
  - deferred labels for memory or sources fetched after submit.
- Screenshot estimate is present even before the screenshot chip is manually
  toggled on when screen metadata is available.
- The outgoing query payload includes selected text, clipboard, browser,
  active document/app context, screenshot/tool permission, memory flag, file
  access mode, allowed/pinned tools, context priority, project id, and
  conversation continuation choice.
- Disabled chips do not leak their source into the payload.
- Cancel sends no model request and leaves conversation history unchanged.

### `test_intent_overlay_context_failure_states_are_visible`

Scenario:

1. Open intent overlay with selected text available.
2. Simulate browser permission failure.
3. Simulate document text unavailable.
4. Simulate screenshot permission denied but screen size known.
5. Simulate memory enabled with no matching facts.
6. Submit a query.

Assertions:

- Working context still reaches the request.
- Unavailable sources show warning text/tooltips.
- Permission failures do not crash the overlay.
- Token labels use `? tok` only for truly deferred sources.
- No broken empty context block is sent.

### `test_chat_window_context_preview_and_send_policy`

This is the first chat-window context test. It must use a real conversation and
the chat composer controls.

Scenario:

1. Create a real project and conversation in the temp conversation store.
2. Open `ChatWindow` with that conversation selected.
3. Confirm the context chips render above the composer:
   - App,
   - Browser/Web,
   - Selection,
   - Clipboard,
   - Screenshot,
   - Memory,
   - Files.
4. Toggle each chip through its visible menu.
5. Verify `request_context_preview()` calls the preview callback with the
   conversation's current context policy.
6. Feed `update_context_preview()` with concrete preview rows and warnings.
7. Attach a text file, an image, and a path-only file reference.
8. Send a chat message through fake streaming `send_fn`.

Assertions:

- Each chip text contains the shortcut key, label, state, and token estimate.
- Each chip tooltip names the token estimate and warning.
- Context policy persists on the conversation after chip changes.
- Preview responses with stale `preview_id` are ignored.
- Attachment label appears before send and clears after send.
- The user message stores attachment context and attachment refs.
- The model messages include:
  - `[Current Chat Context]` for selected chat context,
  - `[Attached context for this message]` for message attachments,
  - active file/tool context from prior turns when present.
- The `send_fn` receives `context_policy`.
- The assistant reply is persisted after streaming completes.
- Switching conversations and returning preserves chip state.

### `test_chat_window_context_disabled_sources_do_not_leak`

Scenario:

1. Start a real chat conversation with all context chips off.
2. Provide fake native selected text, clipboard, browser, document, screenshot,
   memory facts, and file roots.
3. Send a chat message.
4. Turn on only Selection and Memory.
5. Send another message.

Assertions:

- First request contains no current chat context and does not retrieve memory.
- Second request contains selected text and memory retrieval only.
- Browser/document/clipboard/screenshot/file context does not appear while off.
- Conversation history still persists both turns.

### `test_memory_with_real_conversation_project_scope`

This is the first memory test. It must use a real conversation and real memory
store, with only the LLM response faked.

Scenario:

1. Patch memory and conversation paths to temp directories.
2. Create projects `Project A` and `Project B`.
3. Open chat in `Project A`.
4. Send a message equivalent to "remember that this project code name is
   Aurora".
5. Save the memory through the same path the app uses for a real conversation:
   either explicit memory command handling or a fake model `memory_save` tool
   call.
6. Send "What is this project's code name?" in the same conversation with Memory
   on.
7. Switch to `Project B` and ask the same question.
8. Save a general memory and ask from both projects.
9. Open memory viewer and update/delete a fact.

Assertions:

- The saved fact records `project=Project A` by default.
- The next chat request in `Project A` includes retrieved memory context.
- The same query in `Project B` does not retrieve the `Project A` fact.
- A general scoped memory is retrieved in both projects.
- Memory viewer list/add/update/delete changes the real temp memory store.
- Deleted facts no longer appear in retrieval or viewer.
- Low-value/private rejected facts are not stored and produce a visible status.

### `test_memory_through_intent_overlay_continues_selected_conversation`

Scenario:

1. Create a real project conversation with project-scoped memory.
2. Trigger intent overlay.
3. Select that project and continue that conversation.
4. Submit a memory-dependent prompt with Memory on.
5. Repeat with "new chat" in the same project.
6. Repeat with a different project.

Assertions:

- Continuing the conversation replays prior chat history.
- Memory retrieval is scoped to the selected conversation/project.
- New chat in the same project can use project memory but not old hidden
  conversation-only context.
- Different project does not see project-scoped facts.

## Settings Coverage Plan

Settings need more than a smoke test. The suite should exercise every tab,
every major control group, dependent visibility, save/reload, reset, search,
dirty state, and validation warnings.

### App Tab

Controls to cover:

- `THEME_MODE`
- theme colors: background, surface, text, accent
- `TRUST_PRIVACY_MODE`
- `ICON_AUTO_HIDE`
- `CHAT_AUTO_ELABORATE`
- `CHAT_ELABORATE_PROMPT`
- `APP_LANGUAGE`
- `ASSISTANT_LANGUAGE`
- `ICON_SIZE`
- `BUBBLE_WIDTH`
- `BUBBLE_LINES`
- `BUBBLE_FONT_SIZE`
- `BUBBLE_SCROLL_ENABLED`
- `BUBBLE_SCROLL_SNAP_ENABLED`
- `BUBBLE_COLOR`
- `BUBBLE_TEXT_COLOR`
- `BUBBLE_READ_WORD_COLOR`

Tests:

- Changing theme swaps visible color templates and persists per theme.
- Chat elaborate prompt is visible only when auto-elaborate is checked.
- Bubble width/lines/font size persist independently.
- Invalid numeric values show validation and do not corrupt config.
- Privacy mode redacts context in a request-level workflow.
- Language selection persists and updates visible labels after reload where
  supported.

### LLM Tab

Controls to cover:

- ChatGPT sign in/out status.
- GitHub OAuth sign in/out status, client id, scopes.
- Copilot token save/test/clear.
- API key rows: provider, alias, stored secret placeholder, add/remove row.
- Custom provider base URL, custom API key, presets, test custom.
- Chat model route rows: provider, model, fallback rows, add/remove row, test.
- Image model route rows: provider, model, fallback rows, add/remove row, test.
- Memory model route rows: provider, model, fallback rows, add/remove row, test.
- Apply-to-all route button.

Tests:

- Provider/model dropdown naming is identical in settings, chat, and auto-agent.
- Model combobox popup has opaque readable background on macOS style.
- Editable/saved custom model values survive reload.
- Fallback rows save and route in order.
- Apply-to-all copies provider/model/fallback rows without losing custom values.
- Secret fields store through the secret store and do not write raw keys to env.
- Auth/test failures show user-readable status messages.

### TTS / Voice Tab

Controls to cover:

- `TTS_PROVIDER`
- Cartesia API key and voice id.
- ElevenLabs API key, voice id, and model.
- OpenAI TTS voice and model.
- OpenAI-compatible TTS base URL, API key, voice, model, sample rate.
- Test TTS button/status.
- `STT_MODEL`
- `STT_DEVICE`
- `STT_COMPUTE_TYPE`
- `STT_LANGUAGE`
- `STT_BEAM_SIZE`
- STT backend recheck.
- Voice hotkey.
- Voice context controls: Ambient, Screenshot, Open docs, Git/GitHub,
  Browser/Web, Memory, Local files.
- Voice allowed-tools dialog.
- Dictation hotkey.
- `DICTATE_MODE`

Tests:

- Switching TTS provider shows only that provider's fields.
- TTS provider `none` hides provider-specific key/voice fields.
- Test TTS success and failure update visible status.
- STT language choices respond to model constraints.
- Voice context settings persist and affect a voice query payload.
- Dictation mode controls paste behavior in the dictation workflow.

### Keybinds Tab

Controls to cover:

- Caller blocks: hotkey, name, paste result back, remove caller.
- Per-caller context: Ambient, Screenshot, Open docs, Git/GitHub, Browser/Web,
  Memory, Local files.
- Per-caller allowed tools dialog.
- Per-caller intent rows: key, label, prompt, add/remove row.
- Per-caller custom prompt key, label, prompt.
- Voice and dictation hotkeys if they are shown on this tab in the current UI.
- `HOTKEY_ADD_CONTEXT`
- `HOTKEY_CLEAR_CONTEXT`
- `HOTKEY_SNIP`
- `INTENT_CONTEXT_TOGGLE_KEYS`
- `INTENT_OVERLAY_TIMEOUT_MS`
- `SNIP_CONTEXT_AMBIENT`
- `SNIP_CONTEXT_DOCUMENTS`
- `SNIP_CONTEXT_TOOLS`

Tests:

- Add/remove caller persists caller count and rows.
- Hotkey collision warning appears for duplicates.
- Caller context mode changes affect intent overlay default chip states.
- Paste-back caller routes to rewrite/paste workflow.
- Intent rows become visible choices in the overlay.
- Custom prompt row opens the overlay input field.
- Intent context toggle keys control both intent overlay and chat chip labels.
- Overlay timeout closes only after configured timeout, and `0` disables timeout.
- Snip context checkboxes affect screenshot/snipping request context.

### Prompts Tab

Controls to cover:

- `SYSTEM_PROMPT_UTILITY`

Tests:

- System prompt saves and reloads.
- Query/chat requests include the edited system prompt.
- Reset restores default prompt.

### Tools Tab

Controls to cover:

- Tool keyword filter fields for built-in and add-on tools.
- Save keyword filters.
- Per-caller/per-voice allowed-tools dialogs.

Tests:

- Empty keywords always include the tool when governing mode allows it.
- Non-empty keywords include tools only when prompt matches.
- Disabled tools do not appear in allowed/pinned tool payloads.
- Add-on tool keyword filters appear after add-on load and disappear after
  disable.

### Advanced Tab

Controls to cover:

- `CONTEXT_BROWSER_MAX_CHARS`
- `CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS`
- `CONTEXT_TOOL_DOCUMENT_MAX_CHARS`
- `TOOL_FILE_ROOTS`
- `TOOL_FILE_BLOCKED_GLOBS`
- `MEMORY_AUTO_CONSOLIDATE`
- `MEMORY_TOP_K`
- `MEMORY_CONSOLIDATION_INTERVAL`
- `MEMORY_STM_TOKEN_BUDGET`
- `BUBBLE_REVEAL_WPM`
- `BUBBLE_HOLD_REVEAL_WPM`
- `BUBBLE_HIDE_DELAY_S`
- `BUBBLE_SCROLL_SNAP_DELAY_S`
- `TTS_PLAYBACK_RATE`
- `TTS_HOLD_PLAYBACK_RATE`

Tests:

- Context char limits truncate browser/document/tool context at the expected
  visible boundary.
- File roots allow only configured paths.
- Blocked globs override allowed roots.
- Memory top-k changes retrieval count in a real conversation.
- Auto-consolidate toggle starts/stops consolidation scheduling without running
  background LLMs in the test.
- Bubble reveal/hide/snap timings affect bubble state with a fake clock.
- TTS playback rates are passed to audio playback.

### Settings-Wide Behavior

Tests:

- Search finds controls across all tabs.
- Dirty markers appear after edits and clear after apply.
- Cancel/close without apply leaves config unchanged.
- Apply writes expected keys and triggers app reload hooks.
- Reset current page resets only that page.
- Reset all resets settings while preserving user data only where intended.
- Secrets remain in keychain/secret store placeholders after reload.
- Legacy keys are migrated or ignored safely.
- Invalid values produce user-readable warnings and do not partly apply.

### Settings Must Prove Runtime Effect

Every settings workflow test should make three assertions:

1. The control changes in the Settings UI.
2. The value persists after Apply/reload.
3. A user workflow behaves differently because of the changed setting.

Do not stop at "the env/config key changed." A setting is only covered when a
downstream user-visible behavior or outgoing request proves it took effect.

Effect checks to add:

| Setting Area | Change In Settings | Runtime Proof |
| --- | --- | --- |
| Main model route | Change provider/model/fallback rows | Next overlay query and chat send use the selected provider/model route, and route failures mention that route |
| Vision model route | Change image model provider/model | Screenshot/snipping request routes through the vision model and accepts image input |
| Memory model route | Change memory model/fallback rows | Memory consolidation/test route uses the selected memory route, or reports that route on failure |
| API keys/auth | Save/clear key or sign in/out | Test buttons, route validation, and query/chat failures reflect the new auth state |
| Custom provider | Change base URL/API key/model | Test custom and a forced custom route use that endpoint |
| Theme/colors | Change theme and colors | Open chat/settings/overlay/bubble and assert visible palette/style changes |
| Privacy mode | Toggle `TRUST_PRIVACY_MODE` | Query/chat context redacts or preserves sensitive-looking text accordingly |
| Chat auto-elaborate | Toggle `CHAT_AUTO_ELABORATE` and prompt | Opening chat from a short bubble sends or does not send the elaborate prompt |
| Languages | Change app/assistant language | App labels reload in selected app language, and model request includes assistant language guidance when configured |
| Icon auto-hide/size | Change `ICON_AUTO_HIDE`/`ICON_SIZE` | Floating icon visibility and geometry change during idle/active states |
| Bubble size/font | Change width, lines, font size | Bubble dimensions and text point size change independently during a real streamed reply |
| Bubble scroll/snap | Toggle scroll/snap and delay | Wheel scroll works or is ignored, and snap timing follows fake clock |
| Bubble/TTS timing | Change reveal WPM/hide delay/TTS speed | Streaming/TTS playback timeline changes with a fake clock/audio recorder |
| TTS provider/voice | Change provider and voice fields | Test TTS and spoken reply use the selected provider fields; `none` produces no audio request |
| STT settings | Change model/device/compute/language/beam | Voice/dictation transcription path receives the selected STT settings |
| Voice hotkey | Change voice hotkey/context modes | Registered hotkey changes, and voice query payload uses the new context policy |
| Dictation hotkey/mode | Change dictation hotkey and raw/cleanup mode | Registered hotkey changes, and dictation either pastes raw transcript or calls cleanup route before paste |
| Caller hotkeys | Add/change/remove caller rows | Registered hotkeys and intent overlay rows update after Apply |
| Caller paste-back | Toggle paste result back | Same caller switches between response bubble/chat behavior and rewrite/paste behavior |
| Caller context modes | Change App/Browser/Git/Memory/Screenshot/Files modes | Intent overlay defaults and outgoing query payload include/exclude exactly those context/tool grants |
| Caller allowed tools | Change allowed-tools dialog | Query payload `allowed_tools`/`pinned_tools` and offered model tools reflect the selection |
| Intent rows | Add/remove/edit intent key/label/prompt | Overlay shows the new row and submits the edited prompt |
| Intent context keys | Change `INTENT_CONTEXT_TOGGLE_KEYS` | Overlay and chat chips show the new keys, and pressing them toggles the matching source |
| Overlay timeout | Change `INTENT_OVERLAY_TIMEOUT_MS` | Overlay auto-closes at the configured time, while `0` stays open |
| Snip context | Toggle snip Ambient/Open docs/Tools | Snip query includes/excludes the configured surrounding context |
| System prompt | Edit `SYSTEM_PROMPT_UTILITY` | Next query/chat system message contains the edited prompt |
| Tool keywords | Change keyword filters | Tool list offered to the model changes only when prompt keywords match |
| Context limits | Change browser/document/tool char limits | Captured context is truncated at the configured limits before model request |
| File roots | Change `TOOL_FILE_ROOTS` | File tools allow only the configured roots |
| Blocked globs | Change `TOOL_FILE_BLOCKED_GLOBS` | Matching files are refused even inside allowed roots |
| Memory auto-consolidate | Toggle auto consolidate | Consolidation timer starts/stops without affecting explicit memory saves |
| Memory top-k | Change `MEMORY_TOP_K` | Real conversation memory retrieval returns the configured number of relevant facts |
| Memory STM budget/interval | Change budget/interval | STM compression/consolidation scheduling thresholds change under fake clock |

## Missing Areas Found During Audit

The first draft covers the highest-risk visible surfaces, but it was still too
thin on cross-surface synchronization, stale runtime state, destructive-edge
workflows, and diagnostics. Add these workflows before calling the suite broad.

### `test_settings_change_reaches_every_surface`

Scenario:

1. Open Settings and change model labels/routes, context keys, caller defaults,
   theme, language, bubble font size, and tool/file permissions.
2. Apply without restarting.
3. Open intent overlay, chat window, response bubble, auto-agent task window,
   memory viewer, and add-on manager.
4. Restart the app harness and open those same surfaces again.

Assertions:

- Chat, intent overlay, auto-agent, and Settings use the same provider/model
  labels and no stale naming survives in any model list.
- Combo/list popups have an opaque readable background on macOS, Windows, and
  Linux style emulation.
- Changed context toggle keys appear in chat and intent overlay and actually
  toggle the matching source.
- Bubble size and bubble font size remain independent after reload.
- Theme/language changes update all visible surfaces without mixed old/new
  labels.
- Tool/file permission changes affect both normal chat/query and auto-agent
  tool availability.

### `test_real_gpt55_query_and_route_failures`

Scenario:

1. Use the same opt-in real-provider harness as
   `tests/test_real_gpt55_integration.py`.
2. Call the normal `brain.query`/intent-overlay path, not only `brain.chat`.
3. Include selected text, ambient context, memory, and a tiny prompt.
4. In a separate subcase, force an invalid fallback route or unsupported option
   with a fake route so the user-facing failure path is deterministic.
5. Optionally run a real screenshot/vision subcase only when the selected
   provider/model reports image support.

Assertions:

- The real response proves the selected/ambient context reached the prompt.
- The real route name and model name match the visible settings.
- Streaming chunks and final `done` payload are shaped like the UI expects.
- Route/auth/unsupported-parameter failures report the failing route clearly and
  do not leave the next real call poisoned by stale cooldown/client state.
- The test remains low-token and opt-in.

### `test_chat_history_management_workflow`

Scenario:

1. Create several projects and conversations through the chat UI.
2. Rename, pin, delete, branch, rewind, and continue conversations where those
   controls are available.
3. Ingest an externally-created conversation while the window is open.
4. Reload the conversation store.

Assertions:

- Project filters and pinned ordering are stable after reload.
- Delete/rewind never leaves the UI pointing at a missing message.
- Branch/continue preserves the expected prior turns and drops future turns.
- Hidden context, attachments, and project id stay attached to the correct
  conversation only.
- Empty project/conversation states are usable rather than blank or confusing.

### `test_context_buffer_drop_zone_and_priority_workflow`

Scenario:

1. Use the add-context hotkey to append selected text to the context buffer.
2. Add files, images, and raw text through the chat drop zone.
3. Remove one dropped item and clear the buffer.
4. Send queries from intent overlay and chat with different context priority
   orders.

Assertions:

- Buffered context appears as a visible source before send and disappears after
  clear.
- Drop-zone badges, counts, summaries, and token estimates update after remove
  and clear.
- Context order in the outgoing prompt follows configured priority:
  selection, buffered context, clipboard, files, active document/browser,
  screenshot, memory.
- Disabled or removed context never leaks into later requests.

### `test_stream_cancel_stale_state_and_recovery_workflow`

Scenario:

1. Start a streaming query with fake slow chunks and TTS.
2. While it streams, cancel, switch conversations, open Settings, or trigger a
   second hotkey/query.
3. Deliver late chunks from the old stream.
4. Start another query immediately.

Assertions:

- Late chunks from stale generation ids are ignored by bubble, chat, audio, and
  memory recording.
- Cancel stops TTS/filler audio and leaves hotkeys usable.
- Switching conversations cannot append the old answer to the new conversation.
- Error/cancel/done states restore composer buttons, overlay state, and icon
  visibility.

### `test_provider_fallback_cooldown_and_model_capability_workflow`

Scenario:

1. Configure primary and fallback model routes for chat, query, vision, memory,
   rewrite, and auto-agent.
2. Simulate primary failures: auth error, rate limit, unsupported tool call,
   unsupported image input, and rejected sampling/max-token parameter.
3. Send another request while a route is cooling down.

Assertions:

- The app retries only safe provider parameter changes.
- Fallback order, cooldown, and final exhaustion status are visible and logged.
- Screenshot/tool capability warnings match the selected provider/model before
  the user submits.
- The next unrelated surface does not reuse the wrong route or stale warning.

### `test_tool_file_permission_and_approval_workflow`

Scenario:

1. Configure allowed roots, blocked globs, read-only mode, and ask-before-write.
2. Ask chat/query and auto-agent to read, create, edit, patch, delete, and run
   verification commands.
3. Exercise the approval modal for file writes and shell commands.
4. Try path traversal, absolute path escapes, hidden blocked files, and oversized
   files.

Assertions:

- Allowed reads succeed and blocked paths return visible, structured errors.
- Write/delete/run actions block until approved and stop cleanly when denied.
- No tool can escape the configured workspace root.
- Live file events and diffs appear in chat/agent logs without leaking file
  contents beyond configured limits.

### `test_persistence_corruption_migration_and_factory_reset_workflow`

Scenario:

1. Start with legacy env keys, partial env writes, old conversation schema, old
   memory records, and corrupt JSON files.
2. Open Settings, chat, memory viewer, add-on manager, and auto-agent history.
3. Save settings, mutate state, then run factory reset where supported.

Assertions:

- Valid legacy settings migrate or map to the current UI.
- Corrupt user data is isolated with a clear notice; valid neighboring data
  still loads.
- Partial writes do not erase unrelated settings/secrets.
- Factory reset clears only intended app state and leaves unrelated files alone.

### `test_privacy_security_and_log_redaction_workflow`

Scenario:

1. Put API-key-shaped text, OAuth tokens, private file paths, and secret-looking
   clipboard text into selectable context sources.
2. Toggle privacy mode on and off.
3. Trigger chat, intent overlay, add-on hooks, tool calls, route failures, and
   diagnostic log views.

Assertions:

- Privacy mode redacts sensitive-looking context before prompt construction.
- Secrets never appear in visible logs, model payload records, notifications, or
  add-on hook payloads unless the user explicitly attached them as normal text
  with privacy disabled.
- Auth errors identify provider/route without printing tokens.
- Diagnostic exports are useful but redacted by default.

### `test_addon_install_update_remove_and_reload_workflow`

Scenario:

1. Install or discover a valid add-on package.
2. Enable it, change its settings, and use its tray action, intent, hook, hotkey,
   notification, and model-callable tool.
3. Update its manifest/version, reload add-ons, then disable and remove it.
4. Repeat with bad manifest, missing entry point, host crash, and permission
   denial.

Assertions:

- Contributions appear, update, and disappear without stale UI rows or tools.
- Add-on settings persist across reload and are removed only when requested.
- Host crashes are reported and do not break other add-ons.
- Permission denial is visible to the user and logged once.

### `test_auto_agent_history_meeting_artifacts_and_controls_workflow`

Scenario:

1. Start an auto-agent run with several roles and a temporary workspace.
2. Emit direct messages, meeting updates, communication-map changes, tool calls,
   approvals, file artifacts, diffs, verification results, pause/resume, cancel,
   retry, and continue-from-history events.
3. Open run history after restart.

Assertions:

- The live meeting view, communication map, and run log remain synchronized.
- Tool call errors are readable and associated with the right agent/step.
- Approvals pause the runner until the chosen response is returned.
- Artifacts/diffs/history entries survive restart and can create retry or
  continue specs.
- Cancel/pause/resume controls update both UI and runner state.

### `test_ui_accessibility_layout_and_platform_popups_workflow`

Scenario:

1. Run the main windows under compact desktop, wide desktop, high-DPI, and mobile
   scale-factor style constraints.
2. Use keyboard-only navigation for tray/menu alternatives, overlay rows,
   context chips, chat composer, settings tabs, memory viewer, and auto-agent.
3. Open every combo/list/menu popup on macOS style emulation.
4. Switch to long translated labels and long model names.

Assertions:

- Text fits inside buttons, chips, cards, and popups without overlap.
- Focus order and default buttons make the workflow usable without a mouse.
- Escape/Enter/arrow keys behave consistently in overlay, chat, and settings.
- Popup backgrounds are opaque/readable and not transparent on macOS.
- Long localized labels do not hide controls or break layout.

### `test_notifications_logs_and_worker_lifecycle_workflow`

Scenario:

1. Launch the supervisor harness and all workers.
2. Simulate native, UI, audio, and brain worker crash/restart paths.
3. Trigger user-visible notices from context failure, auth failure, add-on
   failure, tool denial, audio failure, and memory corruption.
4. Quit cleanly.

Assertions:

- Worker failures produce one clear notice/log and recover when recoverable.
- Non-recoverable failures disable only the affected feature.
- Startup/shutdown logs include enough route, OS, and worker state to debug.
- Single-instance handoff and clean quit do not orphan helpers or lock files.

### Test Isolation Guard

Every workflow test must prove it is not touching the user's real app state.
Add a shared assertion that:

- Settings/env, chats, projects, memory, attachments, add-ons, logs, and agent
  runs resolve under `tmp_app_state`.
- Real-provider tests patch stores to temp directories even when they use the
  real credential store.
- Tests that need host permissions are marked as real-host/manual and skipped by
  default.

## Additional Workflow Tests

### `test_query_response_bubble_text_size_and_tts`

Scenario:

1. Set bubble width, lines, and font size to distinct values.
2. Trigger a query with fake streaming chunks and thought/progress chunks.
3. Enable TTS and stream segments.
4. Scroll bubble manually.
5. Finish response.

Assertions:

- Width/line count change bubble geometry.
- Font size changes text size independently.
- Thought/progress styling is separate from answer text.
- TTS highlight does not alter chat transcript text.
- Bubble snap/hide timing follows settings.

### `test_rewrite_paste_workflow`

Scenario:

1. Fake selected text in editable app.
2. Trigger paste-back caller.
3. Stream rewritten text.
4. Paste succeeds.
5. Repeat with focus changed.
6. Repeat with direct paste failure and clipboard fallback.

Assertions:

- Request contains selected text and rewrite prompt.
- Paste target is verified before paste.
- Clipboard fallback is visible and restores clipboard when configured.
- Missing selection shows a user-visible notice.

### `test_screenshot_snip_workflow`

Scenario:

1. Trigger snip hotkey.
2. Select region.
3. Submit from overlay.
4. Repeat with cancel.
5. Repeat with permission denied.

Assertions:

- Region image reaches the vision route.
- Token estimate is shown before submit.
- Cancel sends no request.
- Permission denied is shown without app crash.

### `test_voice_and_dictation_workflow`

Scenario:

1. Hold voice hotkey, record fake audio, release.
2. Transcribe and send voice query.
3. Hold dictation hotkey, record fake audio, release.
4. Paste raw transcript.
5. Repeat with LLM cleanup mode.
6. Repeat with mic/transcription failure.

Assertions:

- Recording state is visible while held.
- Voice query uses configured voice context.
- Dictation pastes instead of sending assistant query.
- Failures leave the app ready for another attempt.

### `test_addon_lifecycle_workflow`

Scenario:

1. Load fake add-on with tray action, settings, intent, hotkey, tool, and
   notification.
2. Enable add-on.
3. Use contributed intent in overlay.
4. Use contributed tool in a query.
5. Invoke tray action.
6. Disable add-on.

Assertions:

- Contributions appear only while enabled.
- Permission-denied add-on action is visible and logged.
- Disabled add-on leaves no stale intent/tool/hotkey.

### `test_auto_agent_user_workflow`

Scenario:

1. Open auto-agent task window.
2. Choose workspace, primary model, and fallback models.
3. Start run.
4. Emit agent messages, tool calls, approval request, approval response,
   verification result, and final result.
5. Repeat with route auth failure.
6. Repeat with non-git workspace.

Assertions:

- Model labels match settings.
- Tool call errors appear in visible log.
- Approval blocks the run until user responds.
- Auth failure stops once with clear status.
- Non-git workspace still gets useful verification suggestions.

### `test_os_native_contracts`

Scenario:

1. Run the hotkey/context/paste/screenshot contract against fake Windows,
   macOS, and Linux adapters.
2. Simulate missing accessibility, automation, screen capture, microphone, and
   clipboard permissions.
3. Simulate helper crash/restart.

Assertions:

- Each OS shows platform-specific permission guidance.
- Supported features continue when one native feature fails.
- Helper failures are logged or notified.
- Tests never require real host OS permissions.

## Coverage Matrix

| User Area | Must Use Real State | External Fakes Allowed | Key Assertions |
| --- | --- | --- | --- |
| Intent overlay context | Projects, conversations, caller settings | Native context, model | Chips, token labels, context choices, outgoing query payload |
| Chat context | Conversations, projects, attachments, context policy | Native context, model stream | Preview callback, chip persistence, message context, send policy |
| Memory | Conversation store, project ids, memory store | Model text/tool response | Project scope, retrieval, viewer operations, rejected facts |
| Settings | Settings dialog, env snapshot, secret placeholders | Auth/test endpoints | Visibility, save/reload, reset, dirty markers, validation |
| Bubble | Bubble widget/config | Model/TTS stream, fake clock | Geometry vs font size, streaming, TTS highlight, timing |
| Rewrite | Caller config, selected text path | Native paste, model | Paste target, fallback, clipboard preservation |
| Screenshot | Overlay selection state | Screenshot image/permissions | Estimate, vision payload, cancel/permission states |
| Voice/dictation | Hotkey/caller settings | Mic/STT/model/native paste | Recording state, transcript path, dictation vs query |
| Add-ons | Add-on registry/store | Add-on host implementation | Contributions, permissions, cleanup |
| Auto-agent | Task window/run log | Runner/model/tools | Model naming, approvals, logs, auth failure |
| OS/native | None | Native adapters | Permission guidance and degraded operation |
| Real provider | Temp conversations/memory | None for selected route | Real streaming shape, route/model identity, context reaches prompt |
| Chat history | Conversation/project store | Model stream | Rename, pin, branch, rewind, delete, reload integrity |
| Context buffer/drop zone | Conversation attachments/context policy | Native context/file contents | Add/remove/clear, priority order, no leakage |
| Stream lifecycle | Generation ids, conversation store | Slow model/audio streams | Cancel, stale chunks ignored, recovery after error |
| Provider fallback | Route settings/cache | Provider failures | Fallback order, cooldown, capability warnings, no stale route |
| Tools/files/approvals | Tool roots and permissions | Tool executor/native shell | Root enforcement, approval gating, visible structured errors |
| Persistence/migration | Temp env/data stores | Corrupt/legacy fixtures | Recovery, notices, reset scope, no unrelated data loss |
| Privacy/security | Redaction settings and logs | Secret-looking fake context | Prompt/log/add-on redaction and provider-safe errors |
| UI/accessibility | Qt widgets/settings | Platform style emulation | Keyboard flow, text fit, opaque popups, long labels |
| Worker lifecycle | Supervisor state/logs | Worker crash events | Recoverable degradation, single-instance handoff, clean quit |

## Why Existing Pytest Missed Bugs

The current tests are useful, but many validate helper behavior rather than full
user journeys. Bugs slipped through when tests did not assert what the user
could see or whether the visible choice reached the outgoing request.

This plan fixes that by:

- Testing the chat window context controls separately from the intent overlay.
- Requiring memory tests to pass through real conversation/project state.
- Exercising most settings controls, not only config parsing.
- Asserting visible chip labels, token estimates, tooltips, warnings, and logs.
- Testing disabled-source leakage.
- Testing stale preview ids and stale add-on/tool contributions.
- Testing stale stream chunks, cancellation, and conversation switches.
- Testing route fallback/cooldown and model capability warnings as user-visible
  workflow state.
- Testing privacy, logs, diagnostics, and approval boundaries, not only success
  paths.
- Simulating OS boundaries explicitly.
- Verifying persisted state after reload, not just in-memory widget values.

## Implementation Order

1. Add the opt-in real GPT 5.5 integration test so provider/API regressions are
   visible before broader fake-based workflow coverage is trusted.
2. Add shared workflow fixtures for temp app state, fake native context,
   fake brain/model streams, fake audio, and event recording.
3. Add the test-isolation guard so later workflow tests cannot mutate real user
   data.
4. Add `test_intent_overlay_context_chips_drive_query_request`.
5. Add `test_chat_window_context_preview_and_send_policy`.
6. Add `test_memory_with_real_conversation_project_scope`.
7. Add `test_settings_change_reaches_every_surface` plus App/LLM tab settings
   tests.
8. Add TTS/Voice and Keybinds settings tests.
9. Add Prompts, Tools, Advanced, and settings-wide behavior tests.
10. Add disabled-context leakage tests for chat and overlay.
11. Add chat history, context buffer/drop-zone, and stream cancel/stale-state
    workflows.
12. Add provider fallback/cooldown, privacy/log redaction, file-tool approval,
    persistence/migration, and factory-reset workflows.
13. Add screenshot, rewrite/paste, voice/dictation, add-on install/reload,
    auto-agent history/meeting/artifacts, UI/accessibility, worker lifecycle,
    and OS contract workflows.

## First Files To Create

Start with focused files instead of one huge unmaintainable test:

- `tests/test_user_workflow_intent_context.py`
- `tests/test_user_workflow_chat_context.py`
- `tests/test_user_workflow_memory_conversation.py`
- `tests/test_user_workflow_settings.py`
- `tests/test_real_gpt55_integration.py`
- `tests/test_user_workflow_chat_history.py`
- `tests/test_user_workflow_context_sources.py`
- `tests/test_user_workflow_stream_lifecycle.py`
- `tests/test_user_workflow_routes_and_security.py`
- `tests/test_user_workflow_tools_files.py`
- `tests/test_user_workflow_addons_agent.py`
- `tests/test_user_workflow_ui_platform.py`

Once those are stable, add the remaining workflow areas. A thin
`tests/test_app_user_workflows.py` can import shared fixtures or hold a small
end-to-end smoke path, but the heavy coverage should stay split by user surface.
