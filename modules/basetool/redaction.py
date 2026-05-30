from __future__ import annotations

import re

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
