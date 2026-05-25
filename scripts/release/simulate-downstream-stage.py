#!/usr/bin/env python3
"""Simulate downstream staging and auto-update consumption of a runner tarball."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist"
FIXTURES = REPO_ROOT / "tests/fixtures"


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def _latest_archive() -> Path:
    candidates = sorted(DIST_DIR.glob("basetool-runner-*.tar.gz"))
    if not candidates:
        raise SystemExit(f"error: no release archives found in {DIST_DIR}")
    return candidates[-1]


def _extract_runner(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(destination)
    return destination


def _stage_runtime(extract_dir: Path, runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "minimal-config.json", runtime_dir / "config.json")
    shutil.copy(extract_dir / "proxy.json", runtime_dir / "proxy.json")


def _parse_json_lines(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        start = line.find("{")
        if start == -1:
            continue
        try:
            rows.append(json.loads(line[start:]))
        except json.JSONDecodeError:
            continue
    return rows


def _child_orphans(parent_pid: int) -> list[psutil.Process]:
    try:
        parent = psutil.Process(parent_pid)
    except psutil.Error:
        return []
    survivors = []
    for child in parent.children(recursive=True):
        if child.is_running() and child.pid != parent_pid:
            survivors.append(child)
    return survivors


def _launch_runner(extract_dir: Path, runtime_dir: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(extract_dir)
    env["BASETOOL_RUNTIME_DIR"] = str(runtime_dir)
    env["BASETOOL_JSON"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, str(extract_dir / "basetool.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(extract_dir),
        env=env,
        text=True,
    )


def _console_scenario(extract_dir: Path, runtime_dir: Path) -> None:
    proc = _launch_runner(extract_dir, runtime_dir)
    assert proc.stdin is not None
    assert proc.stdout is not None

    output: list[str] = []

    def _reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    time.sleep(1)
    proc.stdin.write("start\n")
    proc.stdin.flush()
    time.sleep(5)
    proc.stdin.write("stop\n")
    proc.stdin.flush()
    time.sleep(1)
    proc.stdin.write("exit\n")
    proc.stdin.flush()

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise SystemExit("error: console scenario timed out waiting for exit")

    if proc.returncode != 0:
        raise SystemExit(f"error: console scenario exit code {proc.returncode}")

    json_rows = _parse_json_lines("".join(output))
    if not any(row.get("pps", 0) > 0 for row in json_rows):
        raise SystemExit("error: console scenario saw no PPS > 0 in JSON telemetry")

    orphans = _child_orphans(proc.pid)
    if orphans:
        raise SystemExit(f"error: console scenario left orphan processes: {[p.pid for p in orphans]}")


def _terminate_runner(proc: subprocess.Popen[str]) -> None:
    if platform.system().lower() == "windows":
        psutil.Process(proc.pid).terminate()
        return
    if hasattr(signal, "SIGTERM"):
        proc.send_signal(signal.SIGTERM)
    else:
        proc.terminate()


def _sigterm_scenario(extract_dir: Path, runtime_dir: Path) -> None:
    proc = _launch_runner(extract_dir, runtime_dir)
    time.sleep(3)
    _terminate_runner(proc)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise SystemExit("error: SIGTERM scenario timed out")

    if proc.returncode != 0:
        raise SystemExit(f"error: SIGTERM scenario exit code {proc.returncode}")

    orphans = _child_orphans(proc.pid)
    if orphans:
        raise SystemExit(f"error: SIGTERM scenario left orphan processes: {[p.pid for p in orphans]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate downstream staging of a runner tarball")
    parser.add_argument("archive", nargs="?", type=Path, help="Path to basetool-runner-*.tar.gz")
    args = parser.parse_args()

    archive = args.archive.resolve() if args.archive else _latest_archive().resolve()
    if not archive.exists():
        raise SystemExit(f"error: archive not found {archive}")

    server = HTTPServer(("127.0.0.1", 8081), _QuietHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.2)

    try:
        with tempfile.TemporaryDirectory(prefix="basetool-stage-") as tmp:
            extract_dir = _extract_runner(archive, Path(tmp) / "runner")
            runtime_dir = Path(tmp) / "runtime"
            _stage_runtime(extract_dir, runtime_dir)

            required = [extract_dir / "basetool.py", extract_dir / "modules" / "basetool"]
            for path in required:
                if not path.exists():
                    raise SystemExit(f"error: staged layout missing {path.relative_to(extract_dir)}")

            _console_scenario(extract_dir, runtime_dir)
            _sigterm_scenario(extract_dir, runtime_dir)
    finally:
        server.shutdown()
        server_thread.join(timeout=2)

    print(f"downstream stage simulation OK for {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
