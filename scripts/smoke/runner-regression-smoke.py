#!/usr/bin/env python3
"""Regression smoke: compare normalized runner console output to snapshot."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests/fixtures"
INPUT_PATH = FIXTURES / "regression-input.txt"
CONFIG_PATH = FIXTURES / "minimal-config.json"
SNAPSHOT_PATH = FIXTURES / "regression-snapshot-pre.txt"
BASETOOL = REPO_ROOT / "basetool.py"


def normalize_output(text: str) -> str:
    text = re.sub(r"\[\d{2}:\d{2}:\d{2}\]", "[HH:MM:SS]", text)
    text = re.sub(r"\[\d{2}:\d{2}:\d{2} - INFO\]", "[HH:MM:SS]", text)
    text = re.sub(r"BaseTool> ?", "", text)
    text = re.sub(r"\[HH:MM:SS\] Resolved target .+\n", "", text)
    text = re.sub(r"\[HH:MM:SS\] Launching \d+ threads\.\.\.\n", "", text)
    text = re.sub(r"\[HH:MM:SS\] Attack stopped\.\n?", "", text)
    text = re.sub(r"target .+ unreachable: .+\n", "", text)
    text = re.sub(r"PPS: [^\n|]+", "PPS: <N>", text)
    text = re.sub(r"BPS: [^\n]+", "BPS: <N>", text)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"^\{.*\}$", "<JSON>", text, flags=re.MULTILINE)
    text = re.sub(r"\r\n", "\n", text)
    return text.strip() + "\n"


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def _stage_runtime(tmp_dir: Path) -> None:
    shutil.copy(CONFIG_PATH, tmp_dir / "config.json")
    shutil.copy(REPO_ROOT / "proxy.json", tmp_dir / "proxy.json")


def run_runner() -> str:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["BASETOOL_DEV_PLAINTEXT_CONFIGS"] = "1"

    server = HTTPServer(("127.0.0.1", 8081), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        _stage_runtime(stage)
        env["BASETOOL_RUNTIME_DIR"] = str(stage)
        completed = subprocess.run(
            [sys.executable, str(BASETOOL)],
            input=INPUT_PATH.read_text(encoding="utf-8"),
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    server.shutdown()
    thread.join(timeout=2)

    output = completed.stdout + completed.stderr
    if completed.returncode not in (0, None):
        output += f"\nEXIT_CODE={completed.returncode}\n"
    return output


def main() -> int:
    if not SNAPSHOT_PATH.exists():
        print(f"missing snapshot: {SNAPSHOT_PATH}", file=sys.stderr)
        return 1

    actual = normalize_output(run_runner())
    expected = normalize_output(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    if actual == expected:
        print("regression snapshot parity OK")
        return 0

    diff = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile="expected",
        tofile="actual",
    )
    sys.stdout.writelines(diff)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
