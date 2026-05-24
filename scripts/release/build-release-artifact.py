#!/usr/bin/env python3
"""Build a release tarball for the BaseTool runner."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist"

INCLUDE_PATHS = [
    "basetool.py",
    "config.json",
    "proxy.json",
    "requirements.txt",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "modules",
]

FORBIDDEN_PREFIXES = (
    "tests/",
    "docs/",
    ".github/",
    ".git/",
    "__pycache__/",
    "cache/",
)


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def _git_tag() -> str | None:
    completed = subprocess.run(
        ["git", "describe", "--tags", "--exact-match"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _resolve_version(from_tag: bool) -> str:
    if from_tag:
        tag = _git_tag()
        if not tag:
            raise SystemExit("error: --from-tag requires an exact git tag checkout")
        return tag.lstrip("v")
    return f"dev-{_git_sha()}"


def _should_include(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    if normalized.endswith(".pyc") or "/__pycache__/" in normalized:
        return False
    for prefix in FORBIDDEN_PREFIXES:
        if normalized.startswith(prefix) or f"/{prefix}" in normalized:
            return False
    return True


def _collect_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for entry in INCLUDE_PATHS:
        source = REPO_ROOT / entry
        if not source.exists():
            raise SystemExit(f"error: missing release input {source}")
        if source.is_file():
            files.append((source, entry.replace("\\", "/")))
            continue
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if _should_include(relative):
                files.append((path, relative))
    upstream_manifest = REPO_ROOT / "modules/basetool/UPSTREAM.json"
    if upstream_manifest.exists():
        files.append((upstream_manifest, "modules/basetool/UPSTREAM.json"))
    return files


def build(version: str) -> tuple[Path, Path]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive = DIST_DIR / f"basetool-runner-{version}.tar.gz"
    checksum = Path(f"{archive}.sha256")

    with tarfile.open(archive, "w:gz") as tar:
        for source, arcname in _collect_files():
            tar.add(source, arcname=arcname)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


def main() -> int:
    parser = argparse.ArgumentParser(description="Build BaseTool runner release tarball")
    parser.add_argument("--from-tag", action="store_true", help="Use the current git tag as version")
    args = parser.parse_args()

    version = _resolve_version(args.from_tag)
    archive, checksum = build(version)
    print(f"built {archive}")
    print(f"sha256 {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
