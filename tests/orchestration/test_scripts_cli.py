from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "modules/basetool/UPSTREAM.json"
DIST_DIR = REPO_ROOT / "dist"


def _run_script(relative: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    script = REPO_ROOT / relative
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("relative", "args", "expected_exit"),
    [
        ("scripts/sync-mhddos-upstream.py", ("--help",), 0),
        ("scripts/release/build-release-artifact.py", ("--help",), 0),
        ("scripts/release/verify-release-artifact.py", ("--help",), 0),
        ("scripts/release/simulate-downstream-stage.py", ("--help",), 0),
    ],
)
def test_script_help_exits_zero(relative: str, args: tuple[str, ...], expected_exit: int):
    completed = _run_script(relative, *args)
    assert completed.returncode == expected_exit, completed.stderr or completed.stdout
    assert "usage:" in (completed.stdout + completed.stderr).lower()


def test_sync_manifest_refresh_is_idempotent():
    before = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    completed = _run_script(
        "scripts/sync-mhddos-upstream.py",
        "--tag",
        "2.4.4",
        "--no-smoke",
        "--skip-subtree",
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    after = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    before_no_date = {key: value for key, value in before.items() if key != "sync_date"}
    after_no_date = {key: value for key, value in after.items() if key != "sync_date"}
    assert before_no_date == after_no_date


def test_build_release_artifact_dev_version():
    completed = _run_script("scripts/release/build-release-artifact.py")
    assert completed.returncode == 0, completed.stderr or completed.stdout
    archives = sorted(DIST_DIR.glob("basetool-runner-dev-*.tar.gz"))
    assert archives, "expected dev release archive"
    assert archives[-1].with_suffix(".tar.gz.sha256").exists() or Path(
        f"{archives[-1]}.sha256"
    ).exists()


def test_verify_release_artifact_requires_archive():
    completed = _run_script(
        "scripts/release/verify-release-artifact.py",
        "dist/does-not-exist.tar.gz",
    )
    assert completed.returncode != 0
    assert "not found" in (completed.stderr or completed.stdout).lower()


def test_generate_upstream_patches_is_present():
    script = REPO_ROOT / "scripts/dev/generate-upstream-patches.py"
    assert script.exists()
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        assert "wrote" in (completed.stdout + completed.stderr).lower()
    else:
        assert "RuntimeError" in (completed.stderr + completed.stdout)
