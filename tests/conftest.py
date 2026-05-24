from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = REPO_ROOT / "modules/basetool/upstream/patches"
UPSTREAM_TAG = "2.4.4"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def patch_dir(repo_root: Path) -> Path:
    return repo_root / "modules/basetool/upstream/patches"


@pytest.fixture(scope="session")
def upstream_tag() -> str:
    return UPSTREAM_TAG


@pytest.fixture
def tmp_config(tmp_path: Path):
    def _make(**overrides):
        payload = {
            "settings": {"threads": 4, "rpc": 1, "proxy": 0},
            "useragents": ["Mozilla/5.0"],
            "referers": ["https://example.com/"],
            "targets": [
                {"method": "GET", "target": "http://127.0.0.1:8081/", "threads": 4}
            ],
        }
        payload.update(overrides)
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    return _make
