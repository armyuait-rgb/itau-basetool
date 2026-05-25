from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import jsonschema

from tests.integration.helpers import (
    REPO_ROOT,
    run_runner_with_shutdown,
    shutdown_http_server,
    stage_runtime,
    with_http_server,
)

SCHEMA_PATH = REPO_ROOT / "tests/fixtures/json-telemetry.schema.json"


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
    server, thread = with_http_server(8081)
    try:
        with tempfile.TemporaryDirectory(prefix="basetool-json-schema-") as tmp:
            runtime_dir = Path(tmp)
            stage_runtime(runtime_dir)
            returncode, output = run_runner_with_shutdown(
                runtime_dir=runtime_dir,
                extra_env={"BASETOOL_JSON": "1"},
                warmup_seconds=2,
            )
            assert returncode == 0, output
            rows = _parse_json_lines(output)
            assert rows, "expected JSON telemetry lines"
            for row in rows:
                jsonschema.validate(instance=row, schema=schema)
    finally:
        shutdown_http_server(server, thread)
