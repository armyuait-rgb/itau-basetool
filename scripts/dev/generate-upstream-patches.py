#!/usr/bin/env python3
"""Generate upstream patch files against the vendored MHDDoS start.py."""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
START_REL = "modules/basetool/upstream/mhddos/start.py"
START_PATH = REPO_ROOT / START_REL
PATCH_DIR = REPO_ROOT / "modules/basetool/upstream/patches"


def read_start_py() -> str:
    result = subprocess.run(
        ["git", "show", f"HEAD:{START_REL}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def apply_stats_hook(source: str) -> str:
    layer4_start = source.index("class Layer4")
    http_start = source.index("class HttpFlood")
    tools_console_start = source.index("class ToolsConsole")

    head = source[:layer4_start]
    layer4_block = source[layer4_start:http_start]
    http_block = source[http_start:tools_console_start]
    tail = source[tools_console_start:]

    layer4_block = layer4_block.replace("Tools.send(", "self._raw_send(")
    layer4_block = layer4_block.replace("Tools.sendto(", "self._raw_sendto(")
    http_block = http_block.replace("Tools.send(", "self._raw_send(")
    http_block = http_block.replace("Tools.sendto(", "self._raw_sendto(")

    hook_methods = (
        "    def _raw_send(self, sock, payload):\n"
        "        return Tools.send(sock, payload)\n\n"
        "    def _raw_sendto(self, sock, payload, target):\n"
        "        return Tools.sendto(sock, payload, target)\n\n"
    )

    layer4_methods_idx = layer4_block.index("        self.methods = {")
    layer4_block = layer4_block[:layer4_methods_idx] + hook_methods + layer4_block[layer4_methods_idx:]

    http_methods_idx = http_block.index("        self.methods = {")
    http_block = http_block[:http_methods_idx] + hook_methods + http_block[http_methods_idx:]

    return head + layer4_block + http_block + tail


def apply_guard_main(source: str) -> str:
    old_block = (
        "with open(__dir__ / \"config.json\") as f:\n"
        "    con = load(f)\n\n"
        "with socket(AF_INET, SOCK_DGRAM) as s:\n"
        "    s.connect((\"8.8.8.8\", 80))\n"
        "    __ip__ = s.getsockname()[0]\n"
    )
    new_block = (
        "con = None\n"
        "__ip__ = None\n\n\n"
        "def _ensure_runtime_config() -> None:\n"
        "    global con, __ip__\n"
        "    if con is None:\n"
        "        with open(__dir__ / \"config.json\") as f:\n"
        "            con = load(f)\n"
        "    if __ip__ is None:\n"
        "        with socket(AF_INET, SOCK_DGRAM) as s:\n"
        "            s.connect((\"8.8.8.8\", 80))\n"
        "            __ip__ = s.getsockname()[0]\n"
    )
    if old_block not in source:
        raise RuntimeError("expected import-time config block not found")
    source = source.replace(old_block, new_block, 1)

    main_marker = "if __name__ == '__main__':\n"
    if main_marker not in source:
        raise RuntimeError("main guard not found")
    source = source.replace(
        main_marker,
        main_marker + "    _ensure_runtime_config()\n",
        1,
    )
    return source


def write_patch(name: str, original: str, patched: str) -> None:
    rel = "start.py"
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    patch_path = PATCH_DIR / name
    patch_path.write_text("".join(diff), encoding="utf-8")
    print(f"wrote {patch_path}")


def main() -> int:
    original = read_start_py()
    after_stats = apply_stats_hook(original)
    after_guard = apply_guard_main(after_stats)

    write_patch("0001-stats-hook.patch", original, after_stats)
    write_patch("0002-guard-main.patch", after_stats, after_guard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
