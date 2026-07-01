"""Tests for runtime optional dependency installation helpers."""

from __future__ import annotations

import sys


def test_optional_deps_dev_install_uses_current_python(monkeypatch, tmp_path):
    """Repo/dev launches should keep using the active Python's pip."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "frozen", False, raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", sys.executable)

    command = optional_deps.pip_install_command(["elevenlabs>=1.0.0"])

    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--progress-bar=raw" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-1] == "elevenlabs>=1.0.0"


def test_optional_deps_frozen_install_uses_bundled_uv(monkeypatch, tmp_path):
    """Packaged launches must not run Wisp.exe as `python -m pip`."""
    from core import optional_deps

    suffix = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "_internal"
    uv = bundle / "bin" / f"uv{suffix}"
    uv.parent.mkdir(parents=True)
    uv.write_text("", encoding="utf-8")
    target = tmp_path / "data" / "python_packages"
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    command = optional_deps.pip_install_command(["kokoro>=0.9.4", "soundfile"])

    assert command[:3] == [str(uv), "pip", "install"]
    assert "-m" not in command
    assert "--python-version" in command
    assert command[command.index("--target") + 1] == str(target)
    assert command[-2:] == ["kokoro>=0.9.4", "soundfile"]


def test_optional_deps_frozen_install_explains_missing_uv(monkeypatch, tmp_path):
    """A packaged build without bundled uv should fail clearly before starting pip."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "REPO_ROOT", tmp_path / "data")
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(optional_deps.sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(optional_deps.sys, "executable", str(tmp_path / "Wisp.exe"))
    monkeypatch.setattr(optional_deps.shutil, "which", lambda _name: "")

    try:
        optional_deps.pip_install_command(["kokoro>=0.9.4"])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected missing uv to raise")

    assert "uv was not bundled" in message
    assert "rebuild Wisp" in message


def test_optional_deps_install_env_sets_uv_http_timeout(monkeypatch):
    """Optional installs should not hang indefinitely on a silent uv network fetch."""
    from core import optional_deps

    monkeypatch.delenv("UV_HTTP_TIMEOUT", raising=False)

    env = optional_deps.pip_install_env()

    assert env["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"
    assert env["UV_HTTP_TIMEOUT"] == "60"


def test_optional_packages_precede_bundled_packages(monkeypatch, tmp_path):
    """A target install must resolve its own dependency versions as one layer."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    target.mkdir()
    path = str(target)
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "path", ["bundle", path, "stdlib"])

    optional_deps.add_optional_packages_to_path(prepend=True)

    assert optional_deps.sys.path == [path, "bundle", "stdlib"]


def test_optional_packages_preserve_bundled_precedence_by_default(monkeypatch, tmp_path):
    """Non-provider workers should not load duplicate target dependencies first."""
    from core import optional_deps

    target = tmp_path / "python_packages"
    target.mkdir()
    path = str(target)
    monkeypatch.setattr(optional_deps, "OPTIONAL_PACKAGES_DIR", target)
    monkeypatch.setattr(optional_deps.sys, "path", ["bundle", "stdlib"])
    monkeypatch.setattr(optional_deps.site, "addsitedir", lambda value: optional_deps.sys.path.append(value))

    optional_deps.add_optional_packages_to_path()

    assert optional_deps.sys.path == ["bundle", "stdlib", path]


def test_kokoro_install_includes_persistent_english_g2p_model():
    """The target install must avoid spaCy downloading into the frozen runtime."""
    from core import optional_deps

    assert optional_deps.KOKORO_INSTALL_PACKAGES[:2] == ["kokoro>=0.9.4", "soundfile"]
    assert optional_deps.KOKORO_INSTALL_PACKAGES[2].endswith(
        "/en_core_web_sm-3.8.0-py3-none-any.whl"
    )


def test_kokoro_gpu_install_includes_cuda_torch_index():
    """GPU Kokoro installs must request CUDA Torch wheels explicitly."""
    from core import optional_deps

    packages = optional_deps.kokoro_install_packages("cuda")

    assert packages[:4] == [
        "--upgrade",
        "--extra-index-url",
        optional_deps.PYTORCH_CUDA_WHEEL_INDEX,
        "torch",
    ]
    assert "kokoro>=0.9.4" in packages
    assert any(str(item).endswith("/en_core_web_sm-3.8.0-py3-none-any.whl") for item in packages)


def test_kokoro_auto_install_selects_gpu_when_cuda_detected(monkeypatch):
    """Auto should install the GPU stack only when the host has CUDA."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: True)

    assert optional_deps.kokoro_install_mode_for_device("auto") == "gpu"
    assert "torch" in optional_deps.kokoro_install_packages("auto")


def test_kokoro_auto_install_selects_cpu_without_cuda(monkeypatch):
    """Auto should keep the smaller CPU stack on machines without CUDA."""
    from core import optional_deps

    monkeypatch.setattr(optional_deps, "system_cuda_available", lambda: False)

    assert optional_deps.kokoro_install_mode_for_device("auto") == "cpu"
    assert "torch" not in optional_deps.kokoro_install_packages("auto")
