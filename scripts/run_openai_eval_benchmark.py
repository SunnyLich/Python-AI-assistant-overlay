"""Benchmark Wisp current/unified and OpenAI-native paths on OpenAI Evals tests."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_openai_basic_eval_comparison import (  # noqa: E402
    CurrentWispCompletionFn,
    UnavailableCompletionFn,
    UnifiedWispCompletionFn,
    _run_eval_with_completion_fn,
)


DEFAULT_EVALS = [
    "test-match.s1.simple-v0",
    "test-fuzzy-match.s1.simple-v0",
    "test-includes-ignore-case.s1.simple-v0",
    "algebra-word-problems.s1.simple-v0",
    "base64-decode-simple.dev.v0",
]


def main() -> int:
    """Run a benchmark suite and write per-sample results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=".tmp/openai_eval_benchmarks")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--evals",
        default=",".join(DEFAULT_EVALS),
        help="Comma-separated OpenAI Evals names.",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=3,
        help="Samples per eval. Use 0 for the full eval.",
    )
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
    run_dir.mkdir(parents=True, exist_ok=True)

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

    eval_names = [name.strip() for name in args.evals.split(",") if name.strip()]
    max_samples = args.max_samples or None
    benchmark_evals = []
    for eval_name in eval_names:
        safe_eval_name = _safe_name(eval_name)
        events_dir = run_dir / safe_eval_name / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        runs = [
            _run_eval_with_completion_fn(
                evals,
                eval_name,
                flow_name,
                completion_fn,
                events_dir,
                max_samples=max_samples,
            )
            for flow_name, completion_fn in completion_fns
        ]
        benchmark_evals.append({
            "eval_name": eval_name,
            "runs": [run.__dict__ for run in runs],
            "samples": _sample_matrix(runs),
        })

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": selected_model,
        "max_samples_per_eval": max_samples,
        "flows": [name for name, _completion_fn in completion_fns],
        "evals": benchmark_evals,
        "notes": [
            "All tests are loaded from the installed OpenAI Evals registry.",
            "Scores are produced by OpenAI Evals eval classes and LocalRecorder events.",
            "Replies are parsed from OpenAI Evals sampling events.",
            "These are text-output evals, not tool-loop evals.",
            "openai-evals-native uses temperature=1 because gpt-5.5 rejects explicit temperature=0.",
        ],
    }
    (run_dir / "benchmark.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "benchmark.md").write_text(_markdown_report(payload), encoding="utf-8")
    print(f"Wrote OpenAI eval benchmark to {run_dir}")
    print(f"Benchmark JSON: {run_dir / 'benchmark.json'}")
    print(f"Benchmark report: {run_dir / 'benchmark.md'}")
    return 0


def _sample_matrix(runs) -> list[dict[str, Any]]:
    """Build per-sample rows from OpenAI Evals JSONL event logs."""
    by_sample: dict[str, dict[str, Any]] = {}
    for run in runs:
        flow = run.name
        events = _read_events(Path(run.record_path))
        if run.error:
            by_sample.setdefault("__run_error__", {"sample_id": "__run_error__", "prompt": "", "expected": None})
            by_sample["__run_error__"][flow] = {
                "reply": "",
                "correct": False,
                "error_type": run.error_type,
                "error": run.error,
            }
            continue
        for event in events:
            if not isinstance(event, dict) or "type" not in event:
                continue
            sample_id = str(event.get("sample_id") or "")
            if not sample_id:
                continue
            row = by_sample.setdefault(sample_id, {"sample_id": sample_id, "prompt": "", "expected": None})
            data = event.get("data") or {}
            cell = row.setdefault(flow, {})
            if event["type"] == "sampling":
                row["prompt"] = row.get("prompt") or _prompt_text(data.get("prompt"))
                cell["reply"] = _sampled_text(data.get("sampled"))
                cell["model"] = data.get("model")
            elif event["type"] == "match":
                row["expected"] = data.get("expected")
                if "sampled" in data and "reply" not in cell:
                    cell["reply"] = _sampled_text(data.get("sampled"))
                cell["correct"] = bool(data.get("correct"))
                cell["picked"] = data.get("picked")
            elif event["type"] == "metrics":
                cell["metrics"] = data
    return [by_sample[key] for key in sorted(by_sample)]


def _read_events(path: Path) -> list[dict[str, Any]]:
    """Read an OpenAI Evals JSONL event file."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _prompt_text(prompt: Any) -> str:
    """Return a compact prompt string from an OpenAI Evals prompt."""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        user_messages = [
            str(message.get("content") or "")
            for message in prompt
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        if user_messages:
            return user_messages[-1]
        return " / ".join(
            str(message.get("content") or "")
            for message in prompt
            if isinstance(message, dict)
        )
    return str(prompt)


def _sampled_text(sampled: Any) -> str:
    """Return sampled completion as text."""
    if isinstance(sampled, list):
        return str(sampled[0] if sampled else "")
    return str(sampled or "")


def _markdown_report(payload: dict[str, Any]) -> str:
    """Render a human-readable benchmark report."""
    lines = [
        "# OpenAI Eval Benchmark",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Model: `{payload['model']}`",
        f"Max samples per eval: `{payload['max_samples_per_eval']}`",
        "",
        "## Score Summary",
        "",
        "| Eval | Flow | Result | Error |",
        "| --- | --- | --- | --- |",
    ]
    for eval_payload in payload["evals"]:
        for run in eval_payload["runs"]:
            result = _short_json(run.get("result") or {})
            lines.append(
                f"| `{eval_payload['eval_name']}` | `{run['name']}` | `{_escape(result)}` | `{_escape(run.get('error') or '')}` |"
            )
    lines.extend(["", "## Sample Results", ""])
    for eval_payload in payload["evals"]:
        lines.extend([f"### `{eval_payload['eval_name']}`", ""])
        header = "| Sample | Prompt | Expected | " + " | ".join(f"`{flow}`" for flow in payload["flows"]) + " |"
        separator = "| --- | --- | --- | " + " | ".join("---" for _flow in payload["flows"]) + " |"
        lines.extend([header, separator])
        for sample in eval_payload["samples"]:
            cells = []
            for flow in payload["flows"]:
                cell = sample.get(flow) or {}
                reply = _one_line(cell.get("reply") or "")
                correct = cell.get("correct")
                label = "pass" if correct is True else "fail" if correct is False else "n/a"
                error = cell.get("error")
                cells.append(_escape(f"{label}: {reply}" if not error else f"error: {error}"))
            lines.append(
                "| "
                + " | ".join([
                    f"`{_escape(str(sample.get('sample_id') or ''))}`",
                    _escape(_one_line(sample.get("prompt") or "")),
                    _escape(_one_line(json.dumps(sample.get("expected"), ensure_ascii=False))),
                    *cells,
                ])
                + " |"
            )
        lines.append("")
    lines.extend(["## Notes", ""])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines)


def _safe_name(value: str) -> str:
    """Return a filesystem-safe name."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _short_json(value: Any) -> str:
    """Serialize compact JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _one_line(value: str, limit: int = 140) -> str:
    """Return one-line text with a length cap."""
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _escape(value: str) -> str:
    """Escape Markdown table separators."""
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
