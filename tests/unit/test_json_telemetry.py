from __future__ import annotations

import json

from freezegun import freeze_time

from modules.basetool.runner.monitor import _emit_json_tick


@freeze_time("2026-05-24 12:00:00")
def test_emit_json_tick_format(capsys):
    snapshot = {"127.0.0.1:8081": [100, 5000]}
    _emit_json_tick(100, 5000, snapshot)
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["pps"] == 100
    assert payload["bps"] == 5000
    assert payload["targets"]["127.0.0.1:8081"]["req"] == 100
    assert payload["ts"] == "2026-05-24T12:00:00Z"
