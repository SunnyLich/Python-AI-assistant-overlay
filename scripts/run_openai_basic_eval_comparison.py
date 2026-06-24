"""Run current, unified, and OpenAI-native paths against OpenAI Evals' test-match eval."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class EvalRunResult:
    """One OpenAI Evals run result."""

    name: str
    result: dict[str, Any]
    record_path: str
    error: str = ""
    error_type: str = ""


class TextCompletionResult:
    """Minimal OpenAI Evals completion result."""

    def __init__(self, text: str):
        """Store one sampled completion."""
        self._text = text

    def get_completions(self) -> list[str]:
        """Return sampled completion text."""
        return [self._text]


class CurrentWispCompletionFn:
    """OpenAI Evals completion_fn wrapper around Wisp's current chat path."""

    def __init__(self, *, model: str):
        """Initialize the current Wisp wrapper."""
        self.model = model
        from core.llm_clients import client as llm

        self._llm = llm
        self._client = llm._get_chat_codex_client()

    def __call__(self, prompt, **_kwargs) -> TextCompletionResult:
        """Sample from the current Wisp chat path."""
        user_text, system_prompt, history = _wisp_prompt_parts(prompt)
        chunks = self._llm._stream_codex(
            user_text,
            self.model,
            self._client,
            use_tools=False,
            history=history,
            system_prompt=system_prompt,
        )
        text = "".join(str(chunk) for chunk in chunks)
        _record_sampling_if_available(prompt, text, self.model)
        return TextCompletionResult(text)


class UnifiedWispCompletionFn:
    """OpenAI Evals completion_fn wrapper around Wisp's unified chat loop."""

    def __init__(self, *, model: str):
        """Initialize the unified Wisp wrapper."""
        self.model = model
        from core.llm_clients import client as llm

        self._llm = llm
        self._client = llm._get_chat_codex_client()

    def __call__(self, prompt, **_kwargs) -> TextCompletionResult:
        """Sample from the unified Responses adapter through ChatToolLoop."""
        from core.llm_clients.chat_flow_compare import FakeToolExecutor
        from core.llm_clients.chat_tool_loop import ChatToolLoop, ChatToolRequest
        from core.llm_clients.responses_chat_adapter import ResponsesChatLoopModel

        user_text, system_prompt, history = _wisp_prompt_parts(prompt)
        messages = [*history, {"role": "user", "content": user_text}]
        request = ChatToolRequest(
            messages=messages,
            system_prompt=system_prompt,
            model_route={"provider": "chatgpt", "model": self.model},
            tools=[],
            allowed_tools=[],
            pinned_tools=[],
            permissions={},
            budgets={},
        )
        final = ChatToolLoop().run(
            request,
            ResponsesChatLoopModel(
                self._client,
                model=self.model,
                instructions=system_prompt,
                tools=[],
            ),
            FakeToolExecutor({}),
        )
        _record_sampling_if_available(prompt, final.text, self.model)
        return TextCompletionResult(final.text)


class UnavailableCompletionFn:
    """Completion function that records an unavailable comparison path."""

    def __init__(self, message: str):
        """Initialize with a concrete unavailable reason."""
        self.message = message

    def __call__(self, _prompt, **_kwargs) -> TextCompletionResult:
        """Raise the unavailable reason inside the OpenAI Evals run."""
        raise RuntimeError(self.message)


def main() -> int:
    """Run the built-in OpenAI Evals test-match eval against three paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=".tmp/openai_builtin_eval_comparisons")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--eval-name",
        default="test-match.s1.simple-v0",
        help="Built-in OpenAI Evals eval name to run.",
    )
    parser.add_argument("--max-samples", type=int, default=0)
    args = parser.parse_args()

    from core.llm_clients import client as llm
    import config

    platform_api_key = os.environ.get("OPENAI_API_KEY") or getattr(config, "OPENAI_API_KEY", "")
    if platform_api_key:
        os.environ["OPENAI_API_KEY"] = platform_api_key
    else:
        os.environ.setdefault("OPENAI_API_KEY", "sk-local-openai-evals-harness")
    import evals

    selected_model = args.model or config.CHAT_LLM_MODEL or config.LLM_MODEL
    run_dir = Path(args.output_root) / datetime.now().isoformat(timespec="seconds").replace(":", "-")
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    completion_fns: list[tuple[str, Any]] = [
        ("current-wisp", CurrentWispCompletionFn(model=selected_model)),
        ("unified-wisp", UnifiedWispCompletionFn(model=selected_model)),
    ]
    if platform_api_key:
        completion_fns.append((
            "openai-evals-native",
            evals.OpenAIChatCompletionFn(
                model=selected_model,
                api_key=platform_api_key,
                extra_options={"temperature": 1},
            ),
        ))
    else:
        completion_fns.append((
            "openai-evals-native",
            UnavailableCompletionFn("No OPENAI_API_KEY available for OpenAI Evals native API path."),
        ))

    results = []
    for name, completion_fn in completion_fns:
        results.append(
            _run_eval_with_completion_fn(
                evals,
                args.eval_name,
                name,
                completion_fn,
                events_dir,
                max_samples=args.max_samples or None,
            )
        )

    payload = {
        "eval_name": args.eval_name,
        "model": selected_model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runs": [result.__dict__ for result in results],
        "notes": [
            "This uses OpenAI Evals' built-in test-match eval.",
            "current-wisp and unified-wisp adapt Wisp chat routes into OpenAI Evals completion_fn objects.",
            "openai-evals-native uses the evals package's OpenAIChatCompletionFn on the same eval.",
            "openai-evals-native passes temperature=1 because gpt-5.5 rejects the eval's default temperature=0.",
            "The eval has no tools, so this checks plain chat completion behavior, not tool-loop behavior.",
        ],
    }
    (run_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "report.md").write_text(_markdown_report(payload), encoding="utf-8")
    print(f"Wrote OpenAI built-in eval comparison to {run_dir}")
    print(f"Results: {run_dir / 'results.json'}")
    return 0


def _run_eval_with_completion_fn(
    evals,
    eval_name: str,
    flow_name: str,
    completion_fn,
    events_dir: Path,
    *,
    max_samples: int | None = None,
) -> EvalRunResult:
    """Run one completion function through OpenAI Evals."""
    record_path = events_dir / f"{flow_name}.jsonl"
    try:
        if max_samples is not None:
            evals.eval.set_max_samples(max_samples)
        _install_openai_evals_windows_data_patch(evals)
        registry = evals.registry.Registry()
        eval_spec = registry.get_eval(eval_name)
        if eval_spec is None:
            raise ValueError(f"OpenAI eval not found: {eval_name}")
        run_spec = evals.base.RunSpec(
            completion_fns=[flow_name],
            eval_name=eval_name,
            base_eval=eval_name.split(".")[0],
            split=eval_name.split(".")[1],
            run_config={
                "source": "OpenAI Evals built-in registry",
                "eval_name": eval_name,
                "flow_name": flow_name,
                "max_samples": max_samples,
            },
            created_by="wisp-openai-test-comparison",
        )
        recorder = evals.record.LocalRecorder(_blobfile_local_path(record_path), run_spec=run_spec)
        eval_class = registry.get_class(eval_spec)
        evaluation = eval_class(
            completion_fns=[completion_fn],
            seed=20220722,
            name=eval_name,
            eval_registry_path=eval_spec.registry_path,
            registry=registry,
            **(eval_spec.args or {}),
        )
        result = evaluation.run(recorder)
        recorder.record_final_report(result)
        recorder.flush_events()
        return EvalRunResult(name=flow_name, result=result, record_path=str(record_path))
    except Exception as exc:  # noqa: BLE001 - comparison should record failed paths
        error_payload = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(error_payload, indent=2), encoding="utf-8")
        return EvalRunResult(
            name=flow_name,
            result={},
            record_path=str(record_path),
            error=str(exc),
            error_type=type(exc).__name__,
        )


def _install_openai_evals_windows_data_patch(evals) -> None:
    """Let OpenAI Evals read local registry JSONL files from Windows drive paths."""
    if getattr(evals.data, "_wisp_windows_data_patch", False):
        return
    original_get_jsonl = evals.data.get_jsonl

    def get_jsonl(path: str) -> list[dict[str, Any]]:
        local_path = Path(path)
        if local_path.exists():
            if local_path.is_dir():
                rows: list[dict[str, Any]] = []
                for child in sorted(local_path.rglob("*.jsonl")):
                    rows.extend(_read_local_jsonl(child))
                return rows
            return _read_local_jsonl(local_path)
        return original_get_jsonl(path)

    evals.data.get_jsonl = get_jsonl
    evals.eval.get_jsonl = get_jsonl
    evals.data._wisp_windows_data_patch = True


def _read_local_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a local JSONL file without blobfile."""
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Error parsing JSON at {path}:{line_number}: {exc}") from exc
    return rows


def _wisp_prompt_parts(prompt) -> tuple[str, str, list[dict[str, str]]]:
    """Convert an OpenAI Evals chat prompt into Wisp chat pieces."""
    if isinstance(prompt, str):
        return prompt, "", []
    if not isinstance(prompt, list):
        return str(prompt), "", []

    system_parts: list[str] = []
    history: list[dict[str, str]] = []
    user_text = ""
    pending_example_user = ""
    for message in prompt:
        role = str(message.get("role") or "")
        name = str(message.get("name") or "")
        content = str(message.get("content") or "")
        if role == "user":
            user_text = content
            continue
        if role == "assistant":
            history.append({"role": "assistant", "content": content})
            continue
        if role == "system" and name == "example_user":
            pending_example_user = content
            continue
        if role == "system" and name == "example_assistant":
            if pending_example_user:
                history.append({"role": "user", "content": pending_example_user})
                pending_example_user = ""
            history.append({"role": "assistant", "content": content})
            continue
        if role == "system":
            system_parts.append(content)
            continue
        if content:
            history.append({"role": "user", "content": content})
    return user_text, "\n\n".join(system_parts), history


def _record_sampling_if_available(prompt, text: str, model: str) -> None:
    """Record sampling when running inside an OpenAI Evals recorder context."""
    try:
        import evals

        if evals.record.default_recorder() is not None:
            evals.record.record_sampling(prompt=prompt, sampled=[text], model=model)
    except Exception:
        return


def _blobfile_local_path(path: Path) -> str:
    """Return a path accepted by blobfile on Windows."""
    resolved = path.resolve()
    try:
        return os.path.relpath(resolved, Path.cwd().resolve())
    except ValueError:
        return resolved.as_posix()


def _markdown_report(payload: dict[str, Any]) -> str:
    """Render a short Markdown report."""
    lines = [
        f"# OpenAI Built-In Eval Comparison",
        "",
        f"Eval: `{payload['eval_name']}`",
        f"Model: `{payload['model']}`",
        "",
        "| Path | Result | Error | Events |",
        "| --- | --- | --- | --- |",
    ]
    for run in payload["runs"]:
        result = json.dumps(run["result"], ensure_ascii=False)
        lines.append(
            f"| `{run['name']}` | `{result}` | `{run['error'] or ''}` | `{run['record_path']}` |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
