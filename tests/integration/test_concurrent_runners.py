from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.integration.helpers import (
    run_runner,
    shutdown_http_server,
    with_http_server,
)


def test_two_runners_with_isolated_runtime_dirs():
    server_a, thread_a = with_http_server(8081)
    server_b, thread_b = with_http_server(8084)
    try:
        with tempfile.TemporaryDirectory(prefix="basetool-concurrent-a-") as tmp_a, tempfile.TemporaryDirectory(
            prefix="basetool-concurrent-b-"
        ) as tmp_b:
            runtime_a = Path(tmp_a)
            runtime_b = Path(tmp_b)

            for runtime_dir, port in ((runtime_a, 8081), (runtime_b, 8084)):
                config = {
                    "settings": {"threads": 2, "rpc": 1, "proxy": 0},
                    "useragents": ["Mozilla/5.0"],
                    "referers": ["https://example.com/"],
                    "targets": [
                        {
                            "method": "GET",
                            "target": f"http://127.0.0.1:{port}/",
                            "threads": 2,
                        }
                    ],
                }
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
                (runtime_dir / "proxy.json").write_text("[]", encoding="utf-8")

            proc_a = run_runner(runtime_dir=runtime_a, stdin_text="stop\nexit\n", timeout=20)
            proc_b = run_runner(runtime_dir=runtime_b, stdin_text="stop\nexit\n", timeout=20)

            assert proc_a.returncode == 0, proc_a.stdout + proc_a.stderr
            assert proc_b.returncode == 0, proc_b.stdout + proc_b.stderr
    finally:
        shutdown_http_server(server_a, thread_a)
        shutdown_http_server(server_b, thread_b)
