#!/usr/bin/env python3
"""Verify a BaseTool runner release tarball."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist"
METHODS_SMOKE = REPO_ROOT / "scripts/smoke/runner-methods-smoke.py"

FORBIDDEN_PREFIXES = (
    "tests/",
    "docs/",
    ".github/",
    "__pycache__/",
)


def _latest_archive() -> Path:
    candidates = sorted(DIST_DIR.glob("basetool-runner-*.tar.gz"))
    if not candidates:
        raise SystemExit(f"error: no release archives found in {DIST_DIR}")
    return candidates[-1]


def _verify_checksum(archive: Path) -> None:
    checksum_path = Path(f"{archive}.sha256")
    if not checksum_path.exists():
        raise SystemExit(f"error: missing checksum file {checksum_path}")
    expected = checksum_path.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected != actual:
        raise SystemExit(f"error: sha256 mismatch expected={expected} actual={actual}")


def _extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.replace("\\", "/")
            for prefix in FORBIDDEN_PREFIXES:
                if name.startswith(prefix) or f"/{prefix}" in name:
                    raise SystemExit(f"error: forbidden tarball entry {name}")
        tar.extractall(destination)


def _run_help(extract_dir: Path) -> None:
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(extract_dir)
    completed = subprocess.run(
        [sys.executable, str(extract_dir / "basetool.py"), "--help"],
        cwd=str(extract_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"error: basetool.py --help failed: {completed.stderr or completed.stdout}")


def _run_methods_smoke(extract_dir: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(METHODS_SMOKE), "--runner-root", str(extract_dir)],
        cwd=str(extract_dir),
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("error: per-method smoke failed against extracted runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify BaseTool runner release tarball")
    parser.add_argument("archive", nargs="?", type=Path, help="Path to basetool-runner-*.tar.gz")
    args = parser.parse_args()

    archive = args.archive.resolve() if args.archive else _latest_archive().resolve()
    if not archive.exists():
        raise SystemExit(f"error: archive not found {archive}")

    _verify_checksum(archive)

    with tempfile.TemporaryDirectory(prefix="basetool-verify-") as tmp:
        extract_dir = Path(tmp)
        _extract(archive, extract_dir)
        _run_help(extract_dir)
        _run_methods_smoke(extract_dir)

    print(f"verified {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
