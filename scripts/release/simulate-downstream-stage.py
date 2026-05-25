#!/usr/bin/env python3
"""Simulate downstream staging and auto-update consumption of a runner tarball."""

from __future__ import annotations

import argparse
import importlib.util
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
from http.server import HTTPServer
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist"
FIXTURES = REPO_ROOT / "tests/fixtures"


def _load_smoke_module(name: str, relative: str):
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


process_driver = _load_smoke_module("process_driver", "scripts/smoke/process_driver.py")
quiet_http = _load_smoke_module("quiet_http", "scripts/smoke/quiet_http.py")
archive_utils = _load_smoke_module("archive_utils", "scripts/release/archive_utils.py")


def _latest_archive() -> Path:
    return archive_utils.latest_archive(DIST_DIR)


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
    env["BASETOOL_DEV_PLAINTEXT_CONFIGS"] = "1"
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
    output: list[str] = []
    reader = process_driver.start_output_reader(proc, output)

    try:
        time.sleep(1)
        process_driver.write_command(proc, "start\n", output_chunks=output, label="console")
        time.sleep(5)
        process_driver.write_command(proc, "stop\n", output_chunks=output, label="console")
        time.sleep(1)
        process_driver.write_command(proc, "exit\n", output_chunks=output, label="console")
        if proc.stdin is not None:
            proc.stdin.close()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise SystemExit("error: console scenario timed out waiting for exit")
    except RuntimeError as exc:
        proc.kill()
        proc.communicate(timeout=5)
        raise SystemExit(f"error: console scenario failed: {exc}") from exc
    finally:
        reader.join(timeout=2)

    if proc.returncode != 0:
        tail = process_driver.tail_output(output)
        raise SystemExit(f"error: console scenario exit code {proc.returncode}\n{tail}")

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
    output: list[str] = []
    reader = process_driver.start_output_reader(proc, output)
    try:
        time.sleep(3)
        _terminate_runner(proc)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise SystemExit("error: SIGTERM scenario timed out")
    finally:
        reader.join(timeout=2)

    if proc.returncode != 0 and platform.system().lower() != "windows":
        tail = process_driver.tail_output(output)
        raise SystemExit(f"error: SIGTERM scenario exit code {proc.returncode}\n{tail}")

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

    server = HTTPServer(("127.0.0.1", 8081), quiet_http.QuietOKHandler)
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
