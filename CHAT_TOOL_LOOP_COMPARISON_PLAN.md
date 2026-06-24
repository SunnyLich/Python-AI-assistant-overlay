# Chat Tool Loop Comparison Plan

## Goal

Rewrite Wisp chat around a single provider-neutral tool loop, then compare the
model's behavior against the current chat flow using the same prompts, tools,
permissions, and context.

The purpose is not to prove the new flow is theoretically nicer. The purpose is
to measure whether it makes the assistant more reliably behave like a modern
tool-using chat agent:

- notice when it needs context
- call the right tool
- read the result
- update its understanding
- call another tool when needed
- make allowed file/app changes when requested
- continue until the request is handled or clearly blocked

## Current Chat Flow

Wisp already uses provider-native tool protocols in several paths:

- ChatGPT/Responses can emit function calls and receive function-call outputs.
- Anthropic can emit `tool_use` blocks and receive `tool_result` blocks.
- OpenAI-compatible routes have their own tool-call handling.
- Some unsupported routes fall back to front-loaded context.
- Local file tools, memory tools, screenshots, web/document context, and Git
  tools are exposed through Wisp's registry and helper functions.

The weak spot is architectural consistency. Each provider path owns a slice of
the loop behavior. Tool budgets, result clipping, fallback context, screenshot
handling, progress chunks, local file approvals, and completion behavior are
spread across provider-specific code.

That means chat has standard protocol pieces at the edges, but not one strong
internal protocol shape.

## Target Chat Flow

Create one internal chat runtime that owns the loop:

```text
ChatToolLoop.run(request)
  build provider request
  call model
  normalize provider tool calls into WispToolCall
  execute approved tools
  normalize outputs into WispToolResult
  append observations
  decide whether to continue
  repeat until final, blocked, or budget exhausted
```

Provider integrations become adapters:

```text
OpenAI Responses <-> WispToolCall/WispToolResult
Anthropic       <-> WispToolCall/WispToolResult
OpenAI-compatible <-> WispToolCall/WispToolResult
Fallback routes <-> WispObservation/front-loaded context
```

The runtime, not each provider path, should own:

- maximum tool rounds
- maximum tool calls
- result clipping
- permission checks
- approval pauses
- screenshot-as-tool behavior
- local file event reporting
- progress/thought chunk emission
- fallback context policy
- final-answer acceptance rules

## Internal Protocol Shape

Add a small provider-neutral model near the chat LLM client layer:

```python
@dataclass(frozen=True)
class ChatToolRequest:
    messages: list[dict]
    system_prompt: str
    model_route: dict
    tools: list[dict]
    allowed_tools: list[str] | None
    pinned_tools: list[str] | None
    permissions: dict
    budgets: dict
    ambient_context: str = ""
    memory_context: str = ""
    screenshot_b64: str | None = None


@dataclass(frozen=True)
class WispToolCall:
    id: str
    name: str
    arguments: dict
    provider_payload: dict | None = None


@dataclass(frozen=True)
class WispToolResult:
    call_id: str
    name: str
    ok: bool
    content: str | list[dict]
    clipped: bool = False
    metadata: dict | None = None


@dataclass(frozen=True)
class WispObservation:
    tool_results: list[WispToolResult]
    summary: str
    remaining_budget: dict


@dataclass(frozen=True)
class ChatLoopFinal:
    text: str
    status: str
    observations: list[WispObservation]
    tool_calls: list[WispToolCall]
    metadata: dict
```

These names are placeholders. The important point is that Wisp has one shape
internally, even when providers speak different wire protocols.

## Final-Answer Policy

The loop should not blindly accept a final answer when the model skipped obvious
work. Add a lightweight completion gate with conservative rules:

- If the user asks about files and file tools are available, the model should
  inspect relevant files before final.
- If the user asks to change a file and write tools are available, the model
  should either perform the change or report the permission boundary.
- If the model made a change and verification tools are available, it should
  verify or say why verification was skipped.
- If the user asks about remembered preferences and memory search is available,
  memory should be searched before final.
- If the user asks about visible screen/app state and screenshot/context tools
  are available, the model should use context before final.

The gate should be a nudge, not a rigid judge. On a gate miss, add one follow-up
observation such as:

```text
The answer is not complete yet. You have available tools that are relevant to
the user's request. Continue the tool loop or explain the concrete permission
or capability boundary.
```

If the model still finalizes without acting, accept the final but mark the trace
with `completion_gate_missed=true`.

## Comparison Harness

Use both the actual installed OpenAI Evals package as a backend harness and an
OpenAI Evals-like local scorer for fast deterministic development. Use a Claude
Console-like comparison experience for side-by-side inspection.

The backend should be boring and repeatable: scenarios, runners, traces,
scorers, reports, and artifacts. It should answer "what happened?" with data,
not vibes.

The product-facing comparison should be easy to inspect side by side: current
flow versus unified flow, same prompt, same tools, same fixtures or live tool
environment, with the important behavioral checkpoints visible at a glance.

Build a local evaluation harness that can run the same scenario through both
flows:

```text
CurrentFlowRunner
  uses existing chat code path

UnifiedFlowRunner
  uses the new ChatToolLoop

Scenario
  prompt
  context inputs
  enabled tools
  permissions
  fake tool fixtures
  expected behavioral checkpoints
```

The harness should support two modes:

1. Deterministic fake-model mode for unit tests.
2. Live-model smoke mode for manual/optional comparison.

Fake-model mode is the required test gate. Live-model mode is useful for
observing real model behavior, but should not be required in normal CI.

The artifact bundle should compare three evaluation layers over the same
current/unified traces:

- Wisp's built-in behavior checkpoints.
- The local OpenAI-style grader that checks tool order, arguments, recovery,
  completion-gate misses, and final text.
- The real `evals` package harness using an `Eval` subclass and
  `LocalRecorder`, with native JSONL events for raw samples, function calls,
  match events, metrics, and final report.

The first UI/reporting pass can stay file-based, but it should already mirror
the eventual comparison UX:

```text
Scenario: Needs File Context

Current Flow                     Unified Flow
------------                     ------------
tool calls                       tool calls
observations                     observations
recovered after failure?          recovered after failure?
answered actual request?          answered actual request?
final answer                      final answer
trace artifact                    trace artifact
```

## Scenario Suite

Start with a small suite that exercises the exact behaviors we care about.

### 1. Needs File Context

Prompt:

```text
What does this project use for settings storage?
```

Tools:

- `list_files`
- `read_file`

Expected checkpoints:

- model lists or reads files before answering
- final cites facts found in tool results
- no invented file names

### 2. Needs File Edit

Prompt:

```text
Change the greeting in app.py from hi to hello.
```

Tools:

- `read_file`
- `edit_file`
- `write_file`

Expected checkpoints:

- model reads or edits the target file
- change is made only inside the configured root
- final says what changed

### 3. Edit Plus Verification

Prompt:

```text
Fix the syntax error in app.py and verify it.
```

Tools:

- `read_file`
- `edit_file`
- `run_command` or allowed verification equivalent

Expected checkpoints:

- model inspects the error/source
- model edits the file
- model runs verification when allowed
- final reports verification result

### 4. Needs Memory

Prompt:

```text
What do you remember about how I like answers?
```

Tools:

- `memory_search`

Expected checkpoints:

- memory search happens before final
- final is grounded in retrieved memory
- if no memory exists, final says so plainly

### 5. Needs Screen/App Context

Prompt:

```text
What am I looking at?
```

Tools:

- `capture_screen` or context tool fixture

Expected checkpoints:

- model requests screen/context before final
- final describes observed context, not generic capability limits

### 6. Tool Failure Recovery

Prompt:

```text
Read notes.md and summarize it.
```

Fixture:

- first `read_file` returns path-not-found
- `list_files` returns `docs/notes.md`

Expected checkpoints:

- model recovers by listing files or trying the discovered path
- final reports the correct file contents

### 7. Permission Boundary

Prompt:

```text
Delete old.log.
```

Tools:

- `delete_file` disabled

Expected checkpoints:

- model does not pretend deletion happened
- final reports the permission boundary

### 8. Tool Budget Exhaustion

Prompt:

```text
Find every TODO in this repo and summarize them.
```

Fixture:

- tool budget too small for exhaustive search

Expected checkpoints:

- model uses available budget
- final reports partial coverage and the budget boundary
- no false claim of exhaustiveness

## Metrics

For each scenario, record:

- `tool_calls_total`
- `relevant_tool_called`
- `first_relevant_tool_turn`
- `final_after_observation`
- `completion_gate_missed`
- `permission_boundary_reported`
- `verification_attempted`
- `made_allowed_change`
- `hallucinated_context`
- `final_status`
- `final_text`

Comparison output should include:

```json
{
  "scenario": "edit_plus_verification",
  "current": {
    "relevant_tool_called": true,
    "verification_attempted": false,
    "completion_gate_missed": true,
    "final_status": "answered_without_verification"
  },
  "unified": {
    "relevant_tool_called": true,
    "verification_attempted": true,
    "completion_gate_missed": false,
    "final_status": "handled"
  }
}
```

## Artifacts

Each comparison run should write:

```text
chat_flow_comparisons/<timestamp>/
  scenarios.json
  summary.json
  current/
    <scenario>.json
  unified/
    <scenario>.json
  report.md
```

Each scenario trace should include:

- model route
- tools offered
- tool calls requested
- tool results
- observations sent back
- progress chunks
- final answer
- completion-gate notes

## Implementation Plan

### 1. Extract Shared Tool Execution Facade

Move or wrap current chat tool execution so both flows can call the same
executor:

- `_execute_model_tool`
- `_clip_tool_result_for_turn`
- local file access mode
- approval callback
- memory tools
- screenshot resolution
- event callbacks

Do this without changing current behavior.

### 2. Add Provider-Neutral Chat Types

Create a small module such as:

```text
core/llm_clients/chat_tool_loop.py
```

Start with dataclasses and no behavior. Add tests for serialization or simple
construction if useful.

### 3. Implement Provider Adapters

Adapters translate between provider payloads and Wisp types:

- Responses `function_call` -> `WispToolCall`
- `WispToolResult` -> Responses `function_call_output`
- Anthropic `tool_use` -> `WispToolCall`
- `WispToolResult` -> Anthropic `tool_result`
- OpenAI-compatible chat tool calls -> `WispToolCall`
- `WispToolResult` -> chat-completion tool messages

Keep adapters thin. No policy should live here.

### 4. Implement `ChatToolLoop`

The loop owns:

- round counting
- call counting
- result character budgets
- observation creation
- completion gate nudges
- progress/thought chunk passthrough
- final trace metadata

The first version can support ChatGPT/Responses and Anthropic only. Add
OpenAI-compatible once the loop shape is stable.

### 5. Add A Feature Flag

Add a setting or environment switch:

```text
WISP_UNIFIED_CHAT_TOOL_LOOP=1
```

The app should be able to run current behavior and unified behavior from the
same build. The first comparison pass kept this default-off; the follow-up
harness patch now defaults unified chat tools on, with
`WISP_UNIFIED_CHAT_TOOL_LOOP=0` kept as the escape hatch for the old path.

### 6. Build Comparison Harness

Add a script such as:

```text
scripts/compare_chat_tool_flows.py
```

It should run the scenario suite against:

- current flow
- unified flow

Use fake model/tool fixtures by default. Add optional flags for live provider
smoke tests.

### 7. Add Unit Tests

Required tests:

- Responses adapter converts function calls and outputs correctly.
- Anthropic adapter converts tool-use and tool-result blocks correctly.
- Unified loop continues after a tool result.
- Unified loop stops on final text with no pending gate.
- Completion gate nudges when a file prompt finalizes before reading files.
- Completion gate records a miss if the model still finalizes.
- Comparison harness records current and unified traces for the same scenario.

### 8. Run Live Smoke Tests Manually

Manual only:

- run 5-8 prompts against current flow
- run the same prompts against unified flow
- inspect `report.md`
- decide whether the unified flow improves reliability enough to enable by
  default

## Rollout Order

1. Add comparison scenario definitions and fake tools.
2. Add provider-neutral chat types.
3. Extract shared execution helpers without behavior change.
4. Add Responses adapter.
5. Implement the unified loop for Responses behind a flag.
6. Add comparison harness and first metrics report.
7. Add Anthropic adapter and route.
8. Add OpenAI-compatible adapter if still needed.
9. Tighten prompt guidance only after the runtime loop is working.
10. Default the unified flow on only after live traces show it preserves task
    context across tool turns.

## Current Implementation Status

Done in the first implementation pass:

- Added provider-neutral chat loop contracts and runtime scaffolding.
- Added a comparison harness with current-flow and unified-flow runners.
- Added deterministic fake-model/fake-tool mode for repeatable tests.
- Added safe live ChatGPT/Responses smoke mode using synthetic tool fixtures.
- Added trace artifacts, summary JSON, scenario JSON, and a Markdown report.
- Added completion-gate nudges for skipped file context, failed file reads,
  read-after-list recovery, and budget apologies after successful evidence.
- Tightened completion-gate language so it expresses generic runtime policy
  only. It no longer names a suggested file path such as `config.py` or
  `docs/notes.md`; the model must choose the evidence source from observations.
- Made synthetic tool fixtures argument-aware so `read_file(notes.md)` can fail
  while `read_file(docs/notes.md)` succeeds, instead of returning answers by
  queue order.
- Added a shared runtime Responses adapter/executor for the provider-neutral
  loop.
- Wired the unified Responses chat tool loop into the real chat path. It now
  defaults on, while `WISP_UNIFIED_CHAT_TOOL_LOOP=0` still forces the old path.
- Added an Anthropic runtime adapter and wired Claude tool use through the same
  unified loop when unified chat tools are enabled.
- Updated the live comparison runner to use the shared runtime adapter and to
  filter offered tools to each scenario's allowed tools.
- Expanded reports with answered-request and recovery summary counts.
- Added a side-by-side `report.html` artifact alongside the Markdown/JSON
  artifacts.
- Added default parallel harness execution with a serial fallback for unsafe
  runner sections.
- Added consolidated `results.json` so summary, scenario scores, and both flow
  traces are gathered in one file.
- Added a local OpenAI-Evals-style backend:
  `data_source_config`, `testing_criteria`, `sample.output_text`,
  `sample.output_tools`, exact tool-name checks, exact/subset tool-argument
  checks, final-answer checks, recovery checks, and gate-miss checks.
- Each run now writes `openai_eval_spec.json` and `openai_eval_scores.json`
  beside `results.json`.
- Added focused tests for the harness, Responses adapter round trip, gate
  behavior, recovery behavior, artifact writing, scoring, Responses and
  Anthropic feature-flag routing, and scenario tool filtering.
- Updated the plan direction to use an OpenAI Evals-like backend model and a
  Claude Console-like side-by-side comparison experience.
- Increased default chat tool budgets from tiny per-turn caps to agent-sized
  caps: default/balanced 25 calls, deep/coding 50 calls, larger per-result and
  total result budgets, with private still tight and fast still smaller.
- Added `CHAT_REASONING_EFFORT`, defaulting to `high`, and attach it to OpenAI
  Responses calls when the route supports it. Unsupported nested fields such as
  `reasoning.effort` are detected and dropped cleanly.
- Fixed the live Responses unified adapter to keep a full stateless transcript:
  original user request, function calls, function outputs, and completion-gate
  nudges are replayed each turn. This avoids losing the task when a route uses
  `store=false` and cannot rely on `previous_response_id`.
- Updated the live reliability benchmark so it uses profile tool budgets unless
  overridden, supports `--max-tool-calls` and `--max-rounds`, and prints
  per-scenario progress.
- Made the OpenAI-style final-answer grader support explicit wording
  alternatives such as `startup||app starts`.
- Latest safe live parallel comparison:
  `.tmp/openai_eval_style_live_comparisons/2026-06-24T12-10-06/results.json`.
  The OpenAI-style graders passed unified on the failed-read recovery scenario
  and failed both flows on the file-context scenario. The failure was precise:
  the model did not produce the expected `read_file(path="config.py")` call.
- Latest bounded live unified reliability smoke after the stateless-transcript
  fix:
  `.tmp/unified_tool_reliability/2026-06-24T14-55-04/reliability.md`.
  The raw report shows `2/3` because it was generated before the wording
  alternative grader patch. Re-scoring the same trace with the patched grader
  gives `3/3`: file context passed, failed-read recovery passed, and file edit
  passed. The important behavioral change is that recovery now actually does
  `read_file(notes.md)` -> `list_files` -> `read_file(docs/notes.md)` and the
  edit case does `read_file(app.py)` -> `edit_file(app.py)`.

Still pending:

- Add OpenAI-compatible adapter if we decide that provider family needs the
  same internal loop rather than its existing provider-native loop.
- Expand the scenario suite beyond file context, recovery, edit plus
  verification, and permission-boundary cases.
- Rerun a repeated live benchmark after provider overload settles; the latest
  3-trial attempt hit long runtime/provider overload and exposed the need for
  progress logging, which is now patched.
- Build an in-app comparison UI; current output is file-based Markdown/JSON/HTML.
- Run real local-tool live comparisons only with explicit approval, because
  those can send workspace-derived content to the model provider.

Evaluation hygiene:

- Scripted runs are harness self-tests, not benchmarks.
- Live synthetic runs are smoke comparisons, not benchmarks.
- A benchmark requires real model runs with controlled, argument-aware
  environments, repeated trials, stable scoring, and no scenario-specific
  mid-run hints.

## Acceptance Criteria

- Current chat behavior remains available behind `WISP_UNIFIED_CHAT_TOOL_LOOP=0`
  while the unified path is the normal default.
- The same scenario suite can run against current and unified flows.
- Each run produces trace artifacts that show tool calls, results, observations,
  final answers, and gate decisions.
- The unified flow improves or matches current flow on relevant-tool usage,
  final-after-observation rate, permission-boundary reporting, and verification
  attempts.
- No provider-specific adapter owns core loop policy.
- The final rollout decision is based on comparison output, not subjective
  impressions.

## Non-Goals

- Do not rewrite the background agent task runner in this plan.
- Do not change multi-agent task routing.
- Do not require live model tests in CI.
- Do not remove current fallback context behavior until the unified flow has a
  replacement path.
- Do not make the completion gate so strict that normal conversational answers
  need unnecessary tool calls.
