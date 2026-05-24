from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

IMPORT_PROBE = """
import signal
import sys

argv_before = list(sys.argv)
socket_calls = 0
import socket as socket_mod
_real_socket = socket_mod.socket

class CountingSocket(_real_socket):
    def __init__(self, *args, **kwargs):
        global socket_calls
        socket_calls += 1
        super().__init__(*args, **kwargs)

socket_mod.socket = CountingSocket

# urllib3 probes IPv6 support at import time; isolate start.py side effects.
import urllib3.util.connection  # noqa: F401
socket_calls = 0

import modules.basetool.upstream.mhddos.start as start  # noqa: F401

assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
assert sys.argv == argv_before, "import must not mutate sys.argv"
assert socket_calls == 0, f"expected no socket() calls during start import, got {socket_calls}"
print("OK")
"""


def test_import_has_no_side_effects(repo_root: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env["PYTHONUNBUFFERED"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        if "No module named" in combined or "ModuleNotFoundError" in combined:
            pytest.skip(f"missing upstream import dependencies: {combined.strip()}")
        pytest.fail(
            "import side-effect probe failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    assert "OK" in result.stdout
