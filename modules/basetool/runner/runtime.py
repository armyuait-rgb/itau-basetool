from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_runtime_dir() -> Path:
    override = os.environ.get("BASETOOL_RUNTIME_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parents[3]
