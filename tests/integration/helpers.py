from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests/fixtures"
BASETOOL = REPO_ROOT / "basetool.py"
REGRESSION_INPUT = FIXTURES / "regression-input.txt"
MINIMAL_CONFIG = FIXTURES / "minimal-config.json"


def normalize_output(text: str) -> str:
    text = re.sub(r"\[\d{2}:\d{2}:\d{2}\]", "[HH:MM:SS]", text)
    text = re.sub(r"\[\d{2}:\d{2}:\d{2} - INFO\]", "[HH:MM:SS]", text)
    text = re.sub(r"BaseTool> ?", "", text)
    text = re.sub(r"\[HH:MM:SS\] Launching \d+ threads\.\.\.\n", "", text)
    text = re.sub(r"\[HH:MM:SS\] Attack stopped\.\n?", "", text)
    text = re.sub(r"target .+ unreachable: .+\n", "", text)
    text = re.sub(r"PPS: [^\n|]+", "PPS: <N>", text)
    text = re.sub(r"BPS: [^\n]+", "BPS: <N>", text)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"^\{.*\}$", "<JSON>", text, flags=re.MULTILINE)
    text = re.sub(r"\r\n", "\n", text)
    return text.strip() + "\n"


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_POST = do_GET
    do_HEAD = do_GET


def stage_runtime(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(MINIMAL_CONFIG, runtime_dir / "config.json")
    shutil.copy(REPO_ROOT / "proxy.json", runtime_dir / "proxy.json")


def base_env(runtime_dir: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["BASETOOL_DEV_PLAINTEXT_CONFIGS"] = "1"
    if runtime_dir is not None:
        env["BASETOOL_RUNTIME_DIR"] = str(runtime_dir)
    return env


def run_runner(
    *,
    runtime_dir: Path,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    env = base_env(runtime_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(BASETOOL)],
        input=stdin_text,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def with_http_server(port: int = 8081):
    server = HTTPServer(("127.0.0.1", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    return server, thread


def shutdown_http_server(server: HTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(timeout=2)
