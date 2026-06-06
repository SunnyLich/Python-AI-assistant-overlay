"""Unit tests for the ``brain.agent.run`` handler.

The agent loop is driven by a scripted model callback (no provider/network), so
these exercise the *real* ``core.agent`` runner end-to-end: spec deserialization,
the run-loop, log/trace event streaming, and the final-report result. Two seams
are covered -- the single-turn ``WISP_BRAIN_FAKE_LLM`` fast path and an explicit
``WISP_BRAIN_AGENT_TEST_SCRIPT`` of model turns.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wisp_brain import handlers


def _events_of(events, name):
    return [data for event, data in events if event == name]


def _noop_spec(scope, *, title, objective):
    """A minimal task spec for a noop fake run.

    git and shell are disabled so the runner never shells out (a noop agent needs
    neither), keeping the test hermetic and fast -- otherwise the final diff
    artifact step would invoke ``git status`` in a non-repo temp dir.
    """
    return {
        "title": title,
        "objective": objective,
        "scope_folder": str(scope),
        "allow_git": False,
        "allow_shell": False,
    }


def test_agent_is_registered_as_streaming():
    assert "brain.agent.run" in handlers.HANDLERS
    assert "brain.agent.run" in handlers.STREAMING
    assert "brain.agent.history.list" in handlers.HANDLERS
    assert "brain.agent.history.read" in handlers.HANDLERS
    assert "brain.agent.history.retry_spec" in handlers.HANDLERS
    assert "brain.agent.history.continue_spec" in handlers.HANDLERS


def test_agent_requires_spec_dict():
    events = []
    from wisp_brain.handlers import StreamContext

    ctx = StreamContext(lambda e, d, r: events.append((e, d)), 1)
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.agent.run"](ctx, spec=None)


def test_agent_fake_llm_completes_in_one_turn(record_ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    events, ctx = record_ctx()
    spec = _noop_spec(tmp_path, title="smoke", objective="do nothing")
    result = handlers.HANDLERS["brain.agent.run"](
        ctx, spec=spec, log_root=str(tmp_path / "runs")
    )

    assert "Fake agent run complete." in result["final"]
    assert result["error"] == ""
    assert result["cancelled"] is False

    run_dir = Path(result["run_dir"])
    assert run_dir.is_dir()
    assert (run_dir / "final.md").read_text(encoding="utf-8") == result["final"]

    # Progress streamed as agent.log, and a terminal agent.done with the result.
    assert _events_of(events, "agent.log"), "expected streamed agent.log lines"
    assert _events_of(events, "agent.done") == [result]


def test_agent_runs_a_scripted_model(record_ctx, tmp_path, monkeypatch):
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [{"thought": "plan", "final": "Scripted final report.", "tool_calls": []}]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WISP_BRAIN_AGENT_TEST_SCRIPT", str(script))

    events, ctx = record_ctx()
    spec = _noop_spec(tmp_path, title="scripted", objective="report")
    result = handlers.HANDLERS["brain.agent.run"](
        ctx, spec=spec, log_root=str(tmp_path / "runs")
    )

    assert result["final"] == "Scripted final report."
    assert result["error"] == ""
    assert _events_of(events, "agent.done") == [result]


def test_agent_history_lists_recent_runs(tmp_path):
    root = tmp_path / "runs"
    old = root / "20260101-010101-old"
    new = root / "20260102-010101-new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "task.json").write_text(json.dumps({"title": "Old", "objective": "Earlier"}), encoding="utf-8")
    (old / "final.md").write_text("Done old", encoding="utf-8")
    (new / "task.json").write_text(json.dumps({"title": "New", "objective": "Later"}), encoding="utf-8")
    (new / "error.txt").write_text("boom", encoding="utf-8")

    result = handlers.HANDLERS["brain.agent.history.list"](log_root=str(root))

    assert result["runs_root"] == str(root)
    assert [run["title"] for run in result["runs"]] == ["New", "Old"]
    assert result["runs"][0]["status"] == "failed"
    assert result["runs"][1]["status"] == "complete"


def test_agent_history_reads_run_artifacts(tmp_path):
    run = tmp_path / "runs" / "20260101-010101-demo"
    run.mkdir(parents=True)
    (run / "task.json").write_text(json.dumps({"title": "Demo", "objective": "Inspect"}), encoding="utf-8")
    (run / "final.md").write_text("Final report", encoding="utf-8")
    (run / "run.log").write_text("[00:00:00] agent run finished", encoding="utf-8")
    (run / "diff.patch").write_text("diff --git a/a b/a", encoding="utf-8")

    result = handlers.HANDLERS["brain.agent.history.read"](run_dir=str(run))

    assert result["title"] == "Demo"
    assert result["objective"] == "Inspect"
    assert result["status"] == "complete"
    assert result["final"] == "Final report"
    assert "agent run finished" in result["run_log"]
    assert result["diff_patch"].startswith("diff --git")


def test_agent_history_retry_and_continue_specs(tmp_path):
    run = tmp_path / "runs" / "20260101-010101-demo"
    run.mkdir(parents=True)
    (run / "task.json").write_text(
        json.dumps({
            "title": "Demo",
            "objective": "Inspect",
            "scope_folder": str(tmp_path),
            "allow_git": False,
            "allow_shell": False,
        }),
        encoding="utf-8",
    )
    (run / "final.md").write_text("Final report", encoding="utf-8")
    (run / "run.log").write_text("[00:00:00] agent turn 1/1: Agent", encoding="utf-8")

    retry = handlers.HANDLERS["brain.agent.history.retry_spec"](run_dir=str(run))["spec"]
    continued = handlers.HANDLERS["brain.agent.history.continue_spec"](run_dir=str(run))["spec"]

    assert retry["title"] == "Demo"
    assert retry["objective"] == "Inspect"
    assert continued["title"] == "Continue: Demo"
    assert "Continuing from previous agent run" in continued["required_context"]
    assert "Previous final report" in continued["required_context"]
