from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "modules/basetool/UPSTREAM.json"
UPSTREAM_REPO = "https://github.com/MatrixTM/MHDDoS.git"


def test_manifest_sha_matches_upstream_tag():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tag = manifest["tag"]
    expected_sha = manifest["sha"]

    completed = subprocess.run(
        ["git", "ls-remote", UPSTREAM_REPO, f"refs/tags/{tag}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"upstream tag {tag} not found"
    remote_sha = lines[0].split()[0]
    assert remote_sha == expected_sha, (
        "Manifest sha is stale — re-run scripts/sync-mhddos-upstream.py"
    )


def test_manifest_lists_current_patches():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    patch_dir = REPO_ROOT / "modules/basetool/upstream/patches"
    on_disk = sorted(path.name for path in patch_dir.glob("*.patch"))
    assert manifest["patches"] == on_disk
