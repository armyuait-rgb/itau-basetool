from __future__ import annotations

import errno
import json
import logging
import socket
import ssl
import sys
import threading
import time
from typing import Dict, List, Set, Tuple, TYPE_CHECKING

from ..redaction import mask_target_key

if TYPE_CHECKING:
    from .scheduler import TargetScheduler

logger = logging.getLogger("BaseTool")

ERROR_KIND_DNS = "dns"
ERROR_KIND_REFUSED = "refused_closed_port"
ERROR_KIND_TIMEOUT = "timeout_or_filtered"
ERROR_KIND_ROUTE = "route_unavailable"
ERROR_KIND_PROXY = "proxy_tunnel_failed"
ERROR_KIND_TLS_HTTP = "tls_or_http_error"
ERROR_KIND_UNKNOWN = "unknown"

HARD_COOLDOWN_KINDS = {
    ERROR_KIND_DNS,
    ERROR_KIND_REFUSED,
    ERROR_KIND_PROXY,
}


def classify_target_failure(exc: BaseException) -> Tuple[str, str]:
    name = type(exc).__name__
    if isinstance(exc, socket.gaierror):
        return ERROR_KIND_DNS, f"{name}: DNS resolution failed"
    if isinstance(exc, ConnectionRefusedError):
        return ERROR_KIND_REFUSED, f"{name}: host reachable but port refused"
    if isinstance(exc, socket.timeout):
        return ERROR_KIND_TIMEOUT, (
            f"{name}: TCP connect/send timed out; check VPN/proxy/firewall/routing"
        )
    if isinstance(exc, TimeoutError):
        return ERROR_KIND_TIMEOUT, (
            f"{name}: TCP connect/send timed out; check VPN/proxy/firewall/routing"
        )
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }:
        return ERROR_KIND_ROUTE, f"{name}: network route unavailable; check VPN/proxy/firewall"
    if name in ("ProxyConnectionError", "GeneralProxyError", "ProxyError"):
        return ERROR_KIND_PROXY, (
            f"{name}: proxy tunnel failed; refresh proxy list, try VPN, "
            "or wait for proxy cache to reload"
        )
    if isinstance(exc, ssl.SSLError):
        return ERROR_KIND_TLS_HTTP, f"{name}: TLS handshake failed"
    lowered = name.lower()
    if "ssl" in lowered or "certificate" in lowered:
        return ERROR_KIND_TLS_HTTP, f"{name}: TLS/HTTPS error"
    return ERROR_KIND_UNKNOWN, name


def format_target_failure(exc: BaseException) -> str:
    return classify_target_failure(exc)[1]


class TargetHealth:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, object]] = {}
        self._recovered_reported: Set[str] = set()

    def _blank_entry(self) -> Dict[str, object]:
        return {
            "attempts": 0,
            "successes": 0,
            "last_error": "",
            "last_error_kind": ERROR_KIND_UNKNOWN,
            "consecutive_failures": 0,
        }

    def _ensure_unlocked(self, key: str) -> None:
        if key not in self._entries:
            self._entries[key] = self._blank_entry()

    def ensure(self, key: str) -> None:
        with self._lock:
            self._ensure_unlocked(key)

    def record_attempt(self, key: str) -> None:
        with self._lock:
            self._ensure_unlocked(key)
            self._entries[key]["attempts"] = int(self._entries[key]["attempts"]) + 1

    def record_success(self, key: str) -> bool:
        with self._lock:
            self._ensure_unlocked(key)
            self._entries[key]["successes"] = int(self._entries[key]["successes"]) + 1
            self._entries[key]["consecutive_failures"] = 0
            self._entries[key]["last_error"] = ""
            self._entries[key]["last_error_kind"] = ERROR_KIND_UNKNOWN
            if int(self._entries[key]["successes"]) == 1 and key not in self._recovered_reported:
                self._recovered_reported.add(key)
                return True
            return False

    def record_failure(self, key: str, exc: BaseException) -> None:
        kind, message = classify_target_failure(exc)
        with self._lock:
            self._ensure_unlocked(key)
            self._entries[key]["last_error"] = message
            self._entries[key]["last_error_kind"] = kind
            self._entries[key]["consecutive_failures"] = (
                int(self._entries[key]["consecutive_failures"]) + 1
            )

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        with self._lock:
            return {key: dict(val) for key, val in self._entries.items()}


def health_watchdog_loop(
    stop_event: threading.Event,
    target_health: TargetHealth,
    target_keys: List[str],
    proxy_mode: str = "direct",
) -> None:
    last_warn: Dict[str, float] = {}
    last_aggregate_warn = 0.0
    while stop_event.is_set():
        snapshot = target_health.snapshot()
        now = time.time()
        total_attempts = 0
        total_successes = 0
        active_targets = 0
        for key in target_keys:
            entry = snapshot.get(key)
            if not entry:
                continue
            attempts = int(entry.get("attempts", 0))
            successes = int(entry.get("successes", 0))
            last_error = str(entry.get("last_error", "") or "")
            total_attempts += attempts
            total_successes += successes
            if attempts > 0:
                active_targets += 1
            if successes > 0:
                continue
            if attempts <= 0:
                continue
            if not last_error:
                continue
            interval = 5.0 if attempts < 20 else 30.0
            prev = last_warn.get(key, 0.0)
            if now - prev >= interval:
                message = (
                    f"target {mask_target_key(key)} unreachable: 0/{attempts} attempts "
                    f"(last: {last_error})"
                )
                logger.warning(message)
                print(message, file=sys.stderr, flush=True)
                last_warn[key] = now

        all_have_errors = active_targets > 0
        for key in target_keys:
            entry = snapshot.get(key)
            if not entry:
                continue
            attempts = int(entry.get("attempts", 0))
            if attempts <= 0:
                continue
            if not str(entry.get("last_error", "") or ""):
                all_have_errors = False
                break

        aggregate_interval = 10.0 if total_attempts < 50 else 30.0
        if (
            active_targets > 0
            and total_attempts > 0
            and total_successes == 0
            and all_have_errors
            and now - last_aggregate_warn >= aggregate_interval
        ):
            if proxy_mode == "direct":
                aggregate = (
                    "all targets unreachable; direct mode is active; "
                    "enable proxy or VPN, or check DNS/firewall"
                )
            else:
                aggregate = (
                    "all targets unreachable; proxy mode is active but no traffic is flowing; "
                    "proxy pool may be dead — refresh module, check providers, or try VPN"
                )
            logger.warning(aggregate)
            print(aggregate, file=sys.stderr, flush=True)
            last_aggregate_warn = now
        time.sleep(5)


def capacity_health_loop(
    stop_event: threading.Event,
    target_health: TargetHealth,
    target_keys: List[str],
    target_stats: dict,
    stats_lock: threading.Lock,
    scheduler: "TargetScheduler",
    worker_count: int,
    proxy_mode: str,
    interval_sec: float = 10.0,
) -> None:
    last_emit = 0.0
    while stop_event.is_set():
        now = time.time()
        if now - last_emit < interval_sec:
            time.sleep(1)
            continue
        last_emit = now

        snapshot = target_health.snapshot()
        with stats_lock:
            stats_snapshot = {key: val.copy() for key, val in target_stats.items()}

        pools = scheduler.summarize_pools(snapshot, now)
        useful_bytes = sum(int(val[1]) for val in stats_snapshot.values())
        wasted_attempts = 0
        for key in target_keys:
            entry = snapshot.get(key, {})
            attempts = int(entry.get("attempts", 0))
            successes = int(entry.get("successes", 0))
            wasted_attempts += max(0, attempts - successes)

        ready_count = scheduler.count_ready(snapshot, now)
        idle_workers = max(0, worker_count - min(worker_count, ready_count))
        diagnosis = scheduler.derive_diagnosis(
            pools,
            proxy_mode=proxy_mode,
            useful_bytes=useful_bytes,
            ready_count=ready_count,
        )

        payload = {
            "healthyTargets": pools["healthy"],
            "degradedTargets": pools["degraded"],
            "discoveryTargets": pools["discovery"],
            "closedTargets": pools["closed"],
            "cooldownTargets": pools["cooldown"],
            "activeWorkers": worker_count,
            "readyTargets": ready_count,
            "idleWorkers": idle_workers,
            "wastedAttempts": wasted_attempts,
            "usefulBytes": useful_bytes,
            "diagnosis": diagnosis,
            "proxyMode": proxy_mode,
        }
        line = f"BASETOOL_HEALTH {json.dumps(payload, separators=(',', ':'))}"
        logger.info(line)
        print(line, file=sys.stderr, flush=True)
        time.sleep(1)
