from __future__ import annotations

import threading

import pytest
from yarl import URL

from modules.basetool.adapter import METHOD_REGISTRY, make_attack_thread


def test_method_registry_shape():
    for name, entry in METHOD_REGISTRY.items():
        assert set(entry) >= {"cls", "fn", "caps"}
        assert isinstance(name, str)


def test_method_registry_callables():
    for entry in METHOD_REGISTRY.values():
        assert callable(getattr(entry["cls"], entry["fn"], None))


def test_unknown_method_raises():
    with pytest.raises(KeyError):
        make_attack_thread(
            "UNKNOWN",
            target_key="127.0.0.1:9",
            stats_dict={},
            stats_lock=threading.Lock(),
            synevent=threading.Event(),
            l4_target=("127.0.0.1", 9),
        )


def test_tcp_hook_is_not_upstream_default(localhost_tcp_echo):
    stats = {}
    lock = threading.Lock()
    event = threading.Event()
    with localhost_tcp_echo() as (_host, port):
        thread = make_attack_thread(
            "TCP",
            target_key=f"127.0.0.1:{port}",
            stats_dict=stats,
            stats_lock=lock,
            synevent=event,
            l4_target=("127.0.0.1", port),
        )
        upstream_default = thread.__class__._raw_send
        assert thread._raw_send.__func__ is not upstream_default


def test_hook_records_stats(localhost_tcp_echo):
    stats = {}
    lock = threading.Lock()
    event = threading.Event()
    event.set()
    with localhost_tcp_echo() as (_host, port):
        thread = make_attack_thread(
            "TCP",
            target_key=f"127.0.0.1:{port}",
            stats_dict=stats,
            stats_lock=lock,
            synevent=event,
            l4_target=("127.0.0.1", port),
        )
        sock = thread.open_connection()
        try:
            thread._raw_send(sock, b"ping")
        finally:
            sock.close()
    assert stats[f"127.0.0.1:{port}"] == [1, 4]


def test_get_thread_builds(localhost_http_server):
    stats = {}
    lock = threading.Lock()
    event = threading.Event()
    with localhost_http_server() as (_host, port):
        url = URL(f"http://127.0.0.1:{port}/")
        thread = make_attack_thread(
            "GET",
            target_key=url.host,
            stats_dict=stats,
            stats_lock=lock,
            synevent=event,
            url=url,
            host="127.0.0.1",
        )
        assert thread._method == "GET"
