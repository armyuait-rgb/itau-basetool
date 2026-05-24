#!/usr/bin/env python3
"""Per-method localhost smoke for the BaseTool runner."""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.basetool.adapter import L4_METHODS, METHOD_REGISTRY, make_attack_thread


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


def _run_method(method: str, host: str, l7_port: int, l4_tcp_port: int, l4_udp_port: int) -> tuple[str, str]:
    from yarl import URL

    if method == "SYN":
        supported, reason = _syn_supported()
        if not supported:
            return "SKIP", reason

    stats = {}
    lock = threading.Lock()
    event = threading.Event()
    event.set()
    target_key = ""
    threads = []

    if method in L4_METHODS:
        if method == "UDP":
            target_key = f"{host}:{l4_udp_port}"
            thread = make_attack_thread(
                method,
                target_key=target_key,
                stats_dict=stats,
                stats_lock=lock,
                synevent=event,
                l4_target=(host, l4_udp_port),
            )
        else:
            target_key = f"{host}:{l4_tcp_port}"
            thread = make_attack_thread(
                method,
                target_key=target_key,
                stats_dict=stats,
                stats_lock=lock,
                synevent=event,
                l4_target=(host, l4_tcp_port),
            )
        threads = [thread]
    else:
        url = URL(f"http://{host}:{l7_port}/")
        target_key = url.host
        for thread_id in range(4):
            threads.append(
                make_attack_thread(
                    method,
                    target_key=target_key,
                    stats_dict=stats,
                    stats_lock=lock,
                    synevent=event,
                    thread_id=thread_id,
                    url=url,
                    host=host,
                    rpc=1,
                    useragents={"Mozilla/5.0"},
                    referers={"https://example.com/"},
                    proxies=None,
                )
            )

    for thread in threads:
        thread.start()
    time.sleep(2)
    event.clear()
    for thread in threads:
        thread.join(timeout=2)

    entry = stats.get(target_key, [0, 0])
    if entry[0] > 0 and entry[1] > 0:
        return "PASS", f"PPS={entry[0]} BPS={entry[1]}"
    return "FAIL", f"no traffic recorded for {target_key}: {entry}"


def main() -> int:
    results: list[tuple[str, str, str]] = []
    with _http_server() as (_h1, l7_port), _tcp_echo(port=8082) as (_h2, l4_tcp_port), _udp_echo(port=8083) as (_h3, l4_udp_port):
        host = "127.0.0.1"
        for method in sorted(METHOD_REGISTRY):
            status, detail = _run_method(method, host, l7_port, l4_tcp_port, l4_udp_port)
            results.append((method, status, detail))
            print(f"{method:8} {status:4} {detail}")

    failures = [row for row in results if row[1] == "FAIL"]
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
