from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Dict, List, Set

logger = logging.getLogger("BaseTool")


class TargetHealth:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, object]] = {}
        self._recovered_reported: Set[str] = set()

    def _ensure_unlocked(self, key: str) -> None:
        if key not in self._entries:
            self._entries[key] = {"attempts": 0, "successes": 0, "last_error": ""}

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
            if int(self._entries[key]["successes"]) == 1 and key not in self._recovered_reported:
                self._recovered_reported.add(key)
                return True
            return False

    def record_failure(self, key: str, exc: BaseException) -> None:
        with self._lock:
            self._ensure_unlocked(key)
            self._entries[key]["last_error"] = type(exc).__name__

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        with self._lock:
            return {key: dict(val) for key, val in self._entries.items()}


def health_watchdog_loop(
    stop_event: threading.Event,
    target_health: TargetHealth,
    target_keys: List[str],
    stats_dict: dict,
    stats_lock: threading.Lock,
) -> None:
    last_warn: Dict[str, float] = {}
    started_at = time.time()
    warmup_seconds = 10.0

    while stop_event.is_set():
        health_snapshot = target_health.snapshot()
        with stats_lock:
            stats_snapshot = {key: val.copy() for key, val in stats_dict.items()}
        now = time.time()
        elapsed = now - started_at

        for key in target_keys:
            entry = health_snapshot.get(key)
            attempts = int(entry.get("attempts", 0)) if entry else 0
            successes = int(entry.get("successes", 0)) if entry else 0
            packets = stats_snapshot.get(key, [0, 0])[0]

            if successes > 0 or packets > 0:
                continue

            health_trigger = attempts > 0 and successes == 0
            stats_trigger = elapsed >= warmup_seconds and packets == 0
            if not health_trigger and not stats_trigger:
                continue

            interval = 5.0 if attempts < 20 else 30.0
            prev = last_warn.get(key, 0.0)
            if now - prev < interval:
                continue

            if health_trigger:
                message = f"target {key} unreachable: 0/{attempts} attempts (last: unknown)"
            else:
                message = f"target {key} unreachable: 0 packets in {int(elapsed)}s (last: unknown)"

            logger.warning(message)
            print(message, file=sys.stderr, flush=True)
            last_warn[key] = now

        time.sleep(5)
