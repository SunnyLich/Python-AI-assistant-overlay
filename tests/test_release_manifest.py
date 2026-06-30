from __future__ import annotations

from pathlib import Path

import pytest

from scripts import make_release_manifest


def test_release_manifest_accepts_minor_only_v_tag(tmp_path: Path) -> None:
    asset = tmp_path / "Wisp-v0.6-linux-x64.tar.gz"
    asset.write_bytes(b"release")

    manifest = make_release_manifest.build_manifest(
        [asset],
        repo="SunnyLich/Python-AI-assistant-overlay",
        tag="v0.6",
    )

    assert manifest["version"] == "0.6"
    assert manifest["notes_url"].endswith("/releases/tag/v0.6")
    assert manifest["assets"]["linux-x64"]["name"] == asset.name


def test_release_manifest_accepts_patch_v_tag(tmp_path: Path) -> None:
    asset = tmp_path / "Wisp-v0.6.1-windows-x64.zip"
    asset.write_bytes(b"release")

    manifest = make_release_manifest.build_manifest(
        [asset],
        repo="SunnyLich/Python-AI-assistant-overlay",
        tag="v0.6.1",
    )

    assert manifest["version"] == "0.6.1"
    assert manifest["assets"]["windows-x64"]["sha256"] == make_release_manifest._sha256(asset)


def test_release_manifest_rejects_non_version_tag(tmp_path: Path) -> None:
    asset = tmp_path / "Wisp-latest-linux-x64.tar.gz"
    asset.write_bytes(b"release")

    with pytest.raises(ValueError, match="Release tag does not look like a version"):
        make_release_manifest.build_manifest(
            [asset],
            repo="SunnyLich/Python-AI-assistant-overlay",
            tag="latest",
        )
