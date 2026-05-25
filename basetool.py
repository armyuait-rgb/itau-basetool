#!/usr/bin/env python3
"""BaseTool runner entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from modules.basetool.runner import AttackManager, console, load_json_safe, resolve_runtime_dir


def load_runtime_config(base_dir: Path) -> tuple[dict, dict | list]:
    from crypto import decrypt_config

    use_plaintext = os.environ.get("BASETOOL_DEV_PLAINTEXT_CONFIGS", "").strip() == "1"
    if use_plaintext:
        config_path = base_dir / "config.json"
        proxy_path = base_dir / "proxy.json"
        if not config_path.exists():
            print("Error: config.json not found")
            sys.exit(1)
        if not proxy_path.exists():
            print("Error: proxy.json not found")
            sys.exit(1)
        return load_json_safe(config_path), load_json_safe(proxy_path)

    config_path = base_dir / "config.enc"
    proxy_path = base_dir / "proxy.enc"
    if not config_path.exists():
        print("Error: config.enc not found")
        sys.exit(1)
    if not proxy_path.exists():
        print("Error: proxy.enc not found")
        sys.exit(1)

    try:
        config = decrypt_config(config_path)
        proxy_providers = decrypt_config(proxy_path)
    except Exception as exc:
        print(f"Error: failed to decrypt runtime config: {exc}")
        sys.exit(1)

    return config, proxy_providers


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="basetool.py",
        description="BaseTool runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Console commands on stdin: start, stop, exit\n"
            "Environment:\n"
            "  BASETOOL_JSON=1          Emit structured monitor telemetry on stdout\n"
            "  BASETOOL_RUNTIME_DIR     Directory containing config.json and proxy.json\n"
            "  BASETOOL_DEV_PLAINTEXT_CONFIGS=1  Use plaintext config.json/proxy.json"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured monitor telemetry on stdout (table on stderr)",
    )
    args = parser.parse_args(argv)

    json_output = args.json or os.environ.get("BASETOOL_JSON", "").strip() == "1"
    base_dir = resolve_runtime_dir()
    config, proxy_providers = load_runtime_config(base_dir)
    manager = AttackManager(config, proxy_providers, json_output=json_output)
    console(manager)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
