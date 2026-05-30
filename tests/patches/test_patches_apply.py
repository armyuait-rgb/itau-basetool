from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

UPSTREAM_REPO = "https://github.com/MatrixTM/MHDDoS.git"


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def clean_upstream_tree(upstream_tag: str) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for patch apply tests")

    tmp = Path(tempfile.mkdtemp(prefix="mhddos-upstream-"))
    try:
        _run_git(tmp, "init")
        _run_git(tmp, "remote", "add", "origin", UPSTREAM_REPO)
        _run_git(tmp, "fetch", "--depth", "1", "origin", f"refs/tags/{upstream_tag}")
        _run_git(tmp, "checkout", "FETCH_HEAD")
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        pytest.skip(f"unable to fetch upstream tag {upstream_tag}: {exc.stderr.strip()}")

    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _patch_paths(patch_dir: Path) -> list[Path]:
    return sorted(patch_dir.glob("*.patch"))


def test_patch_files_exist(patch_dir: Path) -> None:
    patches = _patch_paths(patch_dir)
    assert patches, "expected at least one upstream patch"
    names = [p.name for p in patches]
    assert "0001-stats-hook.patch" in names
    assert "0002-guard-main.patch" in names


def test_patches_apply_to_clean_upstream_tag(
    clean_upstream_tree: Path,
    patch_dir: Path,
) -> None:
    start_py = clean_upstream_tree / "start.py"
    assert start_py.exists(), "upstream checkout missing start.py"

    for patch in _patch_paths(patch_dir):
        subprocess.run(
            ["git", "apply", "--check", str(patch.resolve())],
            cwd=clean_upstream_tree,
            check=True,
            capture_output=True,
            text=True,
        )


def test_patches_apply_in_order(
    clean_upstream_tree: Path,
    patch_dir: Path,
) -> None:
    for patch in _patch_paths(patch_dir):
        subprocess.run(
            ["git", "apply", str(patch.resolve())],
            cwd=clean_upstream_tree,
            check=True,
            capture_output=True,
            text=True,
        )

    text = (clean_upstream_tree / "start.py").read_text(encoding="utf-8")
    assert "def _raw_send(self, sock, payload):" in text
    assert "def _ensure_runtime_config() -> None:" in text
