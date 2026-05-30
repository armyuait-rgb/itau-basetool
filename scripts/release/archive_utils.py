from __future__ import annotations

from pathlib import Path


def latest_archive(dist_dir: Path) -> Path:
    candidates = list(dist_dir.glob("basetool-runner-*.tar.gz"))
    if not candidates:
        raise FileNotFoundError(f"no release archives found in {dist_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)
