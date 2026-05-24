from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest
from yarl import URL

from modules.basetool.adapter import make_attack_thread
from modules.basetool.runner.manager import AttackManager, console


def test_udp_hook_records_stats(localhost_udp_echo):
    stats = {}
    lock = threading.Lock()
    event = threading.Event()
    event.set()
    with localhost_udp_echo() as (_host, port):
        thread = make_attack_thread(
            "UDP",
            target_key=f"127.0.0.1:{port}",
            stats_dict=stats,
            stats_lock=lock,
            synevent=event,
            l4_target=("127.0.0.1", port),
        )
        import socket as py_socket

        udp = py_socket.socket(py_socket.AF_INET, py_socket.SOCK_DGRAM)
        try:
            thread._raw_sendto(udp, b"data", ("127.0.0.1", port))
        finally:
            udp.close()
    assert stats[f"127.0.0.1:{port}"][0] >= 1


def test_bypass_records_stats(localhost_http_server):
    stats = {}
    lock = threading.Lock()
    event = threading.Event()
    with localhost_http_server() as (_host, port):
        url = URL(f"http://127.0.0.1:{port}/")
        thread = make_attack_thread(
            "BYPASS",
            target_key=url.host,
            stats_dict=stats,
            stats_lock=lock,
            synevent=event,
            url=url,
            host="127.0.0.1",
            rpc=1,
        )
        thread.BYPASS()
    assert stats[url.host][0] >= 1


def test_manager_l4_spawn(localhost_tcp_echo):
    with localhost_tcp_echo() as (_host, port):
        config = {
            "settings": {"threads": 2, "rpc": 1, "proxy": 0},
            "targets": [{"method": "TCP", "target": f"127.0.0.1:{port}", "threads": 2}],
        }
        manager = AttackManager(config, [])
        keys = manager._spawn_threads()
        assert keys == [f"127.0.0.1:{port}"]
        assert len(manager.threads) == 2


def test_manager_start_stop(localhost_http_server):
    with localhost_http_server() as (_host, port):
        config = {
            "settings": {"threads": 2, "rpc": 1, "proxy": 0},
            "targets": [{"method": "GET", "target": f"http://127.0.0.1:{port}/", "threads": 2}],
        }
        manager = AttackManager(config, [])
        with patch("modules.basetool.runner.manager.sys.stdout") as stdout:
            stdout.write = lambda *_args, **_kwargs: None
            manager.start()
            assert manager.event.is_set()
            manager.stop()
            assert not manager.event.is_set()
            manager.start()
            assert manager.event.is_set()


def test_manager_unsupported_method(tmp_config):
    config_path = tmp_config(
        targets=[{"method": "NOPE", "target": "http://127.0.0.1:8081/", "threads": 1}]
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manager = AttackManager(config, [])
    with pytest.raises(ValueError, match="Unsupported method"):
        manager._spawn_threads()


def test_console_exit_stops_manager(localhost_http_server):
    with localhost_http_server() as (_host, port):
        config = {
            "settings": {"threads": 1, "rpc": 1, "proxy": 0},
            "targets": [{"method": "GET", "target": f"http://127.0.0.1:{port}/", "threads": 1}],
        }
        manager = AttackManager(config, [])
        with patch("modules.basetool.runner.manager.sys.stdout") as stdout:
            stdout.write = lambda *_args, **_kwargs: None
            with patch("builtins.input", side_effect=["exit"]):
                console(manager)
        assert not manager.event.is_set()
