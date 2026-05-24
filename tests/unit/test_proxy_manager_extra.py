from __future__ import annotations

import json
import time

from modules.basetool.runner.proxy_manager import ProxyManager


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


def test_load_cache_valid_returns_proxies(tmp_path, monkeypatch):
    cache = tmp_path / "proxies.json"
    cache.write_text(
        json.dumps({"timestamp": time.time(), "proxies": ["127.0.0.1:8080"]}),
        encoding="utf-8",
    )
    _patch_cache_file(monkeypatch, cache)
    loaded = ProxyManager._load_cache()
    assert loaded is not None
    assert loaded[0].port == 8080


def test_get_proxies_empty_providers_returns_none():
    assert ProxyManager.get_proxies([]) is None


def test_download_one_parses_lines(monkeypatch):
    class Response:
        text = "127.0.0.1:9000\nbad-line\n"

    monkeypatch.setattr(
        "modules.basetool.runner.proxy_manager.get",
        lambda *_args, **_kwargs: Response(),
    )
    result = ProxyManager._download_one({"type": "http", "url": "http://example.com", "timeout": 1})
    assert len(result) >= 1


def test_download_one_handles_failure(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr("modules.basetool.runner.proxy_manager.get", _boom)
    assert ProxyManager._download_one({"type": "http", "url": "http://example.com"}) == set()


def test_get_proxies_downloads_when_cache_missing(tmp_path, monkeypatch):
    _patch_cache_file(monkeypatch, tmp_path / "missing.json")
    monkeypatch.setattr(
        ProxyManager,
        "_download_one",
        staticmethod(lambda _provider: set()),
    )
    monkeypatch.setattr(
        "modules.basetool.runner.proxy_manager.ProxyChecker.checkAll",
        lambda *_args, **_kwargs: set(),
    )
    assert ProxyManager.get_proxies([{"type": "http", "url": "http://example.com"}]) is None
