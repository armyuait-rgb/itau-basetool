"""AES-256-GCM helpers for BaseTool encrypted runtime configs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from crypto_baked import BAKED_KEY
except ImportError:
    BAKED_KEY = b""

NONCE_SIZE = 12
DEV_FALLBACK_KEY_HEX = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def resolve_key() -> bytes:
    if isinstance(BAKED_KEY, (bytes, bytearray)) and len(BAKED_KEY) == 32:
        return bytes(BAKED_KEY)

    env_key = os.environ.get("BASETOOL_AES_KEY", "").strip().lower()
    if env_key:
        key = bytes.fromhex(env_key)
        if len(key) != 32:
            raise ValueError("BASETOOL_AES_KEY must be 64 hex characters (32 bytes)")
        return key

    return bytes.fromhex(DEV_FALLBACK_KEY_HEX)


def decrypt_blob(blob: bytes, key: bytes | None = None) -> bytes:
    key = key or resolve_key()
    if len(blob) < NONCE_SIZE + 16:
        raise ValueError("encrypted payload is too short")
    nonce = blob[:NONCE_SIZE]
    ciphertext = blob[NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def encrypt_blob(plaintext: bytes, key: bytes | None = None) -> bytes:
    key = key or resolve_key()
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt_config(path: Path) -> dict | list:
    raw = decrypt_blob(path.read_bytes()).decode("utf-8")
    clean = re.sub(r",\s*([}\]])", r"\1", raw)
    return json.loads(clean)


def encrypt_json(value: dict | list, key: bytes | None = None) -> bytes:
    payload = json.dumps(value, indent=2).encode("utf-8")
    return encrypt_blob(payload, key)
