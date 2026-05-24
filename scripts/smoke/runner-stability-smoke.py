#!/usr/bin/env python3
"""Long-run stability smoke for the BaseTool runner."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.basetool.adapter import L4_METHODS, METHOD_REGISTRY


class _AnyMethodHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def _ok(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        self._ok()

    do_POST = do_GET
    do_HEAD = do_GET


@contextmanager
def _http_server(host: str = "127.0.0.1", port: int = 8081):
    server = HTTPServer((host, port), _AnyMethodHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _tcp_echo(host: str = "127.0.0.1", port: int = 8082):
    stop = threading.Event()

    def worker():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(32)
        sock.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    while chunk := conn.recv(4096):
                        conn.sendall(chunk)
                except OSError:
                    pass
        sock.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        stop.set()
        thread.join(timeout=2)


@contextmanager
def _udp_echo(host: str = "127.0.0.1", port: int = 8083):
    stop = threading.Event()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(0.5)

    def worker():
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                sock.sendto(data, addr)
            except socket.timeout:
                continue
            except OSError:
                break
        sock.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        stop.set()
        thread.join(timeout=2)


def _syn_supported() -> tuple[bool, str]:
    if platform.system().lower() == "windows":
        return False, "SYN requires raw sockets; skipped on Windows"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
        sock.close()
        return True, ""
    except OSError as exc:
        return False, f"SYN skipped: {exc}"


def _target_for_method(method: str, host: str, l7_port: int, l4_tcp_port: int, l4_udp_port: int) -> str:
    if method == "UDP":
        return f"{host}:{l4_udp_port}"
    if method in L4_METHODS:
        return f"{host}:{l4_tcp_port}"
    return f"http://{host}:{l7_port}/"


def _build_config(method: str, host: str, l7_port: int, l4_tcp_port: int, l4_udp_port: int) -> dict:
    return {
        "settings": {"threads": 4, "rpc": 1, "proxy": 0},
        "useragents": ["Mozilla/5.0"],
        "referers": ["https://example.com/"],
        "targets": [
            {
                "method": method,
                "target": _target_for_method(method, host, l7_port, l4_tcp_port, l4_udp_port),
                "threads": 4,
            }
        ],
    }


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


def _rss_growth_ok(samples: list[tuple[float, int]]) -> tuple[bool, str]:
    if len(samples) < 2:
        return True, "insufficient RSS samples"
    warmup = [value for elapsed, value in samples if elapsed >= samples[0][0] + 1]
    baseline = warmup[0] if warmup else samples[0][1]
    peak = max(value for _, value in samples)
    if baseline <= 0:
        return True, "baseline RSS unavailable"
    growth = (peak - baseline) / baseline
    if growth > 0.20:
        return False, f"RSS growth {growth:.1%} exceeds 20%"
    return True, f"RSS growth {growth:.1%}"


def _thread_count_ok(samples: list[tuple[float, int]], warmup_seconds: float) -> tuple[bool, str]:
    after_warmup = [count for elapsed, count in samples if elapsed >= warmup_seconds]
    if len(after_warmup) < 2:
        return True, "insufficient post-warmup thread samples"
    if max(after_warmup) - min(after_warmup) > 4:
        return False, f"thread count drift {min(after_warmup)}..{max(after_warmup)}"
    return True, f"thread count stable at ~{statistics.mean(after_warmup):.0f}"


def _pps_variance_ok(json_rows: list[dict], duration: int) -> tuple[bool, str]:
    if duration < 30:
        return True, "PPS variance check skipped for short runs"
    if not json_rows:
        return False, "no JSON telemetry captured"
    window = max(5, min(30, duration // 2))
    tail = json_rows[-window:]
    values = [row.get("pps", 0) for row in tail if row.get("pps", 0) > 0]
    if len(values) < 2:
        return False, "insufficient PPS samples"
    mean = statistics.mean(values)
    if mean <= 0:
        return False, "PPS mean is zero"
    stdev = statistics.pstdev(values)
    ratio = stdev / mean
    if ratio > 0.30:
        return False, f"PPS variance {ratio:.1%} exceeds 30%"
    return True, f"PPS variance {ratio:.1%}"


def _run_method(
    method: str,
    host: str,
    l7_port: int,
    l4_tcp_port: int,
    l4_udp_port: int,
    duration: int,
    sample_interval: int,
    runner_root: Path,
) -> tuple[str, str]:
    if method == "SYN":
        supported, reason = _syn_supported()
        if not supported:
            return "SKIP", reason

    stage = runner_root / f".stability-stage-{method.lower()}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / "config.json").write_text(
        json.dumps(_build_config(method, host, l7_port, l4_tcp_port, l4_udp_port), indent=2),
        encoding="utf-8",
    )
    shutil.copy(runner_root / "proxy.json", stage / "proxy.json")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(runner_root)
    env["BASETOOL_RUNTIME_DIR"] = str(stage)
    env["BASETOOL_JSON"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, str(runner_root / "basetool.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(runner_root),
        env=env,
        text=True,
    )

    rss_samples: list[tuple[float, int]] = []
    thread_samples: list[tuple[float, int]] = []
    output_chunks: list[str] = []
    start = time.monotonic()

    try:
        ps_proc = psutil.Process(proc.pid)
        while time.monotonic() - start < duration:
            elapsed = time.monotonic() - start
            try:
                rss_samples.append((elapsed, ps_proc.memory_info().rss))
                thread_samples.append((elapsed, ps_proc.num_threads()))
            except psutil.Error:
                pass
            time.sleep(sample_interval)

        proc.stdin.write("stop\nexit\n")
        proc.stdin.flush()
        stdout, _stderr = proc.communicate(timeout=15)
        output_chunks.append(stdout or "")
    except Exception as exc:
        proc.kill()
        proc.communicate(timeout=5)
        return "FAIL", f"runner crashed: {exc}"
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    if proc.returncode != 0:
        return "FAIL", f"exit code {proc.returncode}"

    json_rows = _parse_json_lines("".join(output_chunks))
    checks = [
        _rss_growth_ok(rss_samples),
        _thread_count_ok(thread_samples, warmup_seconds=10),
        _pps_variance_ok(json_rows, duration),
    ]
    failures = [detail for ok, detail in checks if not ok]
    if failures:
        return "FAIL", "; ".join(failures)
    return "PASS", "; ".join(detail for _, detail in checks)


def main() -> int:
    parser = argparse.ArgumentParser(description="BaseTool runner stability smoke")
    parser.add_argument("--method", action="append", help="Limit to one or more methods")
    parser.add_argument("--duration", type=int, default=60, help="Run length in seconds")
    parser.add_argument("--sample-interval", type=int, default=5, help="Sampling interval in seconds")
    parser.add_argument("--runner-root", type=Path, default=REPO_ROOT, help="Runner root directory")
    args = parser.parse_args()

    runner_root = args.runner_root.resolve()
    methods = sorted(args.method) if args.method else sorted(METHOD_REGISTRY)
    unknown = [name for name in methods if name not in METHOD_REGISTRY]
    if unknown:
        print(f"unknown methods: {', '.join(unknown)}", file=sys.stderr)
        return 1

    results: list[tuple[str, str, str]] = []
    with _http_server() as (_h1, l7_port), _tcp_echo(port=8082) as (_h2, l4_tcp_port), _udp_echo(port=8083) as (
        _h3,
        l4_udp_port,
    ):
        host = "127.0.0.1"
        for method in methods:
            status, detail = _run_method(
                method,
                host,
                l7_port,
                l4_tcp_port,
                l4_udp_port,
                args.duration,
                args.sample_interval,
                runner_root,
            )
            results.append((method, status, detail))
            print(f"{method:8} {status:4} {detail}")

    failures = [row for row in results if row[1] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
