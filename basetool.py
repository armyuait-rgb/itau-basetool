#!/usr/bin/env python3
"""BaseTool runner entrypoint (public dev entrypoint)."""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import os
import sys
import threading
import traceback
from pathlib import Path


logging.basicConfig(
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

from modules.basetool.runner import AttackManager, console, load_json_safe


def format_uncaught_exception(exc_type, exc_value, exc_tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))


def log_uncaught_exception(exc_type, exc_value, exc_tb) -> None:
    if isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    text = format_uncaught_exception(exc_type, exc_value, exc_tb)
    try:
        sys.stderr.write("Uncaught exception:\n")
        sys.stderr.write(text)
        if not text.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()
    except Exception:
        fallback = getattr(sys, "__stderr__", None)
        if fallback is None:
            return
        try:
            fallback.write(text)
            fallback.flush()
        except Exception:
            pass


def _log_uncaught_thread_exception(args: threading.ExceptHookArgs) -> None:
    log_uncaught_exception(args.exc_type, args.exc_value, args.exc_traceback)


def install_faulthandler() -> None:
    try:
        import faulthandler

        faulthandler.enable(file=sys.stderr, all_threads=True)
    except Exception:
        pass


def install_uncaught_exception_hooks() -> None:
    install_faulthandler()
    sys.excepthook = log_uncaught_exception
    threading.excepthook = _log_uncaught_thread_exception


def resolve_runtime_dir() -> Path:
    """Return the directory containing config.json / proxy.json for this process."""
    override = os.environ.get("BASETOOL_RUNTIME_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path.cwd()


def main():
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

    mgr = AttackManager(config, proxy_providers)
    console(mgr)

if __name__ == "__main__":
    main()
