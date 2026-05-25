from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist"
MANIFEST_FIXTURE = REPO_ROOT / "tests/fixtures/release-tarball-manifest.txt"
FORBIDDEN_PREFIXES = ("tests/", "docs/", ".github/", "__pycache__/", "cache/")


def _build_archive() -> Path:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/release/build-release-artifact.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    candidates = list(DIST_DIR.glob("basetool-runner-*.tar.gz"))
    assert candidates
    return max(candidates, key=lambda path: path.stat().st_mtime)


def test_tarball_layout_matches_golden_manifest():
    archive = _build_archive()
    with tarfile.open(archive, "r:gz") as tar:
        names = sorted(tar.getnames())

    assert len(names) == len(set(names)), f"duplicate tarball entries in {archive.name}"
    expected = [
        line.strip()
        for line in MANIFEST_FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert names == expected


def test_tarball_excludes_forbidden_prefixes():
    archive = _build_archive()
    with tarfile.open(archive, "r:gz") as tar:
        for name in tar.getnames():
            normalized = name.replace("\\", "/")
            for prefix in FORBIDDEN_PREFIXES:
                assert not normalized.startswith(prefix)
                assert f"/{prefix}" not in normalized
