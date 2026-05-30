from __future__ import annotations

import json
import time

import pytest

from modules.basetool.runner.proxy_manager import ProxyManager, load_json_safe


def _patch_cache_file(monkeypatch, cache_path):
    monkeypatch.setattr(
        ProxyManager,
        "_cache_file",
        classmethod(lambda cls: cache_path),
    )
    monkeypatch.setattr(
        ProxyManager,
        "_cache_dir",
        classmethod(lambda cls: cache_path.parent),
    )


def test_load_json_safe_accepts_trailing_commas(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"a": 1,}', encoding="utf-8")
    assert load_json_safe(path) == {"a": 1}


def test_load_json_safe_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_json_safe(path)


def test_load_cache_missing_returns_none(tmp_path, monkeypatch):
    _patch_cache_file(monkeypatch, tmp_path / "missing.json")
    assert ProxyManager._load_cache() is None


def test_load_cache_expired_returns_none(tmp_path, monkeypatch):
    cache = tmp_path / "proxies.json"
    cache.write_text(
        json.dumps({"timestamp": time.time() - ProxyManager.CACHE_MAX_AGE - 1, "proxies": ["1.1.1.1:8080"]}),
        encoding="utf-8",
    )
    _patch_cache_file(monkeypatch, cache)
    assert ProxyManager._load_cache() is None


def test_save_and_load_cache_roundtrip(tmp_path, monkeypatch):
    from PyRoxy import Proxy, ProxyType

    cache = tmp_path / "proxies.json"
    _patch_cache_file(monkeypatch, cache)
    proxy = Proxy("127.0.0.1", 8080, ProxyType.HTTP)
    ProxyManager._save_cache([proxy])
    loaded = ProxyManager._load_cache()
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].host == "127.0.0.1"
    assert loaded[0].port == 8080


def test_get_proxies_short_circuits_on_fresh_cache(mock_proxy_pool, monkeypatch):
    mock_proxy_pool(3)

    def _fail_download(_provider):
        raise AssertionError("download should not run when cache is fresh")

    monkeypatch.setattr(ProxyManager, "_download_one", staticmethod(_fail_download))
    result = ProxyManager.get_proxies([{"type": "http", "url": "http://example.com/list"}])
    assert result is not None
    assert len(result) == 3
