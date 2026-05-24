#!/usr/bin/env python3
"""BaseTool runner entrypoint."""
from __future__ import annotations

import logging
import sys

from modules.basetool.runner.manager import AttackManager, console, resolve_runtime_dir
from modules.basetool.runner.proxy_manager import load_json_safe

logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("BaseTool")
logger.setLevel(logging.INFO)


def main() -> None:
    base_dir = resolve_runtime_dir()
    config_path = base_dir / "config.json"
    proxy_path = base_dir / "proxy.json"

    if not config_path.exists():
        print("Error: config.json not found")
        sys.exit(1)
    if not proxy_path.exists():
        print("Error: proxy.json not found")
        sys.exit(1)

    config = load_json_safe(config_path)
    proxy_providers = load_json_safe(proxy_path)

    manager = AttackManager(config, proxy_providers)
    console(manager)


if __name__ == "__main__":
    main()
