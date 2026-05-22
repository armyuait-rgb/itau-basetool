#!/usr/bin/env python3
"""Run MegaTool proxy workability checks outside the interactive console."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def _load_proxy_providers(proxy_path: Path) -> list:
    import re
    import json

    raw = proxy_path.read_text(encoding="utf-8")
    clean = re.sub(r",\s*([}\]])", r"\1", raw)
    data = json.loads(clean)
    if not isinstance(data, list):
        raise ValueError("proxy.json must contain a JSON array")
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download proxies from proxy.json sources and verify they work."
    )
    parser.add_argument(
        "--proxy-file",
        type=Path,
        default=BASE_DIR / "proxy.json",
        help="Path to proxy provider list (default: proxy.json)",
    )
    parser.add_argument(
        "--check-url",
        default=None,
        help="URL used for ProxyChecker (default: http://httpbin.org/get)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache/proxies.json and re-download and re-check",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate proxy.json only; no network or cache access",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    proxy_path = args.proxy_file.resolve()

    if not proxy_path.exists():
        logger.error("Proxy file not found: %s", proxy_path)
        return 2

    try:
        proxy_providers = _load_proxy_providers(proxy_path)
    except (OSError, ValueError) as exc:
        print(f"[check] Invalid proxy file {proxy_path}: {exc}", file=sys.stderr)
        return 2

    if not proxy_providers:
        print("[check] proxy.json must be a non-empty list of providers", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[check] Dry run OK: {len(proxy_providers)} provider(s) in {proxy_path.name}")
        return 0

    from megatool import ProxyManager, logger  # noqa: E402

    if args.force and ProxyManager.CACHE_FILE.exists():
        ProxyManager.CACHE_FILE.unlink()
        logger.info("Cache cleared (--force)")

    proxies = ProxyManager.get_proxies(proxy_providers, args.check_url)
    if not proxies:
        logger.error("Workability check failed: no working proxies")
        return 1

    logger.info("Workability check passed: %d working proxy(ies)", len(proxies))
    return 0


if __name__ == "__main__":
    sys.exit(main())
