from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "modules/basetool/UPSTREAM.json"
ADAPTER_PATH = REPO_ROOT / "modules/basetool/adapter/methods.py"
PATCH_DIR = REPO_ROOT / "modules/basetool/upstream/patches"
START_PATH = REPO_ROOT / "modules/basetool/upstream/mhddos/start.py"
SYNC_SCRIPT = REPO_ROOT / "scripts/sync-mhddos-upstream.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_mhddos_upstream", SYNC_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_apply_check(patch: Path, *, cwd: Path, directory: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "apply", "--check"]
    if directory:
        cmd.append(f"--directory={directory}")
    cmd.append(str(patch))
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def test_method_rename_canary_reports_drift():
    sync = _load_sync_module()
    mutated = START_PATH.read_text(encoding="utf-8").replace(
        "    def GET(self) -> None:", "    def GETZ(self) -> None:", 1
    )
    upstream_methods = sync.parse_upstream_methods(mutated)
    registry_methods = sync.parse_registry_methods(ADAPTER_PATH.read_text(encoding="utf-8"))
    previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    drift = sync.compute_drift(previous, upstream_methods, registry_methods)

    assert "GETZ" in upstream_methods
    assert "GET" in drift["registry_orphans"]


def test_import_side_effects_still_pass_on_vendored_tree():
    probe = REPO_ROOT / "tests/patches/test_import_side_effects.py"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_patch_conflict_canary_fails_apply_check(tmp_path):
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "start.py").write_text(
        "class Layer4:\n    pass\n\nclass HttpFlood:\n    pass\n",
        encoding="utf-8",
    )
    patch = PATCH_DIR / "0001-stats-hook.patch"
    result = _git_apply_check(patch, cwd=clean_dir)
    assert result.returncode != 0
    assert "patch does not apply" in (result.stderr + result.stdout)
