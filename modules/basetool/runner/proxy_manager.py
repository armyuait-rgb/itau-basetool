from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Set

from PyRoxy import Proxy, ProxyChecker, ProxyType, ProxyUtiles
from requests import get

logger = logging.getLogger("BaseTool")


def load_json_safe(filepath: Path) -> dict | list:
    raw = filepath.read_text(encoding="utf-8")
    clean = re.sub(r',\s*([}\]])', r'\1', raw)
    return json.loads(clean)


from .runtime import resolve_runtime_dir


class ProxyManager:
    CACHE_MAX_AGE = 86400

    @classmethod
    def _base_dir(cls) -> Path:
        return resolve_runtime_dir()

    @classmethod
    def _cache_dir(cls) -> Path:
        return cls._base_dir() / "cache"

    @classmethod
    def _cache_file(cls) -> Path:
        return cls._cache_dir() / "proxies.json"

    @staticmethod
    def _load_cache():
        if not ProxyManager._cache_file().exists():
            return None
        try:
            data = load_json_safe(ProxyManager._cache_file())
            age = time.time() - data.get("timestamp", 0)
            if age < ProxyManager.CACHE_MAX_AGE:
                proxies = []
                for line in data.get("proxies", []):
                    for ptype in (ProxyType.HTTP, ProxyType.SOCKS4, ProxyType.SOCKS5):
                        parsed = ProxyUtiles.parseAllIPPort([line], ptype)
                        if parsed:
                            proxies.extend(parsed)
                            break
                if proxies:
                    logger.info(f"Loaded {len(proxies)} proxies from cache")
                    return proxies
            else:
                logger.info("Cache expired, reloading...")
        except Exception as exc:
            logger.warning(f"Cache read error: {exc}")
        return None

    @staticmethod
    def _save_cache(proxies: List[Proxy]):
        cache_dir = ProxyManager._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = ProxyManager._cache_file()
        lines = [f"{p.host}:{p.port}" for p in proxies]
        data = {"timestamp": time.time(), "proxies": lines}
        try:
            with open(cache_file, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            logger.debug("Proxy cache saved.")
        except OSError as exc:
            logger.warning(f"Could not save proxy cache: {exc}")

    @staticmethod
    def get_proxies(proxy_providers: list, check_url: str = None) -> Optional[List[Proxy]]:
        if not proxy_providers:
            return None

        cached = ProxyManager._load_cache()
        if cached:
            return cached

        all_proxies = set()
        with ThreadPoolExecutor(max_workers=len(proxy_providers)) as executor:
            futures = {
                executor.submit(ProxyManager._download_one, provider): provider
                for provider in proxy_providers
            }
            for future in as_completed(futures):
                try:
                    all_proxies.update(future.result())
                except Exception as exc:
                    logger.error(f"Download error: {exc}")

        logger.info(f"Downloaded {len(all_proxies)} proxies, verifying...")
        test_url = check_url or "http://httpbin.org/get"
        working = ProxyChecker.checkAll(all_proxies, timeout=5, threads=100, url=test_url)
        if not working:
            logger.error("No working proxies found.")
            return None
        logger.info(f"{len(working)} proxies ready.")
        ProxyManager._save_cache(list(working))
        return list(working)

    @staticmethod
    def _download_one(provider: dict) -> Set[Proxy]:
        proxy_type = ProxyType.stringToProxyType(str(provider.get("type", "http")))
        url = provider["url"]
        timeout = provider.get("timeout", 10)
        try:
            resp = get(url, timeout=timeout)
            proxies = set()
            for line in resp.text.splitlines():
                for proxy in ProxyUtiles.parseAllIPPort([line], proxy_type):
                    proxies.add(proxy)
            return proxies
        except Exception as exc:
            logger.debug(f"Failed to download from {url}: {exc}")
            return set()
