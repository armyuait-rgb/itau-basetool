from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.integration.helpers import (
    run_runner,
    shutdown_http_server,
    with_http_server,
)


def test_unreachable_target_shuts_down_cleanly():
    server, thread = with_http_server(8081)
    try:
        with tempfile.TemporaryDirectory(prefix="basetool-unreachable-") as tmp:
            runtime_dir = Path(tmp)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            config = {
                "settings": {"threads": 2, "rpc": 1, "proxy": 0},
                "useragents": ["Mozilla/5.0"],
                "referers": ["https://example.com/"],
                "targets": [
                    {"method": "GET", "target": "http://127.0.0.1:1/", "threads": 2}
                ],
            }
            (runtime_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
            (runtime_dir / "proxy.json").write_text("[]", encoding="utf-8")

            completed = run_runner(
                runtime_dir=runtime_dir,
                stdin_text="stop\nexit\n",
                timeout=20,
            )
            combined = completed.stdout + completed.stderr
            assert completed.returncode == 0, combined
            assert "PPS:" in combined or "unreachable" in combined.lower()
    finally:
        shutdown_http_server(server, thread)
