"""Generate the Wisp release manifest consumed by the in-app updater."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _platform_key(name: str) -> str:
    lowered = name.lower()
    arch = "arm64" if "arm64" in lowered or "aarch64" in lowered else "x64"
    if "windows" in lowered:
        return f"windows-{arch}"
    if "macos" in lowered or "darwin" in lowered:
        return f"macos-{arch}"
    if "linux" in lowered:
        return f"linux-{arch}"
    raise ValueError(f"Cannot infer platform key from asset name: {name}")


def _version_from_tag(tag: str) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    if not re.match(r"^\d+\.\d+(?:\.\d+)?(?:[.+-][0-9A-Za-z.-]+)?$", version):
        raise ValueError(f"Release tag does not look like a version: {tag}")
    return version


def build_manifest(asset_paths: list[Path], repo: str, tag: str) -> dict:
    """Build the manifest object for release assets."""
    version = _version_from_tag(tag)
    base_url = f"https://github.com/{repo}/releases/download/{tag}"
    assets = {}
    for path in asset_paths:
        key = _platform_key(path.name)
        assets[key] = {
            "name": path.name,
            "url": f"{base_url}/{path.name}",
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
    return {
        "version": version,
        "notes_url": f"https://github.com/{repo}/releases/tag/{tag}",
        "assets": assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="GitHub repository, e.g. owner/name")
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v0.6 or v0.6.1")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("assets", nargs="+", type=Path)
    args = parser.parse_args()

    manifest = build_manifest(args.assets, repo=args.repo, tag=args.tag)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
