from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from PyRoxy import Proxy, ProxyType

REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = REPO_ROOT / "modules/basetool/upstream/patches"
UPSTREAM_TAG = "2.4.4"
FIXTURES_DIR = REPO_ROOT / "tests/fixtures"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def patch_dir(repo_root: Path) -> Path:
    return repo_root / "modules/basetool/upstream/patches"


@pytest.fixture(scope="session")
def upstream_tag() -> str:
    return UPSTREAM_TAG


@pytest.fixture
def tmp_config(tmp_path: Path):
    def _make(**overrides):
        payload = {
            "settings": {"threads": 4, "rpc": 1, "proxy": 0},
            "useragents": ["Mozilla/5.0"],
            "referers": ["https://example.com/"],
            "targets": [
                {"method": "GET", "target": "http://127.0.0.1:8081/", "threads": 4}
            ],
        }
        payload.update(overrides)
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    return _make


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_POST = do_GET
    do_HEAD = do_GET


@contextmanager
def _serve_http(host: str, port: int):
    server = HTTPServer((host, port), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _serve_tcp_echo(host: str, port: int):
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
def _serve_udp_echo(host: str, port: int):
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


@pytest.fixture
def localhost_http_server():
    @contextmanager
    def _open(host: str = "127.0.0.1", port: int = 0):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind((host, port))
        chosen_port = probe.getsockname()[1]
        probe.close()
        with _serve_http(host, chosen_port) as endpoint:
            yield endpoint

    return _open


@pytest.fixture
def localhost_tcp_echo():
    @contextmanager
    def _open(host: str = "127.0.0.1", port: int = 0):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind((host, port))
        chosen_port = probe.getsockname()[1]
        probe.close()
        with _serve_tcp_echo(host, chosen_port) as endpoint:
            yield endpoint

    return _open


@pytest.fixture
def localhost_udp_echo():
    @contextmanager
    def _open(host: str = "127.0.0.1", port: int = 0):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind((host, port))
        chosen_port = probe.getsockname()[1]
        probe.close()
        with _serve_udp_echo(host, chosen_port) as endpoint:
            yield endpoint

    return _open


@pytest.fixture
def mock_proxy_pool(monkeypatch):
    def _make(n: int = 5):
        proxies = [Proxy("127.0.0.1", 8000 + index, ProxyType.HTTP) for index in range(n)]

        def _load_cache():
            return proxies

        monkeypatch.setattr(
            "modules.basetool.runner.proxy_manager.ProxyManager._load_cache",
            staticmethod(_load_cache),
        )
        return proxies

    return _make
