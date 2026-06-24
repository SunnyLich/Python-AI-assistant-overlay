# Temporary Plan: App Context Cleanup

This is a working plan for making App context reliable enough for Ctrl+Q and
Ctrl+Shift+Q before adding a deeper editor integration. It is intentionally
temporary: once the implementation settles, fold the useful parts into the
developer docs and remove this file.

## Goals

- Do not send app chrome, menus, status bars, or accessibility warnings as
  source context.
- Prefer the text area or document surface the user actually cares about.
- Keep context from different apps clearly separated in preview and in prompts.
- Preserve Ctrl+Shift+Q's target selection semantics: the selected text is
  visible to the model as the target, while external app/document context stays
  separately labeled as source material.
- Fail closed when context quality is poor. No app context is better than
  poisoned context.

## Non-Goals

- Do not build a VS Code/Cursor/Windsurf extension in this pass.
- Do not attempt OCR or screenshot parsing as the default app-context path.
- Do not make model-side prompting compensate for bad extraction.
- Do not remove existing browser/document/file context behavior unless it is
  directly poisoning app context.

## 1. Rank UIA Elements

Current issue: the Windows UIA path can capture large chunks of window
accessibility text, including menus and status UI. The fix should choose better
candidate elements before text cleanup.

Implementation sketch:

- In `core/context_fetcher.py`, collect candidate UIA descendants that expose
  `TextPattern` or `ValuePattern`.
- For each candidate, collect:
  - control type
  - localized control type
  - name
  - class name
  - bounding rectangle
  - whether it has keyboard focus
  - extracted text length
  - line count and average line length
- Score candidates with positive weights for:
  - focused element
  - `Document`, `Edit`, or text-like control types
  - larger content rectangles below toolbar area
  - multiline text with prose/code-like structure
  - text that is not mostly repeated short labels
- Score candidates with negative weights for:
  - `MenuBar`, `ToolBar`, `StatusBar`, `Button`, `Tab`, `Pane` with mostly
    short labels
  - rectangles near the top or bottom chrome areas
  - accessibility warning text
  - text dominated by known editor commands/status words
- Pick the highest scoring candidate above a minimum threshold.
- If no candidate passes, return no app context and include a debug reason.

Tests:

- A fake document candidate beats a fake whole-window chrome candidate.
- A focused editable candidate beats a larger unfocused pane when both have text.
- A menu/status-only candidate is rejected.
- Existing Notepad/simple editor extraction still works.

## 2. Filter Chrome Dumps

Current issue: even after candidate selection, some apps expose mixed text. The
cleanup layer should remove obvious standalone chrome and reject fully poisoned
captures.

Implementation sketch:

- Keep the existing mojibake repair before cleanup.
- Detect "chrome mode" only when several strong signals appear, such as:
  - standalone menu labels: `File`, `Edit`, `Selection`, `View`, `Go`, `Run`
  - status labels: `CRLF`, `UTF-8`, `Spaces: 4`, `Ln 5, Col 82`
  - editor accessibility warnings
  - command labels such as `More Actions`, `Open in Agents`
- In chrome mode:
  - strip standalone menu/status/command labels
  - strip known accessibility warnings
  - keep longer natural-language or code-like lines
- Outside chrome mode:
  - avoid stripping normal short document lines such as `1` or `Python`
  - only remove very high-confidence accessibility warnings and glyph noise
- After cleanup, compute a quality score. Reject if the remaining text is empty
  or still mostly chrome.

Tests:

- The pasted VS Code chrome dump cleans to empty.
- Real editor text inside a chrome dump survives.
- Plain short content like `1\nPython` survives outside chrome mode.
- Mojibake and glyph noise are repaired before chrome detection.

## 3. Preserve Source Boundaries

Current issue: when several app sources are detected, both the model and the user
need to know which text came from which app/window.

Implementation sketch:

- Keep `active_document_sources` as the source of truth for preview rows.
- Keep prompt boundaries stable:
  - `[Notepad]`
  - `[demo.py - Visual Studio Code]`
  - or `--- BEGIN ACTIVE DOCUMENT: ... ---` for a grouped block.
- Make preview labels and prompt labels derive from the same normalized labels.
- Ensure Ctrl+Shift+Q rewrite context keeps the target selection in its own
  `TARGET SELECTION` block while external app/source text stays in separate
  source blocks.
- Add debug-only source labels to logs, but never log full source text.

Tests:

- Intent overlay shows multiple App rows: `App 1: Notepad`,
  `App 2: VS Code`.
- Prompt context contains the same labels shown in preview.
- Rewrite context includes the target Notepad text as `TARGET SELECTION` and the
  VS Code text as external source context when the prompt asks for VS Code
  content.

## 4. Use Focused Range As Target Anchor

Current issue: the selection and external app context are
mixed together so the model cannot tell "target to replace" from "source to copy
from."

Implementation sketch:

- Continue capturing paste-back focus/selection at hotkey time.
- For Windows, use the cached UIA text range for anchored paste-back.
- For macOS, keep using the cached AX element.
- During rewrite-context construction:
  - always send the selected text as `TARGET SELECTION`
  - label external app/document text as `SOURCE CONTEXT`
  - if an app-context block is just a duplicate of the target selection, do not
    also present that duplicate block as an external source
  - prefer non-target document/editor blocks when the user asks for content from
    another app, such as VS Code
- If no external source remains, still send the target selection, but show that
  App context is unavailable or empty depending on caller mode.

Tests:

- Rewriting selected Notepad text with VS Code context sends Notepad text as
  `TARGET SELECTION` and VS Code text as `SOURCE CONTEXT`.
- A prompt like `fix this sentence` can use the target selection alone.
- A prompt like `replace this with the content from VS Code` uses the target to
  know what to replace and VS Code as the replacement source.
- If the user changes focus before the model reply, paste-back still replaces
  the original selected range.
- If the anchored range is stale, paste-back refuses unsafe fallback and reports
  failure.

## 5. Add Content Quality Heuristics

Current issue: raw character count can make bad context look useful. The preview
and token estimate should reflect usable context, not chrome volume.

Implementation sketch:

- Add a `DocumentTextQuality` helper or equivalent scoring function.
- Features to consider:
  - total cleaned chars
  - ratio of long lines to short lines
  - repeated short-label ratio
  - code/prose signals: punctuation, indentation, braces, sentence structure
  - chrome signal count
  - accessibility warning count
- Use the quality score in two places:
  - candidate ranking
  - final accept/reject decision
- Add debug data:
  - chosen candidate label/control type
  - rejected reason
  - cleaned chars
  - quality score
- Keep debug data out of the prompt unless it is user-facing context status.

Tests:

- Chrome-heavy text scores below threshold.
- Real prose/code scores above threshold.
- Mixed chrome plus real text scores above threshold after cleanup.
- Token estimate uses cleaned accepted text, not rejected chrome text.

## Suggested Order

1. Tighten cleanup and rejection around the known VS Code chrome dump.
2. Add quality scoring and use it for accept/reject.
3. Add UIA candidate scoring and select the best candidate before cleanup.
4. Wire source labels through preview, prompt, and diagnostics.
5. Re-run paste-back rewrite tests on Windows with Notepad plus VS Code.

## Manual Test Script

1. Open Notepad with selected text: `Text1`.
2. Open VS Code/Cursor with a different document containing `Text 2`.
3. Trigger Ctrl+Shift+Q.
4. Use custom prompt: `Replace Text1 with the entirety of Text 2 from VS Code`.
5. Confirm the intent overlay preview shows VS Code content, not chrome.
6. Confirm the model replaces the Notepad selection, not the VS Code buffer and
   not the current caret.
7. Change focus while the model is replying and confirm paste-back still targets
   the original selection or fails safely.

## Done Criteria

- No known VS Code chrome dump is sent to the model as app context.
- Intent preview reflects the same cleaned context sent to the prompt.
- Ctrl+Shift+Q can copy content from a non-target app into the selected target
  text.
- Clipboard is restored after paste-back.
- Unsafe unanchored paste-back is refused when a captured range exists but
  cannot be reused.
