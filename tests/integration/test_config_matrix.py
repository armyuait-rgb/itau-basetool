from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from crypto import encrypt_json
from tests.integration.helpers import BASETOOL, REPO_ROOT, base_env, run_runner

_BASETOOL_IMPORT = (
    "from importlib.util import module_from_spec, spec_from_file_location; "
    f"spec = spec_from_file_location('basetool', r'{BASETOOL}'); "
    "mod = module_from_spec(spec); spec.loader.exec_module(mod); "
)


def _write_config(runtime_dir: Path, **overrides) -> None:
    payload = {
        "settings": {"threads": 4, "rpc": 1, "proxy": 0},
        "useragents": ["Mozilla/5.0"],
        "referers": ["https://example.com/"],
        "targets": [
            {"method": "GET", "target": "http://127.0.0.1:8081/", "threads": 4}
        ],
    }
    payload.update(overrides)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "config.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (runtime_dir / "proxy.json").write_text("[]", encoding="utf-8")


def test_plaintext_config_loads(tmp_path):
    _write_config(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(BASETOOL), "--help"],
        cwd=str(REPO_ROOT),
        env=base_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "BASETOOL_RUNTIME_DIR" in completed.stdout


def test_encrypted_config_loads(tmp_path):
    config = {
        "settings": {"threads": 1, "rpc": 1, "proxy": 0},
        "useragents": ["Mozilla/5.0"],
        "referers": ["https://example.com/"],
        "targets": [{"method": "GET", "target": "http://127.0.0.1:8081/", "threads": 1}],
    }
    proxy = []
    (tmp_path / "config.enc").write_bytes(encrypt_json(config))
    (tmp_path / "proxy.enc").write_bytes(encrypt_json(proxy))

    env = base_env(tmp_path)
    env.pop("BASETOOL_DEV_PLAINTEXT_CONFIGS", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _BASETOOL_IMPORT
            + "from modules.basetool.runner import resolve_runtime_dir; "
            "cfg, proxy = mod.load_runtime_config(resolve_runtime_dir()); "
            "assert cfg['targets']",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_missing_config_exits_with_code_1(tmp_path):
    env = base_env(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(BASETOOL)],
        cwd=str(REPO_ROOT),
        env=env,
        input="exit\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "config.json not found" in (completed.stdout + completed.stderr)


def test_empty_targets_exit_nonzero(tmp_path):
    _write_config(tmp_path, targets=[])
    completed = subprocess.run(
        [sys.executable, str(BASETOOL)],
        cwd=str(REPO_ROOT),
        env=base_env(tmp_path),
        input="start\nexit\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode != 0
    assert "at least one target" in (completed.stdout + completed.stderr).lower()


def test_runtime_dir_is_created_and_used(tmp_path):
    runtime_dir = tmp_path / "nested" / "runtime"
    _write_config(runtime_dir)
    cache_dir = runtime_dir / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "proxies.json").write_text(
        json.dumps({"timestamp": 0, "proxies": []}),
        encoding="utf-8",
    )

    env = base_env(runtime_dir)
    completed = subprocess.run(
        [sys.executable, "-c", "from modules.basetool.runner import resolve_runtime_dir; "
         "print(resolve_runtime_dir())"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert str(runtime_dir) in completed.stdout


@pytest.mark.skipif(os.name == "nt", reason="read-only directory semantics differ on Windows")
def test_unwritable_runtime_dir_reports_error(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    os.chmod(blocked, stat.S_IRUSR | stat.S_IXUSR)

    env = base_env(blocked)
    completed = subprocess.run(
        [sys.executable, str(BASETOOL)],
        cwd=str(REPO_ROOT),
        env=env,
        input="exit\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 1
    combined = completed.stdout + completed.stderr
    assert "config.json not found" in combined or "Error:" in combined


def test_runner_plaintext_config(tmp_path):
    _write_config(tmp_path)
    completed = run_runner(runtime_dir=tmp_path, stdin_text="exit\n", timeout=10)
    assert completed.returncode == 0
