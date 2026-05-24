#!/usr/bin/env python3
"""BaseTool runner entrypoint."""
from __future__ import annotations

import logging
import os
import sys

from modules.basetool.runner.manager import AttackManager, console
from modules.basetool.runner.proxy_manager import load_json_safe
from modules.basetool.runner.runtime import resolve_runtime_dir

logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("BaseTool")
logger.setLevel(logging.INFO)


def json_output_enabled() -> bool:
    if os.environ.get("BASETOOL_JSON") == "1":
        return True
    return "--json" in sys.argv


def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: python basetool.py [--json]")
        print("Environment: BASETOOL_JSON=1 enables structured monitor output.")
        sys.exit(0)

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

    manager = AttackManager(config, proxy_providers, json_output=json_output_enabled())
    console(manager)


if __name__ == "__main__":
    main()
