from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASETOOL = REPO_ROOT / "basetool.py"
HELP_FIXTURE = REPO_ROOT / "tests/fixtures/basetool-help.txt"


def normalize_help(text: str) -> str:
    text = text.replace("\r\n", "\n")
    return re.sub(
        r"^(optional arguments|options):",
        "options:",
        text,
        flags=re.MULTILINE,
    )


def test_basetool_help_matches_golden():
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, str(BASETOOL), "--help"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    actual = normalize_help(completed.stdout)
    expected = normalize_help(HELP_FIXTURE.read_text(encoding="utf-8"))
    assert actual == expected

    combined = actual.lower()
    for needle in ("start", "stop", "exit", "basetool_json", "basetool_runtime_dir", "config.json", "proxy.json"):
        assert needle in combined
