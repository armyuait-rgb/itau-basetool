from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from basetool import main
from modules.basetool.runner.manager import AttackManager


def test_main_missing_config_exits_with_code_1(tmp_path, monkeypatch):
    monkeypatch.setattr("basetool.resolve_runtime_dir", lambda: tmp_path)
    with patch.object(sys, "argv", ["basetool.py"]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1


def test_spawn_threads_requires_targets():
    manager = AttackManager({"settings": {}, "targets": []}, [])
    with pytest.raises(ValueError, match="at least one target"):
        manager._spawn_threads()


def test_spawn_threads_builds_threads(tmp_config):
    config_path = tmp_config()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manager = AttackManager(config, [])
    keys = manager._spawn_threads()
    assert keys == ["127.0.0.1"]
    assert len(manager.threads) == 4
