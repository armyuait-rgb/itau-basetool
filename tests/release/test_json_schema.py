from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jsonschema

from tests.integration.helpers import BASETOOL, REPO_ROOT, base_env, stage_runtime
SCHEMA_PATH = REPO_ROOT / "tests/fixtures/json-telemetry.schema.json"


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def _parse_json_lines(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        start = line.find("{")
        if start == -1:
            continue
        rows.append(json.loads(line[start:]))
    return rows


def test_json_telemetry_matches_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    server = HTTPServer(("127.0.0.1", 8081), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)

    try:
        with tempfile.TemporaryDirectory(prefix="basetool-json-schema-") as tmp:
            runtime_dir = Path(tmp)
            stage_runtime(runtime_dir)
            env = base_env(runtime_dir)
            env["BASETOOL_JSON"] = "1"
            env["PYTHONUNBUFFERED"] = "1"

            proc = subprocess.Popen(
                [sys.executable, str(BASETOOL)],
                cwd=str(REPO_ROOT),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdin is not None
            time.sleep(2)
            proc.stdin.write("stop\nexit\n")
            proc.stdin.flush()
            proc.stdin.close()
            output, _ = proc.communicate(timeout=20)

            assert proc.returncode == 0, output
            rows = _parse_json_lines(output)
            assert rows, "expected JSON telemetry lines"
            for row in rows:
                jsonschema.validate(instance=row, schema=schema)
    finally:
        server.shutdown()
        thread.join(timeout=2)
