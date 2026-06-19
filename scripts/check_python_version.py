"""Check that the running interpreter matches Wisp's pinned Python version."""

from __future__ import annotations

import argparse
import sys


def parse_version(value: str) -> tuple[int, int, int]:
    parts = value.strip().split(".")
    if len(parts) != 3:
        raise ValueError("expected a version like 3.12.13")
    try:
        major, minor, micro = (int(part) for part in parts)
        return (major, minor, micro)
    except ValueError as exc:
        raise ValueError("version parts must be integers") from exc


def version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def current_version() -> tuple[int, int, int]:
    info = sys.version_info
    return (info.major, info.minor, info.micro)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", help="exact Python version required, for example 3.12.13")
    parser.add_argument("--label", default="Python", help="interpreter label used in error output")
    args = parser.parse_args(argv)

    try:
        expected = parse_version(args.expected)
    except ValueError as exc:
        print(f"Invalid expected Python version {args.expected!r}: {exc}.", file=sys.stderr)
        return 2

    actual = current_version()
    if actual != expected:
        print(
            f"{args.label} {version_text(expected)} is required, "
            f"but this interpreter is {version_text(actual)}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
