from pathlib import Path

import pytest

from macos_py.supervisor import app as supervisor_app


def test_dispatch_module_mode_runs_requested_worker_module(monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor_app.sys, "argv", ["Wisp.exe", "-m", "macos_py.workers.audio_host", "--flag"])
    monkeypatch.setattr(supervisor_app.runpy, "run_module", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(SystemExit) as exc:
        supervisor_app._dispatch_module_mode()

    assert exc.value.code == 0
    assert calls == [
        (
            ("macos_py.workers.audio_host",),
            {"run_name": "__main__", "alter_sys": True},
        )
    ]
    assert supervisor_app.sys.argv == ["macos_py.workers.audio_host", "--flag"]


def test_prepare_run_log_dir_sets_env_and_latest_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir.is_dir()
    assert log_dir.parent == tmp_path / "build_logs"
    assert Path(supervisor_app.os.environ["WISP_RUN_LOG_DIR"]) == log_dir
    assert (tmp_path / "build_logs" / "latest_wisp_runtime.txt").read_text(encoding="utf-8") == str(log_dir)


def test_prepare_run_log_dir_respects_existing_env(tmp_path, monkeypatch):
    configured = tmp_path / "custom-logs"
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(configured))

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir == configured
    assert configured.is_dir()


def test_main_exits_when_single_instance_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: False)

    class ShouldNotStart:
        def __init__(self):
            raise AssertionError("workers should not start for duplicate instance")

    monkeypatch.setattr(supervisor_app, "WispSupervisor", ShouldNotStart)

    assert supervisor_app.main() == 2


def test_main_exits_when_ui_worker_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    instances = []

    class FakeWorker:
        def __init__(self):
            self.exit_handlers = []

        def on_exit(self, handler):
            self.exit_handlers.append(handler)

        def on_event(self, _event, _handler):
            pass

        def call(self, _method, _params=None, *, timeout=30.0, wait=True):
            return {"started": True}

    class FakeSupervisor:
        def __init__(self):
            self.workers = {
                "native": FakeWorker(),
                "ui": FakeWorker(),
                "brain": FakeWorker(),
                "audio": FakeWorker(),
            }
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            return {}

        def shutdown(self):
            self.shutdown_called = True

    class FakeFlowController:
        def __init__(self, *, native, ui, brain, audio):
            self.ui = ui

        def start(self):
            for handler in list(self.ui.exit_handlers):
                handler(0)

        def start_hotkeys(self):
            return {"started": True}

    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlowController)

    assert supervisor_app.main() == 0
    assert instances
    assert instances[0].shutdown_called is True
